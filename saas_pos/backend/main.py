"""
Grocery POS SaaS Backend - FastAPI
Run: uvicorn main:app --reload --host 0.0.0.0 --port 8000
"""

from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Form, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime, timedelta
import sqlite3, hashlib, secrets, os, json, shutil, smtplib, jwt
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import urllib.request, urllib.parse

# ── Cloud Auth (Google Sheets) — replaces SQLite for all login/account data ──
# See google_auth.py / apps_script_api.py / sheet_manager.py for the 3-layer
# implementation. main.py only ever calls into google_auth.py.
import google_auth
from sheet_manager import SheetManagerError

# ======================== CONFIG ========================
SECRET_KEY = os.getenv("SECRET_KEY", "pos_saas_secret_2024_change_in_prod")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30

# ── Render Persistent Disk ──────────────────────────────────────────────────
# IMPORTANT: As of this update, user LOGIN/ACCOUNT data (username, password,
# plan, expiry, status, device limit) no longer lives here — it lives in the
# Google Sheet (see google_auth.py). This disk is now used ONLY for:
#   1) pos_saas.db — POS business data (products, bills, customers, settings)
#      which is per-shop operational data, not account/auth data.
#   2) uploads/ — payment receipt screenshots.
# If this disk were ever lost, user ACCOUNTS would be completely unaffected
# (they live in Google Sheets); only POS billing history/products would need
# to be re-entered, and receipt images would need to be re-uploaded.
_DATA_DIR = os.getenv("RENDER_DATA_DIR", "/var/data")
if not os.path.isdir(_DATA_DIR):
    _DATA_DIR = os.path.dirname(os.path.abspath(__file__))  # local fallback
DB_PATH = os.path.join(_DATA_DIR, "pos_saas.db")
UPLOAD_DIR = os.path.join(_DATA_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
print(f"[DB] Using path: {DB_PATH}")
ADMIN_USERNAME = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASS", "Admin@POS2024")
DEFAULT_ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "dkapoore@gmail.com")

# Email config (set env vars in production)
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")

# WhatsApp (using wa.me link - for actual API use Twilio/Wati)
WA_API_URL = os.getenv("WA_API_URL", "")

# Google Apps Script webhook for Sheets — now doubles as the AUTH database endpoint.
# GAS_API_SECRET must match the API_SECRET Script Property set in GoogleAppsScript.js,
# otherwise the Apps Script Web App will reject all auth read/write calls.
GAS_WEBHOOK_URL = os.getenv("GAS_WEBHOOK_URL", "")
GAS_API_SECRET = os.getenv("GAS_API_SECRET", "")
if not GAS_WEBHOOK_URL:
    print("[WARN] GAS_WEBHOOK_URL not set — login/signup will fail until configured.")

# ======================== APP INIT ========================
app = FastAPI(title="Grocery POS SaaS API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Registering this via @app.exception_handler runs it INSIDE
    # CORSMiddleware in the stack, so the response still gets proper
    # Access-Control-Allow-Origin headers. Without this, an unhandled crash
    # (like the datetime tz-aware/naive bug that caused the "Failed to fetch"
    # mystery) bubbles all the way out to Starlette's ServerErrorMiddleware,
    # which sits OUTSIDE CORSMiddleware — its fallback 500 response has no
    # CORS headers, so the browser blocks it and shows a generic network
    # error instead of the real 500, hiding the actual bug from both the
    # user and the browser's Network tab.
    print(f"[UNHANDLED EXCEPTION] {request.method} {request.url.path}: {type(exc).__name__}: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Server error — kripya thodi der baad try karein. Agar issue rahe toh support se contact karein."},
    )

security = HTTPBearer(auto_error=False)

# ======================== DATABASE ========================
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()

    # ── NOTE ──────────────────────────────────────────────────────────────
    # There is intentionally NO "users" table here anymore. All account /
    # login data (username, password_hash, plan, expiry, status, device
    # limit) now lives in the Google Sheet — see google_auth.py.
    #
    # The tables below still have a `user_id INTEGER` column. This is the
    # SAME numeric user_id that's stored in the Google Sheet's `user_id`
    # column (assigned at signup time by GoogleAppsScript.js). SQLite no
    # longer owns or generates this ID — it's just referencing an identity
    # that lives in the Sheet. This keeps all POS business data (products,
    # bills, customers, settings) working exactly as before, while account
    # data itself is fully decoupled from the local database / Render disk.

    # User Sessions — device tracking (kept local; high write volume, not
    # "credentials", and not required to be in the Sheet by the architecture spec)
    c.execute("""CREATE TABLE IF NOT EXISTS user_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        token_hash TEXT NOT NULL,
        device_id TEXT NOT NULL,
        device_label TEXT DEFAULT '',
        ip_address TEXT DEFAULT '',
        user_agent TEXT DEFAULT '',
        last_seen TEXT DEFAULT (datetime('now')),
        created_at TEXT DEFAULT (datetime('now')),
        is_active INTEGER DEFAULT 1
    )""")

    # ── Phase 3: OTP-based credential management ──────────────────────
    # OTP codes are stored as a SHA-256 hash (not the raw code) — same
    # principle as password hashing, so a DB read alone can't leak a
    # usable code. Codes are short-lived (10 min) and single-use.
    c.execute("""CREATE TABLE IF NOT EXISTS otp_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        purpose TEXT NOT NULL,
        target TEXT NOT NULL,
        code_hash TEXT NOT NULL,
        payload TEXT DEFAULT '',
        expires_at TEXT NOT NULL,
        used INTEGER DEFAULT 0,
        attempts INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    )""")

    # Separate lightweight log of just request timestamps, for resend
    # cooldown + rate limiting (kept even after the OTP itself is used/expired).
    c.execute("""CREATE TABLE IF NOT EXISTS otp_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        target TEXT NOT NULL,
        purpose TEXT NOT NULL,
        requested_at TEXT DEFAULT (datetime('now'))
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        actor TEXT NOT NULL,
        action TEXT NOT NULL,
        details TEXT DEFAULT '',
        ip_address TEXT DEFAULT '',
        user_agent TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now'))
    )""")

    # Admin credentials used to be fixed env vars (ADMIN_USER/ADMIN_PASS) —
    # can't be changed by the running app. This table makes them editable
    # from Settings, with the env vars now only used to SEED the first row.
    c.execute("""CREATE TABLE IF NOT EXISTS admin_account (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        username TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        email TEXT NOT NULL,
        token_version INTEGER DEFAULT 0,
        updated_at TEXT DEFAULT (datetime('now'))
    )""")
    conn.commit()
    existing_admin = c.execute("SELECT id FROM admin_account WHERE id=1").fetchone()
    if not existing_admin:
        c.execute(
            "INSERT INTO admin_account (id, username, password_hash, email, token_version) VALUES (1, ?, ?, ?, 0)",
            (ADMIN_USERNAME, google_auth.hash_password(ADMIN_PASSWORD), DEFAULT_ADMIN_EMAIL)
        )
        conn.commit()

    # Admin sessions
    c.execute("""CREATE TABLE IF NOT EXISTS admin_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )""")
    
    # Products (per user) — user_id references the Sheet's user_id, not a local table
    c.execute("""CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        barcode TEXT,
        name TEXT NOT NULL,
        price REAL NOT NULL,
        discount_percent REAL DEFAULT 0,
        category TEXT DEFAULT 'General',
        tax_percent REAL DEFAULT 0,
        stock INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    )""")
    
    # Bills (per user) — user_id references the Sheet's user_id, not a local table
    c.execute("""CREATE TABLE IF NOT EXISTS bills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        bill_number TEXT,
        cart_json TEXT,
        customer_name TEXT,
        customer_mobile TEXT,
        payment_mode TEXT DEFAULT 'Cash',
        table_no TEXT DEFAULT '',
        waiter TEXT DEFAULT '',
        subtotal REAL DEFAULT 0,
        tax_total REAL DEFAULT 0,
        discount REAL DEFAULT 0,
        discount_type TEXT DEFAULT 'flat',
        additional_charge REAL DEFAULT 0,
        additional_charge_type TEXT DEFAULT 'flat',
        additional_charge_label TEXT DEFAULT '',
        final_amount REAL DEFAULT 0,
        notes TEXT,
        status TEXT DEFAULT 'active',
        loyalty_points_used INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now')),
        completed_at TEXT
    )""")
    
    # Customers (per user) — user_id references the Sheet's user_id, not a local table
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
        created_at TEXT DEFAULT (datetime('now'))
    )""")

    # User Settings (per user) — user_id references the Sheet's user_id, not a local table.
    # NOTE: settings_password_hash column kept here for backward compatibility but is no
    # longer the source of truth — it now mirrors the Sheet's settings_password_hash column
    # (see /api/settings endpoints below, which read/write through google_auth).
    c.execute("""CREATE TABLE IF NOT EXISTS user_settings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE NOT NULL,
        shop_name TEXT DEFAULT 'My Shop',
        address TEXT DEFAULT '',
        mobile TEXT DEFAULT '',
        upi_id TEXT DEFAULT '',
        gst_number TEXT DEFAULT '',
        fssai_no TEXT DEFAULT '',
        extra_header_lines TEXT DEFAULT '',
        footer TEXT DEFAULT 'Thank you for shopping!',
        tax_percent REAL DEFAULT 0,
        cgst_percent REAL DEFAULT 0,
        sgst_percent REAL DEFAULT 0,
        bill_format TEXT DEFAULT 'standard',
        gas_url TEXT DEFAULT '',
        sheet_id TEXT DEFAULT '',
        enable_amount_words INTEGER DEFAULT 0,
        bill_col_qty INTEGER DEFAULT 1,
        bill_col_rate INTEGER DEFAULT 1,
        bill_col_discount INTEGER DEFAULT 1,
        bill_col_taxable INTEGER DEFAULT 1,
        bill_col_cgst INTEGER DEFAULT 1,
        bill_col_sgst INTEGER DEFAULT 1,
        bill_col_net INTEGER DEFAULT 1,
        waiter_list TEXT DEFAULT '',
        table_list TEXT DEFAULT ''
    )""")

    # App-wide config (admin managed) — e.g. landing page hero carousel images
    c.execute("""CREATE TABLE IF NOT EXISTS app_config (
        key TEXT PRIMARY KEY,
        value TEXT DEFAULT ''
    )""")

    conn.commit()
    conn.close()

init_db()

# DB Migration — add new columns to existing databases
def migrate_db():
    conn = get_db()
    c = conn.cursor()
    migrations = [
        # Older deployments may still have a legacy `users` table from before
        # this migration — it's no longer read or written by any route, but
        # we leave it in place (not dropped) so no historical data is lost.
        # New deployments simply never create it (see init_db above).
        "ALTER TABLE user_settings ADD COLUMN gas_url TEXT DEFAULT ''",
        "ALTER TABLE user_settings ADD COLUMN sheet_id TEXT DEFAULT ''",
        "ALTER TABLE user_settings ADD COLUMN enable_amount_words INTEGER DEFAULT 0",
        "ALTER TABLE user_settings ADD COLUMN fssai_no TEXT DEFAULT ''",
        "ALTER TABLE user_settings ADD COLUMN extra_header_lines TEXT DEFAULT ''",
        "ALTER TABLE user_settings ADD COLUMN cgst_percent REAL DEFAULT 0",
        "ALTER TABLE user_settings ADD COLUMN sgst_percent REAL DEFAULT 0",
        "ALTER TABLE user_settings ADD COLUMN bill_format TEXT DEFAULT 'standard'",
        "ALTER TABLE user_settings ADD COLUMN bill_col_qty INTEGER DEFAULT 1",
        "ALTER TABLE user_settings ADD COLUMN bill_col_rate INTEGER DEFAULT 1",
        "ALTER TABLE user_settings ADD COLUMN bill_col_discount INTEGER DEFAULT 1",
        "ALTER TABLE user_settings ADD COLUMN bill_col_taxable INTEGER DEFAULT 1",
        "ALTER TABLE user_settings ADD COLUMN bill_col_cgst INTEGER DEFAULT 1",
        "ALTER TABLE user_settings ADD COLUMN bill_col_sgst INTEGER DEFAULT 1",
        "ALTER TABLE user_settings ADD COLUMN bill_col_net INTEGER DEFAULT 1",
        "ALTER TABLE user_settings ADD COLUMN waiter_list TEXT DEFAULT ''",
        "ALTER TABLE user_settings ADD COLUMN table_list TEXT DEFAULT ''",
        "ALTER TABLE bills ADD COLUMN additional_charge REAL DEFAULT 0",
        "ALTER TABLE bills ADD COLUMN additional_charge_type TEXT DEFAULT 'flat'",
        "ALTER TABLE bills ADD COLUMN additional_charge_label TEXT DEFAULT ''",
        "ALTER TABLE bills ADD COLUMN table_no TEXT DEFAULT ''",
        "ALTER TABLE bills ADD COLUMN waiter TEXT DEFAULT ''",
        "ALTER TABLE products ADD COLUMN discount_percent REAL DEFAULT 0",
        "ALTER TABLE products ADD COLUMN unit TEXT DEFAULT 'piece'",
        "ALTER TABLE products ADD COLUMN hsn TEXT DEFAULT ''",
    ]
    for sql in migrations:
        try:
            c.execute(sql)
            conn.commit()
        except Exception:
            pass  # Column already exists
    conn.close()

migrate_db()

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

# NOTE: hash_password / generate_password used to live here using unsalted
# SHA-256. Both now live in google_auth.py using bcrypt (proper salted
# password hashing). Local aliases kept so any remaining call sites in this
# file keep working without changes.
hash_password = google_auth.hash_password
generate_password = google_auth.generate_password

# ======================== AUTH DEPENDENCY ========================
def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Resolves the JWT to a user. The token stores `username` (not a local
    DB id) — the actual account record (plan, expiry, status, device_limit)
    is fetched fresh from the Google Sheet on every request via google_auth.
    This means admin changes (block/extend/approve) take effect immediately,
    without needing the user to get a new token."""
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(credentials.credentials)
    if not payload or not payload.get("username"):
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        user = google_auth.get_user(payload["username"])
    except SheetManagerError as e:
        raise HTTPException(status_code=503, detail=f"Cloud auth unavailable: {e}")

    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if user["is_blocked"]:
        raise HTTPException(status_code=403, detail="Account blocked")
    if not user["is_active"] and user["subscription_plan"] != "free":
        raise HTTPException(status_code=403, detail="Account not active")

    # Check subscription expiry
    if user["expiry_date"] and user["subscription_plan"] != "free":
        try:
            expiry = datetime.fromisoformat(user["expiry_date"])
            # See google_auth.authenticate() for why this normalization is needed —
            # some expiry_date values are timezone-aware, which crashes a naive
            # datetime.utcnow() comparison with TypeError (not ValueError).
            if expiry.tzinfo is not None:
                expiry = expiry.replace(tzinfo=None)
            if datetime.utcnow() > expiry:
                raise HTTPException(status_code=403, detail="Subscription expired")
        except (ValueError, TypeError):
            pass

    # Session validity check — if admin force-logged-out this token.
    # Sessions remain in SQLite (device/session tracking, not credentials).
    conn = get_db()
    token_hash = hashlib.sha256(credentials.credentials.encode()).hexdigest()
    session = conn.execute(
        "SELECT id FROM user_sessions WHERE token_hash=? AND is_active=1",
        (token_hash,)
    ).fetchone()

    has_any_session = conn.execute(
        "SELECT COUNT(*) as c FROM user_sessions WHERE user_id=?", (user["user_id"],)
    ).fetchone()["c"]

    if user["subscription_plan"] != "free" and has_any_session > 0 and not session:
        conn.close()
        raise HTTPException(status_code=401, detail="Session expired or logged out remotely. Please login again.")

    if session:
        conn.execute("UPDATE user_sessions SET last_seen=datetime(\'now\') WHERE id=?", (session["id"],))
        conn.commit()
    conn.close()

    # Shape a dict that's compatible with the rest of this file, which was
    # written expecting SQLite's `users` row shape (current_user["id"],
    # current_user["plan"], etc). We map Sheet field names to the old names
    # here so downstream endpoints below don't all need rewriting.
    return {
        "id": user["user_id"],
        "username": user["username"],
        "full_name": user["full_name"],
        "email": user["email"],
        "whatsapp": user["whatsapp"],
        "city": user["city"],
        "store_type": user["store_type"],
        "plan": user["subscription_plan"],
        "plan_amount": user["plan_amount"],
        "upi_used": user["upi_used"],
        "receipt_path": user["receipt_path"],
        "payment_status": user["payment_status"],
        "is_active": 1 if user["is_active"] else 0,
        "is_blocked": 1 if user["is_blocked"] else 0,
        "trial_bills_used": user["trial_bills_used"],
        "subscription_expiry": user["expiry_date"],
        "max_devices": user["device_limit"],
        "allowed_ips": user["allowed_ips"],
    }

def get_admin(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if not credentials:
        raise HTTPException(status_code=401, detail="Admin not authenticated")
    payload = decode_token(credentials.credentials)
    if not payload or payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access only")
    # "tv" (token_version) lets a password reset instantly invalidate every
    # previously issued admin token — bumped in admin_account whenever the
    # admin explicitly asks to sign out of all sessions.
    conn = get_db()
    row = conn.execute("SELECT token_version FROM admin_account WHERE id=1").fetchone()
    conn.close()
    if row and payload.get("tv", 0) != row["token_version"]:
        raise HTTPException(status_code=401, detail="Session ended — kripya dobara login karein")
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
    device_id: Optional[str] = ""
    device_label: Optional[str] = ""

class ProductCreate(BaseModel):
    barcode: Optional[str] = ""
    name: str
    price: float
    discount_percent: Optional[float] = 0
    category: Optional[str] = "General"
    tax_percent: Optional[float] = 0
    unit: Optional[str] = "piece"
    hsn: Optional[str] = ""
    stock: Optional[int] = 0

class BillCreate(BaseModel):
    bill_number: str
    cart_json: str
    customer_name: Optional[str] = ""
    customer_mobile: Optional[str] = ""
    payment_mode: Optional[str] = "Cash"
    table_no: Optional[str] = ""
    waiter: Optional[str] = ""
    subtotal: float
    tax_total: float
    discount: Optional[float] = 0
    discount_type: Optional[str] = "flat"
    additional_charge: Optional[float] = 0
    additional_charge_type: Optional[str] = "flat"
    additional_charge_label: Optional[str] = ""
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
    fssai_no: Optional[str] = ""
    extra_header_lines: Optional[str] = ""
    footer: Optional[str] = "Thank you!"
    tax_percent: Optional[float] = 0
    cgst_percent: Optional[float] = 0
    sgst_percent: Optional[float] = 0
    bill_format: Optional[str] = "standard"
    enable_amount_words: Optional[int] = 0
    bill_col_qty: Optional[int] = 1
    bill_col_rate: Optional[int] = 1
    bill_col_discount: Optional[int] = 1
    bill_col_taxable: Optional[int] = 1
    bill_col_cgst: Optional[int] = 1
    bill_col_sgst: Optional[int] = 1
    bill_col_net: Optional[int] = 1
    waiter_list: Optional[str] = ""
    table_list: Optional[str] = ""

class AdminApprove(BaseModel):
    user_id: int
    username: str
    password: str
    plan_days: int  # 30 or 365
    gas_url: str = ""  # User ka apna Google Apps Script URL
    sheet_id: str = ""  # Optional Sheet ID

class AdminBlock(BaseModel):
    user_id: int
    block: bool

class AdminDeleteUser(BaseModel):
    user_id: int

class ExtendSubscription(BaseModel):
    user_id: int
    days: int

class UpdateDeviceLimit(BaseModel):
    user_id: int
    max_devices: int
    allowed_ips: Optional[str] = ""

class ForceLogoutSession(BaseModel):
    session_id: int

class ForceLogoutUser(BaseModel):
    user_id: int

class SettingsPasswordVerify(BaseModel):
    password: str

class AdminGenSettingsPassword(BaseModel):
    user_id: int

class AdminResetPassword(BaseModel):
    user_id: int
    custom_password: Optional[str] = None  # if omitted, a random password is generated
    force_change: bool = True  # if True, user must set their own password on next login

class HeroImagesUpdate(BaseModel):
    images: List[str]  # list of image URLs, 9:16 ratio recommended

class TrialWatermarkUpdate(BaseModel):
    enabled: bool  # whether free-trial bills show the "FREE TRIAL" watermark

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

# ======================== OTP & AUDIT LOG (Phase 3: Credential Management) ========================
OTP_TTL_MINUTES = 10
OTP_RESEND_COOLDOWN_SECONDS = 60
OTP_MAX_REQUESTS_PER_HOUR = 5
OTP_MAX_VERIFY_ATTEMPTS = 5

def _hash_otp(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()

def generate_otp() -> str:
    return f"{secrets.randbelow(1000000):06d}"

def check_otp_rate_limit(target: str, purpose: str) -> Optional[str]:
    """Returns an error message if the caller should be blocked, else None."""
    conn = get_db()
    last = conn.execute(
        "SELECT requested_at FROM otp_requests WHERE target=? AND purpose=? ORDER BY id DESC LIMIT 1",
        (target, purpose)
    ).fetchone()
    if last:
        last_dt = datetime.fromisoformat(last["requested_at"])
        if (datetime.utcnow() - last_dt).total_seconds() < OTP_RESEND_COOLDOWN_SECONDS:
            conn.close()
            return f"Kripya {OTP_RESEND_COOLDOWN_SECONDS} second wait karke dobara try karein"
    hour_ago = (datetime.utcnow() - timedelta(hours=1)).isoformat()
    count = conn.execute(
        "SELECT COUNT(*) c FROM otp_requests WHERE target=? AND purpose=? AND requested_at > ?",
        (target, purpose, hour_ago)
    ).fetchone()["c"]
    conn.close()
    if count >= OTP_MAX_REQUESTS_PER_HOUR:
        return "Bahut zyada OTP requests ho gayi hain — 1 ghante baad try karein"
    return None

def create_otp(target: str, purpose: str, payload: dict = None) -> str:
    """Generates, stores (hashed), and returns a fresh OTP. Caller is
    responsible for emailing it and for rate-limit checking beforehand."""
    code = generate_otp()
    expires_at = (datetime.utcnow() + timedelta(minutes=OTP_TTL_MINUTES)).isoformat()
    conn = get_db()
    conn.execute(
        "INSERT INTO otp_codes (purpose, target, code_hash, payload, expires_at) VALUES (?,?,?,?,?)",
        (purpose, target, _hash_otp(code), json.dumps(payload or {}), expires_at)
    )
    conn.execute("INSERT INTO otp_requests (target, purpose) VALUES (?,?)", (target, purpose))
    conn.commit()
    conn.close()
    return code

def verify_otp(target: str, purpose: str, code: str) -> dict:
    """Returns {"success": True, "payload": {...}} or {"success": False, "message": "..."}."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM otp_codes WHERE target=? AND purpose=? AND used=0 ORDER BY id DESC LIMIT 1",
        (target, purpose)
    ).fetchone()
    if not row:
        conn.close()
        return {"success": False, "message": "OTP nahi mila — pehle naya OTP request karein"}
    if datetime.fromisoformat(row["expires_at"]) < datetime.utcnow():
        conn.close()
        return {"success": False, "message": "OTP expire ho chuka hai — naya OTP request karein"}
    if row["attempts"] >= OTP_MAX_VERIFY_ATTEMPTS:
        conn.close()
        return {"success": False, "message": "Bahut zyada galat attempts — naya OTP request karein"}
    if _hash_otp(code) != row["code_hash"]:
        conn.execute("UPDATE otp_codes SET attempts=attempts+1 WHERE id=?", (row["id"],))
        conn.commit()
        conn.close()
        return {"success": False, "message": "Galat OTP"}
    conn.execute("UPDATE otp_codes SET used=1 WHERE id=?", (row["id"],))
    conn.commit()
    conn.close()
    return {"success": True, "payload": json.loads(row["payload"] or "{}")}

def get_client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""

def log_audit(actor: str, action: str, request: Request, details: str = ""):
    conn = get_db()
    conn.execute(
        "INSERT INTO audit_log (actor, action, details, ip_address, user_agent) VALUES (?,?,?,?,?)",
        (actor, action, details, get_client_ip(request), request.headers.get("user-agent", ""))
    )
    conn.commit()
    conn.close()

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
    # NOTE: signup() already checks for an existing username internally
    # before creating the row, so there's no need to duplicate that check
    # here — it was an extra, unnecessary Sheet round-trip on every signup.

    # Save receipt — this is a file, not credential data, so it's fine to keep
    # on the Render disk (referenced by path from the Sheet's receipt_path column).
    ext = receipt.filename.split(".")[-1] if "." in receipt.filename else "jpg"
    receipt_path = f"{UPLOAD_DIR}/receipt_{username}_{int(datetime.utcnow().timestamp())}.{ext}"
    with open(receipt_path, "wb") as f:
        shutil.copyfileobj(receipt.file, f)

    plan_amount = 299 if plan == "monthly" else 3200

    res = await run_in_threadpool(
        google_auth.signup,
        username=username, password=None, full_name=full_name, email=email,
        whatsapp=whatsapp, city=city, store_type=store_type,
        subscription_plan=plan, plan_amount=plan_amount,
        upi_used=upi_used, receipt_path=receipt_path,
        account_status="Inactive", payment_status="pending",
    )
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("message", "Registration failed"))

    # Default settings — still local (per-shop billing config, not account data)
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO user_settings (user_id, shop_name) VALUES (?,?)",
                 (res["user_id"], full_name + "'s Shop"))
    conn.commit()
    conn.close()

    # Push to Google Sheets (legacy "Registrations" log tab — kept for the
    # existing admin notification workflow / WhatsApp+Email approval flow)
    await run_in_threadpool(push_to_google_sheets, {
        "action": "new_registration",
        "username": username, "full_name": full_name,
        "email": email, "whatsapp": whatsapp, "plan": plan,
        "plan_amount": plan_amount, "city": city, "store_type": store_type,
        "upi_used": upi_used, "timestamp": datetime.utcnow().isoformat() + "Z"
    })

    return {"success": True, "message": "Registration submitted. Admin will verify and send login credentials within 24 hours."}

@app.post("/api/auth/login")
async def login(req: LoginRequest, request: Request):
    # google_auth.authenticate() makes a blocking network call to Apps Script
    # (up to ~36s worst-case with retries). Running it inline here would
    # freeze this whole async server — every other user's request — for that
    # entire duration, since there's only one worker. run_in_threadpool moves
    # it off the event loop so other requests keep flowing while this waits.
    auth_res = await run_in_threadpool(google_auth.authenticate, req.username, req.password)
    if not auth_res.get("success"):
        msg = auth_res.get("message", "Invalid credentials")
        status_code = 403 if "blocked" in msg.lower() or "expired" in msg.lower() or "not activated" in msg.lower() else 401
        raise HTTPException(status_code=status_code, detail=msg)

    user = auth_res["user"]  # normalized dict from google_auth — see _normalize_user

    # ── IP Whitelist Check ──────────────────────────────────────────────────
    client_ip = request.headers.get("X-Forwarded-For", request.client.host or "").split(",")[0].strip()
    allowed_ips_raw = user["allowed_ips"] or ""
    if allowed_ips_raw.strip():
        allowed_list = [ip.strip() for ip in allowed_ips_raw.split(",") if ip.strip()]
        if allowed_list and client_ip not in allowed_list:
            raise HTTPException(status_code=403, detail=f"Access denied from IP {client_ip}. Contact admin.")

    # ── Device Session Limit Check ──────────────────────────────────────────
    conn = get_db()
    max_devices = user["device_limit"] or 1
    device_id = (req.device_id or "").strip()
    device_label = (req.device_label or "unknown device").strip()
    user_agent = request.headers.get("User-Agent", "")[:200]

    if device_id and user["subscription_plan"] != "free":
        # Count distinct active devices (excluding this device_id if already registered)
        active_sessions = conn.execute(
            """SELECT DISTINCT device_id FROM user_sessions
               WHERE user_id=? AND is_active=1 AND device_id != ?""",
            (user["user_id"], device_id)
        ).fetchall()
        active_device_count = len(active_sessions)

        # Check if this device already has a session
        existing = conn.execute(
            "SELECT id FROM user_sessions WHERE user_id=? AND device_id=? AND is_active=1",
            (user["user_id"], device_id)
        ).fetchone()

        if not existing and active_device_count >= max_devices:
            conn.close()
            raise HTTPException(
                status_code=403,
                detail=f"Device limit reached ({max_devices} device{'s' if max_devices>1 else ''} allowed). "
                       f"Please logout from another device or contact admin."
            )

    # ── Create Token ────────────────────────────────────────────────────────
    # Token now stores `username` (the Sheet's stable identity) instead of a
    # local SQLite row id — see get_current_user() above.
    token = create_token({"username": user["username"]})
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    if device_id and user["subscription_plan"] != "free":
        # Deactivate old sessions for this device (re-login on same device)
        conn.execute(
            "UPDATE user_sessions SET is_active=0 WHERE user_id=? AND device_id=?",
            (user["user_id"], device_id)
        )
        # Register new session
        conn.execute(
            """INSERT INTO user_sessions
               (user_id, token_hash, device_id, device_label, ip_address, user_agent, last_seen, created_at, is_active)
               VALUES (?,?,?,?,?,?,datetime('now'),datetime('now'),1)""",
            (user["user_id"], token_hash, device_id, device_label, client_ip, user_agent)
        )
        conn.commit()

    conn.close()

    return {
        "token": token,
        "user": {
            "id": user["user_id"],
            "username": user["username"],
            "full_name": user["full_name"],
            "plan": user["subscription_plan"],
            "is_active": user["is_active"],
            "trial_bills_used": user["trial_bills_used"],
            "subscription_expiry": user["expiry_date"],
            "payment_status": user["payment_status"],
            "max_devices": max_devices,
        }
    }

@app.post("/api/auth/free-trial")
async def free_trial(req: LoginRequest):
    """Create or login free trial account — backed by the Google Sheet."""
    user = await run_in_threadpool(google_auth.get_user, req.username)

    if not user:
        # Create free trial account directly with Active status (no approval needed)
        res = await run_in_threadpool(
            google_auth.signup,
            username=req.username, password=None, full_name=req.username,
            subscription_plan="free", account_status="Active", payment_status="free",
        )
        if not res.get("success"):
            raise HTTPException(status_code=400, detail=res.get("message", "Could not start free trial"))
        conn = get_db()
        conn.execute("INSERT OR IGNORE INTO user_settings (user_id, shop_name) VALUES (?,?)",
                     (res["user_id"], req.username + "'s Shop"))
        conn.commit()
        conn.close()
        user = await run_in_threadpool(google_auth.get_user, req.username)

    token = create_token({"username": user["username"]})
    return {
        "token": token,
        "user": {
            "id": user["user_id"],
            "username": user["username"],
            "full_name": user["full_name"] or user["username"],
            "plan": user["subscription_plan"],
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
        "payment_status": current_user["payment_status"],
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
    conn.execute("""INSERT INTO products (user_id, barcode, name, price, discount_percent, category, tax_percent, unit, hsn, stock)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                 (current_user["id"], p.barcode, p.name, p.price, p.discount_percent or 0,
                  p.category, p.tax_percent, p.unit or 'piece', p.hsn or '', p.stock))
    pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    return {"success": True, "id": pid}

@app.put("/api/products/{pid}")
async def update_product(pid: int, p: ProductCreate, current_user: dict = Depends(get_current_user)):
    conn = get_db()
    conn.execute("""UPDATE products SET barcode=?, name=?, price=?, discount_percent=?, category=?, tax_percent=?, unit=?, hsn=?, stock=?
                    WHERE id=? AND user_id=?""",
                 (p.barcode, p.name, p.price, p.discount_percent or 0,
                  p.category, p.tax_percent, p.unit or 'piece', p.hsn or '', p.stock, pid, current_user["id"]))
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
         table_no, waiter,
         subtotal, tax_total, discount, discount_type,
         additional_charge, additional_charge_type, additional_charge_label,
         final_amount, notes, status, loyalty_points_used, completed_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (current_user["id"], bill.bill_number, bill.cart_json, bill.customer_name,
         bill.customer_mobile, bill.payment_mode,
         bill.table_no or '', bill.waiter or '',
         bill.subtotal, bill.tax_total,
         bill.discount, bill.discount_type,
         bill.additional_charge, bill.additional_charge_type, bill.additional_charge_label,
         bill.final_amount, bill.notes, "completed",
         bill.loyalty_points_used, datetime.utcnow().isoformat()))
    
    # Increment trial counter — this lives in the Sheet now, not SQLite
    if current_user["plan"] == "free":
        try:
            await run_in_threadpool(
                google_auth.set_trial_bills_used,
                current_user["username"], current_user["trial_bills_used"] + 1
            )
        except SheetManagerError:
            pass  # don't fail bill creation if the Sheet write hiccups
    
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

@app.get("/api/bills/history")
async def get_bills_history(current_user: dict = Depends(get_current_user)):
    """Alias for get_bills — used by frontend showHistory()"""
    conn = get_db()
    rows = conn.execute("""SELECT id, bill_number, customer_name, customer_mobile, 
                           final_amount, payment_mode, status, completed_at as completedAt,
                           created_at as date
                           FROM bills WHERE user_id=? ORDER BY completed_at DESC LIMIT 200""",
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

@app.delete("/api/bills/{bill_id}")
async def delete_bill(bill_id: int, current_user: dict = Depends(get_current_user)):
    conn = get_db()
    bill = conn.execute("SELECT id FROM bills WHERE id=? AND user_id=?",
                        (bill_id, current_user["id"])).fetchone()
    if not bill:
        conn.close()
        raise HTTPException(status_code=404, detail="Bill not found")
    conn.execute("DELETE FROM bills WHERE id=? AND user_id=?", (bill_id, current_user["id"]))
    conn.commit()
    conn.close()
    return {"success": True, "message": "Bill deleted"}

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
    data = dict(row)
    # Settings-tab lock status now lives on the Sheet (account-management data),
    # not in this local table.
    try:
        sheet_user = await run_in_threadpool(google_auth.get_user, current_user["username"])
        data["settings_locked"] = bool(sheet_user and sheet_user.get("settings_password_hash"))
    except SheetManagerError:
        data["settings_locked"] = False
    return data

@app.put("/api/settings")
async def update_settings(s: SettingsUpdate, current_user: dict = Depends(get_current_user)):
    conn = get_db()
    conn.execute("""INSERT OR REPLACE INTO user_settings 
        (user_id, shop_name, address, mobile, upi_id, gst_number, fssai_no, extra_header_lines,
         footer, tax_percent, cgst_percent, sgst_percent, bill_format, enable_amount_words,
         bill_col_qty, bill_col_rate, bill_col_discount, bill_col_taxable,
         bill_col_cgst, bill_col_sgst, bill_col_net,
         waiter_list, table_list)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (current_user["id"], s.shop_name, s.address, s.mobile, s.upi_id, s.gst_number,
         s.fssai_no, s.extra_header_lines, s.footer, s.tax_percent,
         s.cgst_percent, s.sgst_percent, s.bill_format, s.enable_amount_words,
         s.bill_col_qty, s.bill_col_rate, s.bill_col_discount, s.bill_col_taxable,
         s.bill_col_cgst, s.bill_col_sgst, s.bill_col_net,
         s.waiter_list, s.table_list))
    conn.commit()
    conn.close()
    return {"success": True}

@app.post("/api/settings/verify-password")
async def verify_settings_password(req: SettingsPasswordVerify, current_user: dict = Depends(get_current_user)):
    """Unlock the settings tab — checks the per-user settings lock password
    (set only by admin), stored as settings_password_hash on the Sheet."""
    try:
        sheet_user = await run_in_threadpool(google_auth.get_user, current_user["username"])
    except SheetManagerError as e:
        raise HTTPException(status_code=503, detail=f"Cloud auth unavailable: {e}")
    stored_hash = (sheet_user or {}).get("settings_password_hash", "")
    if not stored_hash:
        # No lock set — settings are open to anyone logged into this account
        return {"success": True, "locked": False}
    if not await run_in_threadpool(google_auth.verify_password, req.password, stored_hash):
        raise HTTPException(status_code=401, detail="Galat password")
    return {"success": True, "locked": True}

@app.get("/api/settings/lock-status")
async def settings_lock_status(current_user: dict = Depends(get_current_user)):
    try:
        sheet_user = await run_in_threadpool(google_auth.get_user, current_user["username"])
    except SheetManagerError:
        return {"locked": False}
    return {"locked": bool(sheet_user and sheet_user.get("settings_password_hash"))}

class ChangePassword(BaseModel):
    current_password: str
    new_password: str

@app.post("/api/auth/change-password")
async def change_password(req: ChangePassword, request: Request, current_user: dict = Depends(get_current_user)):
    """User-initiated password change — used both for the mandatory
    'set a new password' step after an admin reset (must_change_password),
    and for voluntary password changes anytime."""
    if len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="Naya password kam se kam 6 characters ka hona chahiye")
    try:
        sheet_user = await run_in_threadpool(google_auth.get_user, current_user["username"])
    except SheetManagerError as e:
        raise HTTPException(status_code=503, detail=f"Cloud auth unavailable: {e}")
    if not sheet_user:
        raise HTTPException(status_code=404, detail="User not found")
    if not await run_in_threadpool(google_auth.verify_password, req.current_password, sheet_user["password_hash"]):
        raise HTTPException(status_code=401, detail="Current password galat hai")

    new_hash = await run_in_threadpool(hash_password, req.new_password)
    ok = await run_in_threadpool(google_auth.reset_password, sheet_user["user_id"], new_hash, False)
    if not ok:
        raise HTTPException(status_code=500, detail="Password update failed — dobara try karein")
    log_audit(current_user["username"], "user_password_changed", request)
    return {"success": True, "message": "Password successfully changed"}

# ── USER FORGOT PASSWORD (OTP via registered email, no admin needed) ──
class ForgotPasswordRequest(BaseModel):
    username: str

class ForgotPasswordReset(BaseModel):
    username: str
    otp: str
    new_password: str
    logout_other_devices: bool = False

@app.post("/api/auth/forgot-password/request-otp")
async def user_forgot_password_request_otp(req: ForgotPasswordRequest, request: Request):
    try:
        user = await run_in_threadpool(google_auth.get_user, req.username)
    except SheetManagerError as e:
        raise HTTPException(status_code=503, detail=f"Cloud auth unavailable: {e}")
    # Same generic response whether or not the username exists — avoids
    # leaking which usernames are registered.
    generic_msg = {"success": True, "message": "Agar ye username registered hai, toh OTP uske email par bhej diya gaya hai"}
    if not user or not user.get("email"):
        return generic_msg
    limit_err = check_otp_rate_limit(user["email"], "user_forgot_password")
    if limit_err:
        raise HTTPException(status_code=429, detail=limit_err)
    otp = create_otp(user["email"], "user_forgot_password", {"username": user["username"]})
    await run_in_threadpool(send_email, user["email"], "🔑 GroceryPOS Password Reset OTP", f"""Hello {user['full_name'] or user['username']},

Aapka password reset OTP hai: {otp}

Ye OTP {OTP_TTL_MINUTES} minute me expire ho jayega. Agar aapne ye request nahi ki, is email ko ignore karein.

Team GroceryPOS""")
    log_audit(req.username, "user_forgot_password_otp_requested", request)
    return generic_msg

@app.post("/api/auth/forgot-password/reset")
async def user_forgot_password_reset(req: ForgotPasswordReset, request: Request):
    if len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="Naya password kam se kam 6 characters ka hona chahiye")
    try:
        user = await run_in_threadpool(google_auth.get_user, req.username)
    except SheetManagerError as e:
        raise HTTPException(status_code=503, detail=f"Cloud auth unavailable: {e}")
    if not user or not user.get("email"):
        raise HTTPException(status_code=404, detail="User not found")

    result = verify_otp(user["email"], "user_forgot_password", req.otp)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])

    new_hash = await run_in_threadpool(hash_password, req.new_password)
    ok = await run_in_threadpool(google_auth.reset_password, user["user_id"], new_hash, False)
    if not ok:
        raise HTTPException(status_code=500, detail="Password update failed — dobara try karein")

    if req.logout_other_devices:
        conn = get_db()
        conn.execute("UPDATE user_sessions SET is_active=0 WHERE user_id=?", (user["user_id"],))
        conn.commit()
        conn.close()

    log_audit(req.username, "user_password_reset_via_otp", request,
              f"logout_other_devices={req.logout_other_devices}")
    return {"success": True, "message": "Password successfully reset — ab naye password se login karein"}

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
    
    await run_in_threadpool(send_whatsapp, mobile, message)
    # Return WA link (fallback)
    wa_link = f"https://wa.me/91{mobile}?text={urllib.parse.quote(message)}"
    return {"success": True, "wa_link": wa_link}

# ======================== ADMIN LOGIN ========================
@app.post("/api/admin/login")
async def admin_login(req: LoginRequest):
    conn = get_db()
    admin_row = conn.execute("SELECT * FROM admin_account WHERE id=1").fetchone()
    conn.close()
    valid = admin_row and req.username == admin_row["username"] and \
        await run_in_threadpool(google_auth.verify_password, req.password, admin_row["password_hash"])
    if not valid:
        raise HTTPException(status_code=401, detail="Invalid admin credentials")

    token = create_token(
        {"role": "admin", "username": admin_row["username"], "tv": admin_row["token_version"]},
        expires_delta=timedelta(days=1)
    )
    return {"token": token}

# ── ADMIN CREDENTIALS (Settings — change username/email/password, OTP-verified) ──
@app.get("/api/admin/credentials")
async def get_admin_credentials(admin = Depends(get_admin)):
    conn = get_db()
    row = conn.execute("SELECT username, email, updated_at FROM admin_account WHERE id=1").fetchone()
    conn.close()
    return dict(row)

class AdminCredentialOtpRequest(BaseModel):
    current_password: str
    field: str  # 'username' | 'email' | 'password'
    new_value: str

@app.post("/api/admin/credentials/request-otp")
async def admin_credentials_request_otp(req: AdminCredentialOtpRequest, request: Request, admin = Depends(get_admin)):
    if req.field not in ("username", "email", "password"):
        raise HTTPException(status_code=400, detail="Invalid field")
    if req.field == "password" and len(req.new_value) < 6:
        raise HTTPException(status_code=400, detail="Naya password kam se kam 6 characters ka hona chahiye")
    if req.field == "username" and len(req.new_value.strip()) < 3:
        raise HTTPException(status_code=400, detail="Username kam se kam 3 characters ka hona chahiye")

    conn = get_db()
    row = conn.execute("SELECT * FROM admin_account WHERE id=1").fetchone()
    conn.close()
    if not row or not await run_in_threadpool(google_auth.verify_password, req.current_password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Current password galat hai")

    # OTP always goes to the CURRENTLY registered email — even when the
    # change being verified IS the email address, so an attacker who
    # only guessed/knows the new email can't hijack the account.
    limit_err = check_otp_rate_limit(row["email"], "admin_credential_change")
    if limit_err:
        raise HTTPException(status_code=429, detail=limit_err)
    otp = create_otp(row["email"], "admin_credential_change", {"field": req.field, "new_value": req.new_value})
    await run_in_threadpool(send_email, row["email"], "🔐 GroceryPOS Admin — Confirm Credential Change", f"""Admin,

Aapne apna {req.field} change karne ki request ki hai.
Verification OTP: {otp}

Ye OTP {OTP_TTL_MINUTES} minute me expire ho jayega. Agar aapne ye request nahi ki, turant apna password change karein.

Team GroceryPOS""")
    log_audit("admin", "admin_credential_otp_requested", request, f"field={req.field}")
    return {"success": True, "message": f"OTP bhej diya gaya hai registered email par"}

class AdminCredentialOtpVerify(BaseModel):
    otp: str

@app.post("/api/admin/credentials/verify-otp")
async def admin_credentials_verify_otp(req: AdminCredentialOtpVerify, request: Request, admin = Depends(get_admin)):
    conn = get_db()
    row = conn.execute("SELECT * FROM admin_account WHERE id=1").fetchone()
    email = row["email"]
    conn.close()

    result = verify_otp(email, "admin_credential_change", req.otp)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])

    field = result["payload"]["field"]
    new_value = result["payload"]["new_value"]
    conn = get_db()
    if field == "password":
        new_hash = await run_in_threadpool(hash_password, new_value)
        conn.execute("UPDATE admin_account SET password_hash=?, updated_at=datetime('now') WHERE id=1",
                     (new_hash,))
    elif field == "username":
        conn.execute("UPDATE admin_account SET username=?, updated_at=datetime('now') WHERE id=1", (new_value.strip(),))
    elif field == "email":
        conn.execute("UPDATE admin_account SET email=?, updated_at=datetime('now') WHERE id=1", (new_value.strip(),))
    conn.commit()
    conn.close()
    log_audit("admin", f"admin_{field}_changed", request)
    return {"success": True, "message": f"{field.title()} successfully updated"}

# ── ADMIN FORGOT PASSWORD (Admin Login page, OTP via registered email) ──
class AdminForgotPasswordRequest(BaseModel):
    username: str

@app.post("/api/admin/forgot-password/request-otp")
async def admin_forgot_password_request_otp(req: AdminForgotPasswordRequest, request: Request):
    conn = get_db()
    row = conn.execute("SELECT * FROM admin_account WHERE id=1").fetchone()
    conn.close()
    generic_msg = {"success": True, "message": "Agar username sahi hai, OTP registered email par bhej diya gaya hai"}
    if not row or req.username != row["username"]:
        return generic_msg  # don't reveal whether the username matched
    limit_err = check_otp_rate_limit(row["email"], "admin_forgot_password")
    if limit_err:
        raise HTTPException(status_code=429, detail=limit_err)
    otp = create_otp(row["email"], "admin_forgot_password", {})
    await run_in_threadpool(send_email, row["email"], "🔐 GroceryPOS Admin — Password Reset OTP", f"""Admin,

Aapka admin password reset OTP hai: {otp}

Ye OTP {OTP_TTL_MINUTES} minute me expire ho jayega. Agar aapne ye request nahi ki, is email ko ignore karein.

Team GroceryPOS""")
    log_audit("admin", "admin_forgot_password_otp_requested", request)
    return generic_msg

class AdminForgotPasswordReset(BaseModel):
    username: str
    otp: str
    new_password: str
    sign_out_all_sessions: bool = True

@app.post("/api/admin/forgot-password/reset")
async def admin_forgot_password_reset(req: AdminForgotPasswordReset, request: Request):
    if len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="Naya password kam se kam 6 characters ka hona chahiye")
    conn = get_db()
    row = conn.execute("SELECT * FROM admin_account WHERE id=1").fetchone()
    conn.close()
    if not row or req.username != row["username"]:
        raise HTTPException(status_code=400, detail="Invalid request")

    result = verify_otp(row["email"], "admin_forgot_password", req.otp)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])

    new_hash = await run_in_threadpool(hash_password, req.new_password)
    conn = get_db()
    if req.sign_out_all_sessions:
        conn.execute(
            "UPDATE admin_account SET password_hash=?, token_version=token_version+1, updated_at=datetime('now') WHERE id=1",
            (new_hash,)
        )
    else:
        conn.execute("UPDATE admin_account SET password_hash=?, updated_at=datetime('now') WHERE id=1",
                     (new_hash,))
    conn.commit()
    conn.close()
    log_audit("admin", "admin_password_reset_via_otp", request,
              f"sign_out_all_sessions={req.sign_out_all_sessions}")
    return {"success": True, "message": "Password successfully reset — ab naye password se login karein"}

# ======================== ADMIN ROUTES ========================
@app.get("/api/admin/users")
async def admin_get_users(admin = Depends(get_admin)):
    try:
        users = await run_in_threadpool(google_auth.list_users)
    except SheetManagerError as e:
        raise HTTPException(status_code=503, detail=f"Cloud auth unavailable: {e}")
    # Shape each user to match what admin.html expects (same field names as
    # the old SQLite `users` table response, so the admin panel needs zero changes).
    return [
        {
            "id": u["user_id"],
            "username": u["username"],
            "full_name": u["full_name"],
            "email": u["email"],
            "whatsapp": u["whatsapp"],
            "city": u["city"],
            "store_type": u["store_type"],
            "plan": u["subscription_plan"],
            "plan_amount": u["plan_amount"],
            "upi_used": u["upi_used"],
            "receipt_path": u["receipt_path"],
            "payment_status": u["payment_status"],
            "is_active": 1 if u["is_active"] else 0,
            "is_blocked": 1 if u["is_blocked"] else 0,
            "trial_bills_used": u["trial_bills_used"],
            "subscription_start": u["created_date"],
            "subscription_expiry": u["expiry_date"],
            "created_at": u["created_date"],
            "approved_at": u["last_login"],
            "max_devices": u["device_limit"],
            "allowed_ips": u["allowed_ips"],
        }
        for u in sorted(users, key=lambda x: x["created_date"], reverse=True)
    ]

@app.post("/api/admin/approve")
async def admin_approve(req: AdminApprove, admin = Depends(get_admin)):
    res = await run_in_threadpool(
        google_auth.approve_user,
        user_id=req.user_id, new_username=req.username,
        password=req.password, plan_days=req.plan_days,
    )
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("message", "Approve failed"))

    password = res["generated_password"]
    plan = res["plan"]
    expiry_str = res["expiry"]
    expiry = datetime.fromisoformat(expiry_str)

    user = await run_in_threadpool(google_auth.get_user, req.username)

    # Optional per-shop "export bills to their own Google Sheet" config —
    # unrelated to the auth Sheet, stored locally (operational config, not credentials).
    if req.gas_url or req.sheet_id:
        conn = get_db()
        conn.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (req.user_id,))
        conn.execute("UPDATE user_settings SET gas_url=?, sheet_id=? WHERE user_id=?",
                     (req.gas_url or '', req.sheet_id or '', req.user_id))
        conn.commit()
        conn.close()

    _raw_url = os.getenv("APP_URL", "https://dkapoore.github.io/grocery-pos-saas/saas_pos/frontend/app.html")
    # Normalise — remove trailing slash, avoid double /app.html
    _raw_url = _raw_url.rstrip("/")
    if not _raw_url.endswith("app.html"):
        _raw_url = _raw_url.rstrip("/") + "/app.html"
    login_url = _raw_url

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

    await run_in_threadpool(send_email, user["email"], "🎉 Your POS Login Credentials", email_body)
    await run_in_threadpool(send_whatsapp, user["whatsapp"], wa_msg)

    # Push to Google Sheets (legacy notification log tab)
    # NOTE: 'Z' suffix makes this an unambiguous UTC timestamp — without it,
    # Apps Script's `new Date(...)` parses the string as local time before
    # converting to Asia/Kolkata, silently shifting the displayed time by
    # several hours (this was the "wrong approval timing" bug).
    await run_in_threadpool(push_to_google_sheets, {
        "action": "approved",
        "username": req.username,
        "plan": plan,
        "expiry": expiry_str,
        "approved_at": datetime.utcnow().isoformat() + "Z"
    })

    return {"success": True, "generated_password": password,
            "message": f"User approved. Credentials sent to {user['email']} and WhatsApp {user['whatsapp']}"}

@app.post("/api/admin/generate-settings-password")
async def admin_generate_settings_password(req: AdminGenSettingsPassword, admin = Depends(get_admin)):
    """Admin generates (or regenerates) the per-user Settings-tab lock password.
    Only the shop owner (admin) can set/reset this — the user cannot change it themselves.
    Stored as settings_password_hash on the Sheet (account-management data)."""
    user = await run_in_threadpool(google_auth.get_user_by_id, req.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    new_password = generate_password()
    new_hash = await run_in_threadpool(hash_password, new_password)
    ok = await run_in_threadpool(google_auth.update_settings_password, user["username"], new_hash)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to save settings password to Sheet")

    shop_name = user["full_name"] or user["username"]

    wa_message = f"""🔒 *Settings Lock Password*

Hello {shop_name}! 👋

Aapke GroceryPOS *Settings* tab ke liye ek surakshit password set kiya gaya hai. Ye password sirf aapke liye hai — store ke staff ko Settings tab access karne ke liye ye password chahiye hoga.

🔹 *Username:* {user['username']}
🔹 *Settings Password:* {new_password}

⚠️ Is password ko surakshit rakhein. Agar bhool jaayein, to admin se naya password generate karwa sakte hain.

🙏 Team GroceryPOS"""

    email_message = f"""Hello {shop_name},

A Settings-tab lock password has been generated for your GroceryPOS account. Staff members will need this password to access the Settings tab (shop details, UPI, etc).

Username          : {user['username']}
Settings Password : {new_password}

Please keep this password safe. If forgotten, contact admin to generate a new one.

Thank you,
Team GroceryPOS"""

    return {
        "success": True,
        "password": new_password,
        "username": user["username"],
        "whatsapp_message": wa_message,
        "email_message": email_message,
        "whatsapp_number": user["whatsapp"],
        "email": user["email"]
    }

@app.post("/api/admin/reset-password")
async def admin_reset_password(req: AdminResetPassword, request: Request, admin = Depends(get_admin)):
    """Admin regenerates a user's LOGIN password (not the Settings-tab lock —
    see /api/admin/generate-settings-password for that). Either a random
    password is generated, or the admin supplies a custom one. When
    force_change is True the account is flagged so the user must set their
    own new password the next time they log in."""
    user = await run_in_threadpool(google_auth.get_user_by_id, req.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if req.custom_password:
        if len(req.custom_password) < 6:
            raise HTTPException(status_code=400, detail="Password kam se kam 6 characters ka hona chahiye")
        new_password = req.custom_password
    else:
        new_password = generate_password()

    new_hash = await run_in_threadpool(hash_password, new_password)
    ok = await run_in_threadpool(
        google_auth.reset_password, req.user_id, new_hash, req.force_change
    )
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to reset password on Sheet")

    shop_name = user["full_name"] or user["username"]
    force_note_wa = "\n\n⚠️ Agla login karte hi aapko naya password khud set karna hoga." if req.force_change else ""
    force_note_email = "\n\nNote: You will be asked to set your own new password the next time you log in." if req.force_change else ""

    wa_message = f"""🔑 *Password Reset*

Hello {shop_name}! 👋

Aapke GroceryPOS account ka login password admin dwara reset kiya gaya hai.

🔹 *Username:* {user['username']}
🔹 *New Password:* {new_password}{force_note_wa}

🙏 Team GroceryPOS"""

    email_message = f"""Hello {shop_name},

Your GroceryPOS login password has been reset by the admin.

Username     : {user['username']}
New Password : {new_password}{force_note_email}

Thank you,
Team GroceryPOS"""

    await run_in_threadpool(send_email, user["email"], "🔑 Your GroceryPOS Password Was Reset", email_message)
    await run_in_threadpool(send_whatsapp, user["whatsapp"], wa_message)
    log_audit("admin", "admin_reset_user_password", request, f"user={user['username']}, force_change={req.force_change}")

    return {
        "success": True,
        "password": new_password,
        "username": user["username"],
        "force_change": req.force_change,
        "whatsapp_message": wa_message,
        "email_message": email_message,
        "whatsapp_number": user["whatsapp"],
        "email": user["email"]
    }

# ======================== HERO CAROUSEL IMAGES (Admin managed) ========================
@app.get("/api/hero-images")
async def get_hero_images():
    """Public — used by the landing page mobile mockup carousel (9:16 portrait)."""
    conn = get_db()
    row = conn.execute("SELECT value FROM app_config WHERE key='hero_images'").fetchone()
    conn.close()
    images = json.loads(row["value"]) if row and row["value"] else []
    return {"images": images}

@app.put("/api/admin/hero-images")
async def update_hero_images(req: HeroImagesUpdate, admin = Depends(get_admin)):
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO app_config (key, value) VALUES ('hero_images', ?)",
                 (json.dumps(req.images),))
    conn.commit()
    conn.close()
    return {"success": True, "images": req.images}

@app.get("/api/preview-images")
async def get_preview_images():
    """Public — used by the 'See Software In Action' gallery carousel (3:2 landscape)."""
    conn = get_db()
    row = conn.execute("SELECT value FROM app_config WHERE key='preview_images'").fetchone()
    conn.close()
    images = json.loads(row["value"]) if row and row["value"] else []
    return {"images": images}

@app.put("/api/admin/preview-images")
async def update_preview_images(req: HeroImagesUpdate, admin = Depends(get_admin)):
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO app_config (key, value) VALUES ('preview_images', ?)",
                 (json.dumps(req.images),))
    conn.commit()
    conn.close()
    return {"success": True, "images": req.images}


# ======================== FREE TRIAL WATERMARK (Admin managed) ========================
# Controls whether the "⚠️ FREE TRIAL — Upgrade for Professional Bills" banner
# shows on bills for free-trial accounts. Defaults to enabled (matches the
# behaviour before this toggle existed) unless an admin explicitly turns it off.
@app.get("/api/trial-watermark-status")
async def get_trial_watermark_status():
    """Public — read by every client to decide whether to render the free-trial watermark."""
    conn = get_db()
    row = conn.execute("SELECT value FROM app_config WHERE key='trial_watermark_enabled'").fetchone()
    conn.close()
    enabled = True if not row or row["value"] is None else row["value"] == "1"
    return {"enabled": enabled}

@app.put("/api/admin/trial-watermark")
async def update_trial_watermark_status(req: TrialWatermarkUpdate, admin = Depends(get_admin)):
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO app_config (key, value) VALUES ('trial_watermark_enabled', ?)",
                 ("1" if req.enabled else "0",))
    conn.commit()
    conn.close()
    return {"success": True, "enabled": req.enabled}


@app.post("/api/admin/block")
async def admin_block(req: AdminBlock, admin = Depends(get_admin)):
    if req.block:
        ok = await run_in_threadpool(google_auth.set_account_status, req.user_id, blocked=True)
    else:
        ok = await run_in_threadpool(google_auth.set_account_status, req.user_id, blocked=False, active=True)
    if not ok:
        raise HTTPException(status_code=400, detail="Failed to update account status")
    return {"success": True}

@app.post("/api/admin/delete-user")
async def admin_delete_user(req: AdminDeleteUser, admin = Depends(get_admin)):
    """Permanently removes the account row from the Google Sheet. POS data
    (products/bills/customers tied to this user_id) is intentionally left
    untouched in SQLite, in case the admin needs to recover it later —
    only the login/account record is deleted."""
    user = await run_in_threadpool(google_auth.get_user_by_id, req.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    ok = await run_in_threadpool(google_auth.delete_account, req.user_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to delete user from Sheet")
    return {"success": True, "message": f"User '{user['username']}' deleted"}

class UpdateGasUrl(BaseModel):
    user_id: int
    gas_url: str
    sheet_id: str = ""

@app.post("/api/admin/update-gas-url")
async def admin_update_gas_url(req: UpdateGasUrl, admin = Depends(get_admin)):
    """Per-shop 'export bills to my own Google Sheet' config — unrelated to
    the auth Sheet. This is operational/billing config, so it stays in the
    local user_settings table, keyed by the Sheet's stable numeric user_id."""
    user = await run_in_threadpool(google_auth.get_user_by_id, req.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (req.user_id,))
    conn.execute("UPDATE user_settings SET gas_url=?, sheet_id=? WHERE user_id=?",
                 (req.gas_url, req.sheet_id, req.user_id))
    conn.commit()
    conn.close()
    return {"success": True, "message": f"GAS URL updated for user {user['username']}"}

@app.post("/api/admin/extend")
async def admin_extend(req: ExtendSubscription, admin = Depends(get_admin)):
    res = await run_in_threadpool(google_auth.extend_subscription, req.user_id, req.days)
    if not res.get("success"):
        raise HTTPException(status_code=404, detail=res.get("message", "User not found"))
    return res

@app.get("/api/admin/receipt/{user_id}")
async def get_receipt(user_id: int, token: str = None, credentials: HTTPAuthorizationCredentials = Depends(security)):
    # Support both Bearer header (API calls) and ?token= query param (img src / download link)
    raw_token = token or (credentials.credentials if credentials else None)
    if not raw_token:
        raise HTTPException(status_code=401, detail="Admin not authenticated")
    payload = decode_token(raw_token)
    if not payload or payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access only")
    user = await run_in_threadpool(google_auth.get_user_by_id, user_id)
    if not user or not user["receipt_path"]:
        raise HTTPException(status_code=404, detail="Receipt not found")
    if not os.path.exists(user["receipt_path"]):
        raise HTTPException(status_code=404, detail="Receipt file not found")
    return FileResponse(user["receipt_path"])

@app.get("/api/admin/stats")
async def admin_stats(admin = Depends(get_admin)):
    try:
        users = await run_in_threadpool(google_auth.list_users)
    except SheetManagerError as e:
        raise HTTPException(status_code=503, detail=f"Cloud auth unavailable: {e}")
    total = len(users)
    active = sum(1 for u in users if u["is_active"])
    pending = sum(1 for u in users if u["payment_status"] == "pending")
    blocked = sum(1 for u in users if u["is_blocked"])
    return {"total": total, "active": active, "pending_verification": pending, "blocked": blocked}

@app.get("/api/admin/audit-log")
async def get_audit_log(admin = Depends(get_admin), limit: int = 100):
    limit = max(1, min(500, limit))
    conn = get_db()
    rows = conn.execute(
        "SELECT actor, action, details, ip_address, user_agent, created_at FROM audit_log ORDER BY id DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return {"entries": [dict(r) for r in rows]}

@app.post("/api/admin/update-device-limit")
async def admin_update_device_limit(req: UpdateDeviceLimit, admin = Depends(get_admin)):
    """Set max devices and IP whitelist per user"""
    max_d = max(1, min(50, req.max_devices))
    ok = await run_in_threadpool(google_auth.update_device_limit, req.user_id, max_d, req.allowed_ips)
    if not ok:
        raise HTTPException(status_code=404, detail="User not found")
    return {"success": True, "max_devices": max_d}

@app.get("/api/admin/sessions/{user_id}")
async def admin_get_sessions(user_id: int, admin = Depends(get_admin)):
    """Get all active sessions for a user"""
    conn = get_db()
    sessions = conn.execute(
        """SELECT id, device_id, device_label, ip_address, user_agent,
                  last_seen, created_at, is_active
           FROM user_sessions WHERE user_id=? ORDER BY last_seen DESC""",
        (user_id,)
    ).fetchall()
    conn.close()
    return [dict(s) for s in sessions]

@app.post("/api/admin/sessions/revoke")
async def admin_revoke_session(req: ForceLogoutSession, admin = Depends(get_admin)):
    """Force logout a specific session"""
    conn = get_db()
    conn.execute("UPDATE user_sessions SET is_active=0 WHERE id=?", (req.session_id,))
    conn.commit()
    conn.close()
    return {"success": True, "message": f"Session {req.session_id} revoked"}

@app.post("/api/admin/sessions/revoke-all")
async def admin_revoke_all_sessions(req: ForceLogoutUser, admin = Depends(get_admin)):
    """Force logout ALL sessions for a user"""
    conn = get_db()
    conn.execute("UPDATE user_sessions SET is_active=0 WHERE user_id=?", (req.user_id,))
    conn.commit()
    conn.close()
    return {"success": True, "message": f"All sessions revoked for user {req.user_id}"}

@app.post("/api/auth/logout")
async def logout(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """User self-logout — deactivate their session token"""
    if not credentials:
        return {"success": True}
    token_hash = hashlib.sha256(credentials.credentials.encode()).hexdigest()
    conn = get_db()
    conn.execute("UPDATE user_sessions SET is_active=0 WHERE token_hash=?", (token_hash,))
    conn.commit()
    conn.close()
    return {"success": True}

# ======================== SERVE FRONTEND ========================
@app.get("/")
async def root():
    return FileResponse("../frontend/app.html")

@app.get("/app")
async def serve_app():
    return FileResponse("../frontend/app.html")

@app.get("/admin")
async def serve_admin():
    return FileResponse("../admin/admin.html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
