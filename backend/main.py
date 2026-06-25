"""
Grocery POS SaaS Backend - FastAPI
Run: uvicorn main:app --reload --host 0.0.0.0 --port 8000
"""

from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Form, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime, timedelta
import sqlite3, hashlib, secrets, os, json, shutil, smtplib, jwt
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import urllib.request, urllib.parse

# ======================== CONFIG ========================
SECRET_KEY = os.getenv("SECRET_KEY", "pos_saas_secret_2024_change_in_prod")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30
DB_PATH = "pos_saas.db"
UPLOAD_DIR = "../uploads"
ADMIN_USERNAME = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASS", "Admin@POS2024")

# Email config (set env vars in production)
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")

# WhatsApp (using wa.me link - for actual API use Twilio/Wati)
WA_API_URL = os.getenv("WA_API_URL", "")

# Google Apps Script webhook for Sheets
GAS_WEBHOOK_URL = os.getenv("GAS_WEBHOOK_URL", "")

# ======================== APP INIT ========================
app = FastAPI(title="Grocery POS SaaS API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs(UPLOAD_DIR, exist_ok=True)
security = HTTPBearer(auto_error=False)

# ======================== DATABASE ========================
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    
    # Users / Subscriptions
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT,
        full_name TEXT,
        email TEXT,
        whatsapp TEXT,
        city TEXT,
        store_type TEXT,
        plan TEXT DEFAULT 'free',
        plan_amount INTEGER DEFAULT 0,
        upi_used TEXT,
        receipt_path TEXT,
        payment_status TEXT DEFAULT 'pending',
        is_active INTEGER DEFAULT 0,
        is_blocked INTEGER DEFAULT 0,
        trial_bills_used INTEGER DEFAULT 0,
        subscription_start TEXT,
        subscription_expiry TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        approved_at TEXT
    )""")
    
    # Admin sessions
    c.execute("""CREATE TABLE IF NOT EXISTS admin_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )""")
    
    # Products (per user)
    c.execute("""CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        barcode TEXT,
        name TEXT NOT NULL,
        price REAL NOT NULL,
        category TEXT DEFAULT 'General',
        tax_percent REAL DEFAULT 0,
        stock INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(user_id) REFERENCES users(id)
    )""")
    
    # Bills (per user)
    c.execute("""CREATE TABLE IF NOT EXISTS bills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        bill_number TEXT,
        cart_json TEXT,
        customer_name TEXT,
        customer_mobile TEXT,
        payment_mode TEXT DEFAULT 'Cash',
        subtotal REAL DEFAULT 0,
        tax_total REAL DEFAULT 0,
        discount REAL DEFAULT 0,
        discount_type TEXT DEFAULT 'flat',
        final_amount REAL DEFAULT 0,
        notes TEXT,
        status TEXT DEFAULT 'active',
        loyalty_points_used INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now')),
        completed_at TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )""")
    
    # Customers (per user)
    c.execute("""CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name TEXT,
        mobile TEXT,
        email TEXT,
        total_points INTEGER DEFAULT 0,
        used_points INTEGER DEFAULT 0,
        total_spend REAL DEFAULT 0,
        visit_count INTEGER DEFAULT 0,
        last_visit TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(user_id) REFERENCES users(id)
    )""")

    # User Settings (per user)
    c.execute("""CREATE TABLE IF NOT EXISTS user_settings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE NOT NULL,
        shop_name TEXT DEFAULT 'My Shop',
        address TEXT DEFAULT '',
        mobile TEXT DEFAULT '',
        upi_id TEXT DEFAULT '',
        gst_number TEXT DEFAULT '',
        footer TEXT DEFAULT 'Thank you for shopping!',
        tax_percent REAL DEFAULT 0,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )""")
    
    conn.commit()
    conn.close()

init_db()

# ======================== JWT HELPERS ========================
def create_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str):
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except:
        return None

def hash_password(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

def generate_password() -> str:
    return secrets.token_urlsafe(8)

# ======================== AUTH DEPENDENCY ========================
def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (payload.get("user_id"),)).fetchone()
    conn.close()
    
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if user["is_blocked"]:
        raise HTTPException(status_code=403, detail="Account blocked")
    if not user["is_active"] and user["plan"] != "free":
        raise HTTPException(status_code=403, detail="Account not active")
    
    # Check subscription expiry
    if user["subscription_expiry"] and user["plan"] != "free":
        expiry = datetime.fromisoformat(user["subscription_expiry"])
        if datetime.utcnow() > expiry:
            raise HTTPException(status_code=403, detail="Subscription expired")
    
    return dict(user)

def get_admin(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if not credentials:
        raise HTTPException(status_code=401, detail="Admin not authenticated")
    payload = decode_token(credentials.credentials)
    if not payload or payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access only")
    return payload

# ======================== PYDANTIC MODELS ========================
class PaymentRequest(BaseModel):
    full_name: str
    email: str
    whatsapp: str
    username: str
    city: str
    store_type: str
    upi_used: str
    plan: str  # 'monthly' or 'yearly'

class LoginRequest(BaseModel):
    username: str
    password: str

class ProductCreate(BaseModel):
    barcode: Optional[str] = ""
    name: str
    price: float
    category: Optional[str] = "General"
    tax_percent: Optional[float] = 0
    stock: Optional[int] = 0

class BillCreate(BaseModel):
    bill_number: str
    cart_json: str
    customer_name: Optional[str] = ""
    customer_mobile: Optional[str] = ""
    payment_mode: Optional[str] = "Cash"
    subtotal: float
    tax_total: float
    discount: Optional[float] = 0
    discount_type: Optional[str] = "flat"
    final_amount: float
    notes: Optional[str] = ""
    loyalty_points_used: Optional[int] = 0

class CustomerUpdate(BaseModel):
    name: str
    mobile: str
    email: Optional[str] = ""

class SettingsUpdate(BaseModel):
    shop_name: Optional[str] = "My Shop"
    address: Optional[str] = ""
    mobile: Optional[str] = ""
    upi_id: Optional[str] = ""
    gst_number: Optional[str] = ""
    footer: Optional[str] = "Thank you!"
    tax_percent: Optional[float] = 0

class AdminApprove(BaseModel):
    user_id: int
    username: str
    password: str
    plan_days: int  # 30 or 365

class AdminBlock(BaseModel):
    user_id: int
    block: bool

class ExtendSubscription(BaseModel):
    user_id: int
    days: int

# ======================== UTILS ========================
def send_email(to_email: str, subject: str, body: str):
    if not SMTP_USER or not SMTP_PASS:
        print(f"[EMAIL SKIPPED] To: {to_email} | {subject}")
        return False
    try:
        msg = MIMEMultipart()
        msg['From'] = SMTP_USER
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'html'))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"Email error: {e}")
        return False

def send_whatsapp(mobile: str, message: str):
    # Opens WA link - for production use Twilio/Wati API
    print(f"[WHATSAPP] To: {mobile} | {message[:50]}...")
    if WA_API_URL:
        try:
            data = json.dumps({"phone": mobile, "message": message}).encode()
            req = urllib.request.Request(WA_API_URL, data=data, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5)
        except Exception as e:
            print(f"WhatsApp error: {e}")
    return True

def push_to_google_sheets(data: dict):
    if not GAS_WEBHOOK_URL:
        print(f"[SHEETS SKIPPED] {data.get('username')}")
        return
    try:
        payload = json.dumps(data).encode()
        req = urllib.request.Request(GAS_WEBHOOK_URL, data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"Sheets error: {e}")

def row_to_dict(row):
    if row is None:
        return None
    return dict(row)

# ======================== AUTH ROUTES ========================
@app.post("/api/auth/register")
async def register(
    full_name: str = Form(...),
    email: str = Form(...),
    whatsapp: str = Form(...),
    username: str = Form(...),
    city: str = Form(...),
    store_type: str = Form(...),
    upi_used: str = Form(...),
    plan: str = Form(...),
    receipt: UploadFile = File(...)
):
    conn = get_db()
    existing = conn.execute("SELECT id FROM users WHERE username=? OR email=?", (username, email)).fetchone()
    if existing:
        conn.close()
        raise HTTPException(status_code=400, detail="Username or email already exists")
    
    # Save receipt
    ext = receipt.filename.split(".")[-1] if "." in receipt.filename else "jpg"
    receipt_path = f"{UPLOAD_DIR}/receipt_{username}_{int(datetime.utcnow().timestamp())}.{ext}"
    with open(receipt_path, "wb") as f:
        shutil.copyfileobj(receipt.file, f)
    
    plan_amount = 299 if plan == "monthly" else 3200
    
    conn.execute("""INSERT INTO users 
        (username, full_name, email, whatsapp, city, store_type, upi_used, receipt_path, plan, plan_amount, payment_status, is_active)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,0)""",
        (username, full_name, email, whatsapp, city, store_type, upi_used, receipt_path, plan, plan_amount, "pending"))
    user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    
    # Default settings
    conn.execute("INSERT OR IGNORE INTO user_settings (user_id, shop_name) VALUES (?,?)", (user_id, full_name + "'s Shop"))
    conn.commit()
    conn.close()
    
    # Push to Google Sheets
    push_to_google_sheets({
        "action": "new_registration",
        "username": username, "full_name": full_name,
        "email": email, "whatsapp": whatsapp, "plan": plan,
        "plan_amount": plan_amount, "city": city, "store_type": store_type,
        "upi_used": upi_used, "timestamp": datetime.utcnow().isoformat()
    })
    
    return {"success": True, "message": "Registration submitted. Admin will verify and send login credentials within 24 hours."}

@app.post("/api/auth/login")
async def login(req: LoginRequest):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE username=?", (req.username,)).fetchone()
    conn.close()
    
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    # Check free trial user (no password set yet, skip hash check)
    if user["plan"] == "free" and not user["password_hash"]:
        pass
    elif not user["password_hash"] or hash_password(req.password) != user["password_hash"]:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    if user["is_blocked"]:
        raise HTTPException(status_code=403, detail="Account blocked. Contact support.")
    
    token = create_token({"user_id": user["id"], "username": user["username"]})
    
    return {
        "token": token,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "full_name": user["full_name"],
            "plan": user["plan"],
            "is_active": user["is_active"],
            "trial_bills_used": user["trial_bills_used"],
            "subscription_expiry": user["subscription_expiry"],
            "payment_status": user["payment_status"]
        }
    }

@app.post("/api/auth/free-trial")
async def free_trial(req: LoginRequest):
    """Create or login free trial account"""
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE username=?", (req.username,)).fetchone()
    
    if not user:
        # Create free trial account
        conn.execute("""INSERT INTO users (username, full_name, plan, is_active, trial_bills_used) 
                       VALUES (?,?,?,?,?)""", (req.username, req.username, 'free', 1, 0))
        user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT OR IGNORE INTO user_settings (user_id, shop_name) VALUES (?,?)", (user_id, req.username + "'s Shop"))
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    
    conn.close()
    
    token = create_token({"user_id": user["id"], "username": user["username"]})
    return {
        "token": token,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "full_name": user["full_name"] or user["username"],
            "plan": user["plan"],
            "is_active": user["is_active"],
            "trial_bills_used": user["trial_bills_used"],
            "subscription_expiry": None,
            "payment_status": "free"
        }
    }

@app.get("/api/auth/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    return {
        "id": current_user["id"],
        "username": current_user["username"],
        "full_name": current_user["full_name"],
        "plan": current_user["plan"],
        "is_active": current_user["is_active"],
        "trial_bills_used": current_user["trial_bills_used"],
        "subscription_expiry": current_user["subscription_expiry"],
        "payment_status": current_user["payment_status"]
    }

# ======================== PRODUCTS ========================
@app.get("/api/products")
async def get_products(current_user: dict = Depends(get_current_user)):
    conn = get_db()
    rows = conn.execute("SELECT * FROM products WHERE user_id=? ORDER BY name", (current_user["id"],)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/products")
async def add_product(p: ProductCreate, current_user: dict = Depends(get_current_user)):
    conn = get_db()
    conn.execute("""INSERT INTO products (user_id, barcode, name, price, category, tax_percent, stock)
                    VALUES (?,?,?,?,?,?,?)""",
                 (current_user["id"], p.barcode, p.name, p.price, p.category, p.tax_percent, p.stock))
    pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    return {"success": True, "id": pid}

@app.put("/api/products/{pid}")
async def update_product(pid: int, p: ProductCreate, current_user: dict = Depends(get_current_user)):
    conn = get_db()
    conn.execute("""UPDATE products SET barcode=?, name=?, price=?, category=?, tax_percent=?, stock=?
                    WHERE id=? AND user_id=?""",
                 (p.barcode, p.name, p.price, p.category, p.tax_percent, p.stock, pid, current_user["id"]))
    conn.commit()
    conn.close()
    return {"success": True}

@app.delete("/api/products/{pid}")
async def delete_product(pid: int, current_user: dict = Depends(get_current_user)):
    conn = get_db()
    conn.execute("DELETE FROM products WHERE id=? AND user_id=?", (pid, current_user["id"]))
    conn.commit()
    conn.close()
    return {"success": True}

# ======================== BILLS ========================
@app.post("/api/bills")
async def save_bill(bill: BillCreate, current_user: dict = Depends(get_current_user)):
    # Free trial check (SERVER SIDE - cannot be bypassed)
    if current_user["plan"] == "free":
        if current_user["trial_bills_used"] >= 3:
            raise HTTPException(status_code=402, detail="FREE_TRIAL_EXPIRED")
    
    conn = get_db()
    conn.execute("""INSERT INTO bills 
        (user_id, bill_number, cart_json, customer_name, customer_mobile, payment_mode,
         subtotal, tax_total, discount, discount_type, final_amount, notes, status, loyalty_points_used, completed_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (current_user["id"], bill.bill_number, bill.cart_json, bill.customer_name,
         bill.customer_mobile, bill.payment_mode, bill.subtotal, bill.tax_total,
         bill.discount, bill.discount_type, bill.final_amount, bill.notes, "completed",
         bill.loyalty_points_used, datetime.utcnow().isoformat()))
    
    # Increment trial counter
    if current_user["plan"] == "free":
        conn.execute("UPDATE users SET trial_bills_used=trial_bills_used+1 WHERE id=?", (current_user["id"],))
    
    # Update customer loyalty points
    if bill.customer_mobile:
        points_earned = int(bill.final_amount // 100)
        cust = conn.execute("SELECT id FROM customers WHERE user_id=? AND mobile=?",
                           (current_user["id"], bill.customer_mobile)).fetchone()
        if cust:
            conn.execute("""UPDATE customers SET 
                total_points=total_points+?, total_spend=total_spend+?,
                visit_count=visit_count+1, last_visit=?
                WHERE id=?""", (points_earned, bill.final_amount, datetime.utcnow().isoformat(), cust["id"]))
        else:
            cust_name = bill.customer_name or "Customer"
            conn.execute("""INSERT INTO customers (user_id, name, mobile, total_points, total_spend, visit_count, last_visit)
                           VALUES (?,?,?,?,?,1,?)""",
                        (current_user["id"], cust_name, bill.customer_mobile, points_earned,
                         bill.final_amount, datetime.utcnow().isoformat()))
    
    conn.commit()
    conn.close()
    
    return {"success": True, "message": "Bill saved"}

@app.get("/api/bills")
async def get_bills(current_user: dict = Depends(get_current_user)):
    conn = get_db()
    rows = conn.execute("""SELECT id, bill_number, customer_name, customer_mobile, 
                           final_amount, payment_mode, status, completed_at 
                           FROM bills WHERE user_id=? ORDER BY completed_at DESC LIMIT 100""",
                       (current_user["id"],)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/api/bills/trial-status")
async def trial_status(current_user: dict = Depends(get_current_user)):
    return {
        "plan": current_user["plan"],
        "trial_bills_used": current_user["trial_bills_used"],
        "trial_remaining": max(0, 3 - current_user["trial_bills_used"]) if current_user["plan"] == "free" else 999,
        "is_locked": current_user["plan"] == "free" and current_user["trial_bills_used"] >= 3
    }

# ======================== CUSTOMERS ========================
@app.get("/api/customers")
async def get_customers(current_user: dict = Depends(get_current_user)):
    conn = get_db()
    rows = conn.execute("SELECT * FROM customers WHERE user_id=? ORDER BY name", (current_user["id"],)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/api/customers/lookup/{mobile}")
async def lookup_customer(mobile: str, current_user: dict = Depends(get_current_user)):
    conn = get_db()
    row = conn.execute("SELECT * FROM customers WHERE user_id=? AND mobile=?",
                      (current_user["id"], mobile)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Customer not found")
    return dict(row)

@app.post("/api/customers/redeem")
async def redeem_points(mobile: str, points: int, current_user: dict = Depends(get_current_user)):
    conn = get_db()
    cust = conn.execute("SELECT * FROM customers WHERE user_id=? AND mobile=?",
                       (current_user["id"], mobile)).fetchone()
    if not cust:
        conn.close()
        raise HTTPException(status_code=404, detail="Customer not found")
    
    balance = cust["total_points"] - cust["used_points"]
    if points > balance:
        conn.close()
        raise HTTPException(status_code=400, detail="Insufficient points")
    
    discount = (points // 100) * 50  # 100 points = ₹50
    conn.execute("UPDATE customers SET used_points=used_points+? WHERE id=?", (points, cust["id"]))
    conn.commit()
    conn.close()
    return {"discount": discount, "points_redeemed": points}

# ======================== SETTINGS ========================
@app.get("/api/settings")
async def get_settings(current_user: dict = Depends(get_current_user)):
    conn = get_db()
    row = conn.execute("SELECT * FROM user_settings WHERE user_id=?", (current_user["id"],)).fetchone()
    if not row:
        conn.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (current_user["id"],))
        conn.commit()
        row = conn.execute("SELECT * FROM user_settings WHERE user_id=?", (current_user["id"],)).fetchone()
    conn.close()
    return dict(row)

@app.put("/api/settings")
async def update_settings(s: SettingsUpdate, current_user: dict = Depends(get_current_user)):
    conn = get_db()
    conn.execute("""INSERT OR REPLACE INTO user_settings 
        (user_id, shop_name, address, mobile, upi_id, gst_number, footer, tax_percent)
        VALUES (?,?,?,?,?,?,?,?)""",
        (current_user["id"], s.shop_name, s.address, s.mobile, s.upi_id, s.gst_number, s.footer, s.tax_percent))
    conn.commit()
    conn.close()
    return {"success": True}

# ======================== WHATSAPP BILL ========================
@app.post("/api/bills/whatsapp")
async def send_whatsapp_bill(
    mobile: str,
    bill_number: str,
    shop_name: str,
    amount: float,
    current_user: dict = Depends(get_current_user)
):
    message = f"""🛒 *{shop_name}*
    
✅ Bill #{bill_number} - ₹{amount:.2f}

Thank you for shopping with us! 🙏

🎁 *Special Offer:* Visit again within 7 days and get *₹30 OFF* on purchase above ₹500!

Pay via UPI or visit us again soon."""
    
    send_whatsapp(mobile, message)
    # Return WA link (fallback)
    wa_link = f"https://wa.me/91{mobile}?text={urllib.parse.quote(message)}"
    return {"success": True, "wa_link": wa_link}

# ======================== ADMIN LOGIN ========================
@app.post("/api/admin/login")
async def admin_login(req: LoginRequest):
    if req.username != ADMIN_USERNAME or req.password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid admin credentials")
    
    token = create_token({"role": "admin", "username": "admin"}, expires_delta=timedelta(days=1))
    return {"token": token}

# ======================== ADMIN ROUTES ========================
@app.get("/api/admin/users")
async def admin_get_users(admin = Depends(get_admin)):
    conn = get_db()
    rows = conn.execute("""SELECT id, username, full_name, email, whatsapp, city, store_type,
                           plan, plan_amount, upi_used, receipt_path, payment_status, 
                           is_active, is_blocked, trial_bills_used, subscription_start,
                           subscription_expiry, created_at, approved_at
                           FROM users ORDER BY created_at DESC""").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/admin/approve")
async def admin_approve(req: AdminApprove, admin = Depends(get_admin)):
    password = req.password or generate_password()
    start = datetime.utcnow()
    expiry = start + timedelta(days=req.plan_days)
    plan = "yearly" if req.plan_days >= 300 else "monthly"
    
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (req.user_id,)).fetchone()
    if not user:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")
    
    conn.execute("""UPDATE users SET 
        username=?, password_hash=?, is_active=1, payment_status='approved',
        subscription_start=?, subscription_expiry=?, approved_at=?, plan=?
        WHERE id=?""",
        (req.username, hash_password(password), start.isoformat(), expiry.isoformat(),
         start.isoformat(), plan, req.user_id))
    conn.commit()
    
    # Send credentials
    user = conn.execute("SELECT * FROM users WHERE id=?", (req.user_id,)).fetchone()
    conn.close()
    
    login_url = os.getenv("APP_URL", "https://yourpos.com") + "/app.html"
    
    email_body = f"""
    <h2>🎉 Your POS Account is Ready!</h2>
    <p>Dear {user['full_name']},</p>
    <p>Your subscription has been approved.</p>
    <table>
    <tr><td><b>Username:</b></td><td>{req.username}</td></tr>
    <tr><td><b>Password:</b></td><td>{password}</td></tr>
    <tr><td><b>Plan:</b></td><td>{plan.title()} ({req.plan_days} days)</td></tr>
    <tr><td><b>Expiry:</b></td><td>{expiry.strftime('%d %B %Y')}</td></tr>
    <tr><td><b>Login:</b></td><td><a href="{login_url}">{login_url}</a></td></tr>
    </table>
    <p>Keep these credentials safe!</p>
    """
    
    wa_msg = f"""✅ *POS Account Approved!*

👤 Username: {req.username}
🔑 Password: {password}
📅 Expiry: {expiry.strftime('%d %b %Y')}
🔗 Login: {login_url}

Keep this safe! 🙏"""
    
    send_email(user["email"], "🎉 Your POS Login Credentials", email_body)
    send_whatsapp(user["whatsapp"], wa_msg)
    
    # Push to Google Sheets
    push_to_google_sheets({
        "action": "approved",
        "username": req.username,
        "plan": plan,
        "expiry": expiry.isoformat(),
        "approved_at": start.isoformat()
    })
    
    return {"success": True, "message": f"User approved. Credentials sent to {user['email']} and WhatsApp {user['whatsapp']}"}

@app.post("/api/admin/block")
async def admin_block(req: AdminBlock, admin = Depends(get_admin)):
    conn = get_db()
    conn.execute("UPDATE users SET is_blocked=? WHERE id=?", (1 if req.block else 0, req.user_id))
    conn.commit()
    conn.close()
    return {"success": True}

@app.post("/api/admin/extend")
async def admin_extend(req: ExtendSubscription, admin = Depends(get_admin)):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (req.user_id,)).fetchone()
    if not user:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")
    
    current_expiry = user["subscription_expiry"]
    if current_expiry:
        base = datetime.fromisoformat(current_expiry)
        if base < datetime.utcnow():
            base = datetime.utcnow()
    else:
        base = datetime.utcnow()
    
    new_expiry = base + timedelta(days=req.days)
    conn.execute("UPDATE users SET subscription_expiry=? WHERE id=?", (new_expiry.isoformat(), req.user_id))
    conn.commit()
    conn.close()
    return {"success": True, "new_expiry": new_expiry.isoformat()}

@app.get("/api/admin/receipt/{user_id}")
async def get_receipt(user_id: int, admin = Depends(get_admin)):
    conn = get_db()
    user = conn.execute("SELECT receipt_path FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    if not user or not user["receipt_path"]:
        raise HTTPException(status_code=404, detail="Receipt not found")
    if not os.path.exists(user["receipt_path"]):
        raise HTTPException(status_code=404, detail="Receipt file not found")
    return FileResponse(user["receipt_path"])

@app.get("/api/admin/stats")
async def admin_stats(admin = Depends(get_admin)):
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    active = conn.execute("SELECT COUNT(*) as c FROM users WHERE is_active=1").fetchone()["c"]
    pending = conn.execute("SELECT COUNT(*) as c FROM users WHERE payment_status='pending'").fetchone()["c"]
    blocked = conn.execute("SELECT COUNT(*) as c FROM users WHERE is_blocked=1").fetchone()["c"]
    conn.close()
    return {"total": total, "active": active, "pending_verification": pending, "blocked": blocked}

# ======================== SERVE FRONTEND ========================
@app.get("/")
async def root():
    return FileResponse("../frontend/index.html")

@app.get("/app")
async def serve_app():
    return FileResponse("../frontend/app.html")

@app.get("/admin")
async def serve_admin():
    return FileResponse("../admin/admin.html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
