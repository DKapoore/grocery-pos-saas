# 🛒 Grocery POS SaaS — Setup & Google Sheet Integration Guide

## 🚀 Quick Start

### Frontend (GitHub Pages)
1. Upload `saas_pos/frontend/app.html` to your GitHub repo
2. Enable GitHub Pages → source: root or `docs/` folder
3. Your app URL: `https://yourusername.github.io/repo-name/saas_pos/frontend/app.html`

### Backend (Render Deployment)
1. Push repo to GitHub
2. Go to [render.com](https://render.com) → New Web Service
3. Connect your GitHub repo
4. **Root Directory:** `saas_pos/backend`
5. **Build Command:** `pip install -r requirements.txt`
6. **Start Command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
7. Add **Persistent Disk** (CRITICAL for data survival):
   - Render dashboard → your service → Disks
   - Mount Path: `/var/data`
   - Size: 1 GB (free tier)
8. Set Environment Variables:
   ```
   SECRET_KEY=your_random_secret_here
   ADMIN_USER=admin
   ADMIN_PASS=YourStrongPassword123
   RENDER_DATA_DIR=/var/data
   APP_URL=https://yourusername.github.io/repo-name/saas_pos/frontend/app.html
   ```

> ⚠️ **Without Persistent Disk**, SQLite data is lost on every Render restart/deploy.
> With the disk, all subscriber data, bills, and products are preserved.

---

## ☁️ Google Sheet Integration Setup

Each shop owner gets their **own Google Sheet** connected to their POS app.

### Step 1 — Create Google Sheet

1. Go to [sheets.google.com](https://sheets.google.com) → New Sheet
2. Name it: `MyShop POS Data`
3. Create these tabs (sheets):
   - `Products` — columns: `Name, Price, Category, Barcode, Tax%, Stock`
   - `Sales` — auto-filled by app
   - `Customers` — auto-filled by app

### Step 2 — Add Google Apps Script

1. In your Sheet: **Extensions → Apps Script**
2. Delete existing code
3. Paste the entire code from `saas_pos/GoogleAppsScript.js`
4. Save (Ctrl+S), name project: `POS Integration`

### Step 3 — Deploy as Web App

1. Click **Deploy → New Deployment**
2. Select type: **Web App**
3. Set:
   - **Execute as:** Me (your Google account)
   - **Who has access:** Anyone
4. Click **Deploy**
5. **Copy the Web App URL** — it looks like:
   ```
   https://script.google.com/macros/s/AKfycbXXXXXXXX/exec
   ```

### Step 4 — Connect to POS App

1. Open POS app → Settings (⚙️) → Cloud tab
2. Paste your Web App URL in **Google Apps Script URL**
3. Click **Test Connection** — should show ✅
4. Click **Save Cloud Config**

### Step 5 — Sync Products from Google Sheet

**Sheet format (Products tab):**
```
Name          | Price | Category  | Barcode       | Tax% | Stock
Basmati Rice  | 120   | Grocery   | 8901234567890 | 5    | 50
Tata Salt     | 25    | Grocery   | 8901234567891 | 0    | 100
```

In app: Settings → Cloud → **☁️ Google Sheet** button → products will import.

> Products tab se prices live update hote hain automatically every 5 minutes.

---

## 📱 Google Apps Script — What It Does

The `GoogleAppsScript.js` handles these actions via HTTP:

| Action | Description |
|--------|-------------|
| `ping` | Connection test |
| `getProducts` | Returns product list for sync |
| `saveSale` | Saves bill to Sales tab |
| `getAnalytics` | Returns daily/weekly stats |
| `syncCustomers` | Customer loyalty data |

---

## 🖨️ Thermal Printer Setup

The app auto-detects **58mm thermal** paper size. Browser print settings:

1. Select **Thermal (58mm)** in Print dialog
2. Browser will auto-set page size to `58mm × auto`
3. Margins: 2mm auto-set
4. For **80mm** printers: change CSS `@page { size: 80mm auto; }` in app.html

For best results:
- Chrome browser → Print → **Destination: Your thermal printer**
- Disable "headers and footers"
- Paper size: Custom → 58×210mm

---

## 📊 Sales Charts

In **Sales History (📊)**, 3 chart tabs are available:
- **📈 Daily Sales** — last 7 days bar chart
- **🏆 Product Wise** — top 10 products by revenue
- **📦 Stock Balance** — low stock products highlighted in red

---

## 🔐 Admin Panel

URL: `https://your-render-url.onrender.com/admin`  
Default: `admin` / `Admin@POS2024`

Change via Render environment variables:
```
ADMIN_USER=youradmin
ADMIN_PASS=StrongPassword123
```

---

## 📦 CSV Product Import

1. Products → Add New → **📥 Sample CSV Format** to download template
2. Fill in your products
3. Click **📂 Upload CSV** to bulk import

CSV columns: `Name, Price, Category, Barcode, Tax%, Stock`

---

## 🔧 Environment Variables Reference

| Variable | Description | Example |
|----------|-------------|---------|
| `SECRET_KEY` | JWT secret | random string |
| `ADMIN_USER` | Admin login username | `admin` |
| `ADMIN_PASS` | Admin login password | `Admin@POS2024` |
| `RENDER_DATA_DIR` | Persistent disk path | `/var/data` |
| `APP_URL` | Frontend URL for emails | `https://...` |
| `SMTP_HOST` | Email server | `smtp.gmail.com` |
| `SMTP_PORT` | Email port | `587` |
| `SMTP_USER` | Email address | `you@gmail.com` |
| `SMTP_PASS` | App password | Gmail App Password |
| `GAS_WEBHOOK_URL` | Admin-level GAS URL | `https://script.google.com/...` |

