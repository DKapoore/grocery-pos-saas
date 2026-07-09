# 🛒 Grocery POS SaaS — Setup & Google Sheet Integration Guide

## 🔑 Cloud Auth Setup (REQUIRED — do this first)

As of this update, **all login/signup/account data lives in a Google Sheet**,
not in the Render database. This means user accounts survive every redeploy
automatically — Render's disk is no longer a single point of failure for
logins. The app **will not work** until this is set up.

```
User → FastAPI Backend (Render) → Google Apps Script Web API → Google Sheet (Users tab)
```

### Step A1 — Create the Auth Google Sheet

1. Go to [sheets.google.com](https://sheets.google.com) → New Sheet
2. Name it anything, e.g. `GroceryPOS Cloud Auth`
3. You don't need to create the `Users` tab yourself — the script creates it
   automatically on first run, with these columns:
   `user_id, username, password_hash, full_name, email, whatsapp, city,
   store_type, subscription_plan, plan_amount, expiry_date, account_status,
   device_limit, allowed_ips, trial_bills_used, payment_status, upi_used,
   receipt_path, settings_password_hash, created_date, last_login`

### Step A2 — Add the Apps Script

1. In your new Sheet: **Extensions → Apps Script**
2. Delete any existing code
3. Paste the entire contents of `saas_pos/GoogleAppsScript.js`
4. Save (Ctrl+S), name the project e.g. `GroceryPOS Cloud Auth API`

### Step A3 — Set the shared secret (important for security)

This secret stops random people from hitting your public Web App URL and
reading/writing user accounts.

1. In the Apps Script editor → click the **⚙️ Project Settings** (gear icon)
2. Scroll to **Script Properties** → **Add script property**
3. Property: `API_SECRET`  →  Value: a long random string (e.g. generate one
   at [randomkeygen.com](https://randomkeygen.com))
4. Save — remember this value, you'll need it again in Step A5

### Step A4 — Deploy as Web App

1. Click **Deploy → New Deployment**
2. Select type: **Web App**
3. Set:
   - **Execute as:** Me (your Google account)
   - **Who has access:** Anyone
4. Click **Deploy**
5. **Copy the Web App URL** — looks like:
   ```
   https://script.google.com/macros/s/AKfycbXXXXXXXX/exec
   ```

> ⚠️ Whenever you edit `GoogleAppsScript.js` and want the changes live, you
> must do **Deploy → Manage Deployments → ✏️ Edit → New version → Deploy**.
> Saving the script alone does NOT update the live Web App URL.

### Step A5 — Configure FastAPI (Render) to use it

In Render dashboard → your backend service → **Environment**, add:

```
GAS_WEBHOOK_URL=https://script.google.com/macros/s/AKfycbXXXXXXXX/exec
GAS_API_SECRET=<the same random string from Step A3>
```

Redeploy. Login/signup/admin will now work, backed entirely by the Sheet.

### Verifying it works

1. Open your app → **Try Demo / Free Trial** → sign up with any username.
2. Open the Google Sheet → a `Users` tab should now exist with one row.
3. Open the Admin Panel → that user should appear in the Users table.
4. Restart/redeploy the Render service → the user should still be there
   and still able to log in (this is the whole point of the migration).

---

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
7. Add **Persistent Disk** (for POS data — products/bills/receipts only,
   NOT required for login to survive redeploys anymore):
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
   GAS_WEBHOOK_URL=https://script.google.com/macros/s/AKfycbXXXXXXXX/exec
   GAS_API_SECRET=<same secret as the Apps Script Property>
   ```

> ⚠️ **POS data** (products, bills, customers, receipts) still benefits from
> the Persistent Disk — without it, that data is lost on redeploy. **Login
> accounts**, however, are now completely independent of this disk; they
> live in the Google Sheet and always survive redeploys.

---

## ☁️ Google Sheet Integration Setup (optional — per-shop product sync)

> ⚠️ **Ye Auth Sheet se bilkul alag hai.** Auth Sheet (upar) admin ka ek global system hai. Ye section har shop owner ke apne alag Sheet ke liye hai — products sync aur sales export ke liye. Login/account se koi connection nahi.

**File:** `saas_pos/GoogleAppsScript_PerShop.js` — sirf yahi file yahan use karni hai, `GoogleAppsScript.js` nahi.

**Kya milta hai:**
- `Products` sheet → POS app mein products + rates automatic load
- `Sales` sheet → har completed bill automatically record
- `Sales_Items` sheet → item-wise detailed analytics
- App se connection ping/test support

### Step 1 — Apna Google Sheet banao

1. [sheets.google.com](https://sheets.google.com) → New Sheet
2. Naam dein: e.g. `MyShop POS Data`
3. **Setup automatic hoga** — `setupSheets()` function khud sab sheets banata hai (Step 4 mein)

### Step 2 — Apps Script add karo

1. Sheet mein: **Extensions → Apps Script**
2. Purana sab code delete karo
3. `saas_pos/GoogleAppsScript_PerShop.js` ka poora content paste karo
4. Save (Ctrl+S), project naam: e.g. `MyShop POS Script`

### Step 3 — Pehli baar Setup run karo (optional)

1. Apps Script editor mein: **Run → `setupSheets`**
2. Permission maangega → Allow karo
3. `Products`, `Sales`, `Sales_Items` tabs automatically ban jayenge with sample data

### Step 4 — Deploy as Web App

1. **Deploy → New Deployment**
2. Type: **Web App**
3. Execute as: **Me**
4. Who has access: **Anyone**
5. Deploy → **URL copy karo**

> ⚠️ Jab bhi code change karo: **Deploy → Manage Deployments → Edit → New Version → Deploy**

### Step 5 — URL POS app mein lagao

**Option A (Admin Panel se — recommended):**
Admin Panel → Users → us user ki row mein 📋 button → GAS URL field mein paste karo → Save

**Option B (User khud Settings mein):**
POS App → Settings → ☁️ Cloud tab → GAS URL field mein paste karo → Save

### Products Sheet format

Sheet mein headers hone chahiye (exact naam zaruri nahi — keywords match hote hain):

| Column | Accepted Keywords | Example |
|--------|------------------|---------|
| Product name | name, item, product | Amul Milk 500ml |
| Price | price, rate, mrp | 28 |
| Category | category, cat, type | Dairy |
| Barcode | barcode, code, sku | 8901030010214 |
| Tax % | tax, gst, vat | 5 |
| Stock | stock, qty, quantity | 50 |
| Unit | unit, uom | pcs |

Blank rows aur zero-price rows automatically skip ho jaate hain.

### Test karo (deploy se pehle)

Apps Script editor mein: **Run → `testConnection`**
Console mein result dikhega — products count, aur ek test sale row `Sales` sheet mein add hogi.


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
| `RENDER_DATA_DIR` | Persistent disk path (POS data + receipts only) | `/var/data` |
| `APP_URL` | Frontend URL for emails | `https://...` |
| `SMTP_HOST` | Email server | `smtp.gmail.com` |
| `SMTP_PORT` | Email port | `587` |
| `SMTP_USER` | Email address | `you@gmail.com` |
| `SMTP_PASS` | App password | Gmail App Password |
| `GAS_WEBHOOK_URL` | **REQUIRED** — Cloud Auth Apps Script Web App URL | `https://script.google.com/.../exec` |
| `GAS_API_SECRET` | **REQUIRED** — shared secret matching the Apps Script's `API_SECRET` property | random string |

---

## 🏗️ Backend Module Architecture

```
main.py              ← FastAPI routes (HTTP layer only)
   │
   ▼
google_auth.py        ← Auth business logic: bcrypt hashing, login/signup/
   │                     approve/block/extend rules, JSON contract shaping
   ▼
apps_script_api.py    ← Typed wrapper: one function per Sheet action
   │                     (signup_user, lookup_user, update_account, ...)
   ▼
sheet_manager.py      ← Raw HTTP client: POSTs JSON to the Apps Script
   │                     Web App, retries on network failure
   ▼
Google Apps Script (GoogleAppsScript.js) ← reads/writes the Users sheet
```

POS business data (products, bills, customers, per-shop settings, device
sessions) is untouched by this migration and continues to use SQLite
directly from `main.py`, keyed by the Sheet's stable numeric `user_id`.

### Future migration to PostgreSQL

Because `main.py` only ever calls into `google_auth.py` — never directly
into `apps_script_api.py` or `sheet_manager.py` — moving off Google Sheets
later only requires writing a new `db_api.py` with the same function
signatures as `apps_script_api.py` (`signup_user`, `lookup_user`,
`list_all_users`, `update_account`, `delete_user`, ...) and swapping the
import at the top of `google_auth.py`. No changes would be needed in
`main.py` or any frontend code.

