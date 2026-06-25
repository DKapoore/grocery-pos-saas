# GroceryPOS SaaS — Complete Setup Guide

## 📁 Project Structure

```
saas_pos/
├── backend/
│   ├── main.py           ← FastAPI backend (all API routes)
│   └── requirements.txt  ← Python dependencies
├── frontend/
│   └── app.html          ← Landing + Login + POS App (single file)
├── admin/
│   └── admin.html        ← Admin panel (single file)
├── uploads/              ← Payment receipts stored here (auto-created)
└── README.md
```

---

## 🚀 Step 1: Install Python Dependencies

```bash
cd saas_pos/backend
pip install -r requirements.txt
```

---

## 🔧 Step 2: Configure Environment Variables

Create a `.env` file in `backend/` folder (or set system env vars):

```env
# REQUIRED — Change in production!
SECRET_KEY=your_super_secret_key_here_change_this

# Admin credentials
ADMIN_USER=admin
ADMIN_PASS=YourAdminPassword@123

# Your app URL (used in credential emails)
APP_URL=https://yourpos.com

# Email (Gmail SMTP recommended)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=yourmail@gmail.com
SMTP_PASS=your_gmail_app_password

# WhatsApp API (optional — Wati / Twilio)
WA_API_URL=

# Google Apps Script Webhook (optional)
GAS_WEBHOOK_URL=https://script.google.com/macros/s/YOUR_SCRIPT_ID/exec
```

---

## ▶️ Step 3: Start Backend Server

```bash
cd saas_pos/backend
uvicorn main:app --host 0.0.0.0 --port 8000
```

For production with auto-restart:
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

API will be available at: `http://localhost:8000`

---

## 🌐 Step 4: Serve Frontend Files

**Option A — Simple (for testing):**
Open `frontend/app.html` directly in browser.
Change API URL in the HTML file:
```javascript
const API = 'http://your-server-ip:8000';
```

**Option B — FastAPI serves frontend (recommended):**
Place `app.html` and `admin.html` in the backend folder.
The backend already has routes:
- `/` → Landing + POS App
- `/admin` → Admin Panel

**Option C — Nginx (production):**
```nginx
server {
    listen 80;
    server_name yourpos.com;

    location /api {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location / {
        root /var/www/pos;
        try_files $uri $uri/ /app.html;
    }
}
```

---

## 🔐 Admin Panel Access

Open: `http://yourserver/admin/admin.html`

Default credentials:
- Username: `admin`
- Password: `Admin@POS2024` (change in env var `ADMIN_PASS`)

---

## ✉️ Gmail SMTP Setup

1. Enable 2-Factor Auth on Gmail
2. Go to Google Account → Security → App Passwords
3. Generate App Password for "Mail"
4. Use that 16-digit password in `SMTP_PASS`

---

## 📊 Google Sheets Integration

1. Open Google Sheets → Extensions → Apps Script
2. Paste the code from Admin Panel → Google Sheets tab
3. Click Deploy → New Deployment → Web App
4. Set "Who has access" → Anyone
5. Copy the Web App URL
6. Set `GAS_WEBHOOK_URL` in your `.env` file

---

## 📱 PWA Installation

Users can install as app on mobile:
- Open in Chrome browser
- Tap "Add to Home Screen"
- Works like a native app

---

## 🔄 USB Barcode Scanner

No configuration needed:
- Plug USB scanner into PC/Laptop
- Open the POS app
- Scan any barcode — it auto-detects

---

## 📷 Mobile Camera Scanner

- Click the camera icon in POS
- Allow camera permission
- Point at barcode

---

## 💾 Database

SQLite is used by default (file: `pos_saas.db`).

For PostgreSQL (production):
```bash
pip install psycopg2-binary
```
Change in `main.py`:
```python
# Replace sqlite3 with asyncpg or psycopg2
DATABASE_URL = "postgresql://user:pass@localhost/posdb"
```

---

## 🆓 Free Trial Logic

- New users get 3 free bills
- Trial lock is enforced **server-side** (cannot be bypassed)
- Free bills show "FREE TRIAL VERSION" watermark
- After 3 bills: billing is locked, upgrade page shown

---

## 🔑 Subscription Flow

1. User visits landing page → selects plan
2. Pays via UPI QR → screenshots payment
3. Fills registration form → uploads screenshot
4. Admin sees request in panel → verifies payment
5. Admin clicks Approve → sets username/password/expiry
6. System auto-sends credentials via Email + WhatsApp
7. User logs in → full access until expiry

---

## 🌍 Production Deployment (Ubuntu VPS)

```bash
# 1. Install Python & Nginx
sudo apt update && sudo apt install python3-pip nginx -y

# 2. Clone/upload your files
mkdir /var/www/pos && cd /var/www/pos

# 3. Install deps
pip3 install -r backend/requirements.txt

# 4. Create systemd service
sudo nano /etc/systemd/system/pos.service
```

```ini
[Unit]
Description=GroceryPOS API
After=network.target

[Service]
User=www-data
WorkingDirectory=/var/www/pos/backend
ExecStart=/usr/local/bin/uvicorn main:app --host 127.0.0.1 --port 8000 --workers 2
Restart=always
Environment="SECRET_KEY=your_key"
Environment="ADMIN_PASS=YourPass"
Environment="SMTP_USER=yourmail@gmail.com"
Environment="SMTP_PASS=yourapppass"

[Install]
WantedBy=multi-user.target
```

```bash
# 5. Start & enable
sudo systemctl enable pos
sudo systemctl start pos

# 6. Setup Nginx (see config above)
sudo certbot --nginx -d yourpos.com  # SSL certificate
```

---

## 📞 Support API Endpoints (Quick Reference)

| Endpoint | Method | Description |
|---|---|---|
| `/api/auth/login` | POST | User login |
| `/api/auth/register` | POST | New registration |
| `/api/auth/free-trial` | POST | Start free trial |
| `/api/auth/me` | GET | Get current user |
| `/api/products` | GET/POST | Products CRUD |
| `/api/bills` | GET/POST | Bills |
| `/api/bills/trial-status` | GET | Trial info |
| `/api/customers` | GET | Customer list |
| `/api/customers/lookup/{mobile}` | GET | Find customer |
| `/api/customers/redeem` | POST | Redeem points |
| `/api/settings` | GET/PUT | Shop settings |
| `/api/bills/whatsapp` | POST | WA message link |
| `/api/admin/login` | POST | Admin login |
| `/api/admin/users` | GET | All users |
| `/api/admin/approve` | POST | Approve user |
| `/api/admin/block` | POST | Block/unblock |
| `/api/admin/extend` | POST | Extend subscription |
| `/api/admin/stats` | GET | Dashboard stats |
