/**
 * ╔══════════════════════════════════════════════════════════════════╗
 * ║         GroceryPOS — Admin Auth Google Apps Script              ║
 * ║         Version: 2.0  |  For: ADMIN ONLY (not for shop owners) ║
 * ╠══════════════════════════════════════════════════════════════════╣
 * ║                                                                  ║
 * ║  ⚠️  YE FILE SIRF ADMIN KE LIYE HAI — SHOP OWNERS KO NAHI     ║
 * ║      DENI HAI.                                                   ║
 * ║                                                                  ║
 * ║  Ye script ek GLOBAL Auth Google Sheet ke saath kaam karta hai  ║
 * ║  jo poore SaaS ka login database hai:                           ║
 * ║   • User signup / login credential check                        ║
 * ║   • Account status (Active/Blocked/Inactive)                    ║
 * ║   • Plan, expiry, device limit                                  ║
 * ║   • Admin panel se user management                              ║
 * ║                                                                  ║
 * ║  Per-shop product sync ke liye alag file hai:                   ║
 * ║  → GoogleAppsScript_PerShop.js  (shop owners ke liye)          ║
 * ║                                                                  ║
 * ╠══════════════════════════════════════════════════════════════════╣
 * ║  SETUP (Admin ek baar kare):                                    ║
 * ║  1. Ek naya Google Sheet banao: "GroceryPOS Cloud Auth"         ║
 * ║  2. Extensions → Apps Script → ye code paste karo              ║
 * ║  3. Script Properties mein API_SECRET set karo                  ║
 * ║  4. Deploy → Web App → URL copy karo                            ║
 * ║  5. Render env vars mein daalo:                                 ║
 * ║       GAS_WEBHOOK_URL = <Web App URL>                           ║
 * ║       GAS_API_SECRET  = <same secret as Script Property>        ║
 * ╚══════════════════════════════════════════════════════════════════╝
 */

// ============================================================
// SHEET NAMES - Agar change karna ho to yahan se karo
// ============================================================
const SHEET_REGISTRATIONS = "Registrations";
const SHEET_APPROVED      = "Approved Users";
const SHEET_BILLS         = "Bills";
const SHEET_USERS         = "Users";   // ★ Cloud Auth Database — single source of truth for login

// ============================================================
// SECURITY — Shared secret between FastAPI backend and this script
// ============================================================
// 1. Apps Script editor → Project Settings (gear icon) → Script Properties
// 2. Add property: API_SECRET = <ek lamba random string yahan>
// 3. FastAPI Render env var GAS_API_SECRET mein bhi WAHI value daalo
// This prevents random people from hitting your public Web App URL and
// reading/writing user accounts.
function getApiSecret() {
  return PropertiesService.getScriptProperties().getProperty('API_SECRET') || '';
}

function checkAuth(data) {
  const expected = getApiSecret();
  if (!expected) return true; // not configured yet — allow (dev mode), but warn in logs
  return data && data.api_secret === expected;
}

// ============================================================
// USERS SHEET — Columns (in order):
// user_id | username | password_hash | full_name | email | whatsapp |
// city | store_type | subscription_plan | plan_amount | expiry_date |
// account_status | device_limit | allowed_ips | trial_bills_used |
// payment_status | upi_used | receipt_path | settings_password_hash |
// created_date | last_login
// ============================================================
const USERS_HEADER = [
  "user_id", "username", "password_hash", "full_name", "email", "whatsapp",
  "city", "store_type", "subscription_plan", "plan_amount", "expiry_date",
  "account_status", "device_limit", "allowed_ips", "trial_bills_used",
  "payment_status", "upi_used", "receipt_path", "settings_password_hash",
  "created_date", "last_login", "must_change_password"
];
// Map column name -> 1-based index, built once from USERS_HEADER
const UCOL = {};
USERS_HEADER.forEach((name, i) => { UCOL[name] = i + 1; });

function getUsersSheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(SHEET_USERS);
  if (!sheet) {
    sheet = ss.insertSheet(SHEET_USERS);
    sheet.appendRow(USERS_HEADER);
    sheet.getRange(1, 1, 1, USERS_HEADER.length)
      .setBackground("#673AB7").setFontColor("white").setFontWeight("bold");
    sheet.setFrozenRows(1);
  }
  return sheet;
}

// Find the row number (1-based, includes header) for a given username.
// Returns -1 if not found. Username match is case-insensitive.
function findUserRow(sheet, username) {
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) { Logger.log('findUserRow: sheet has no data rows (lastRow=' + lastRow + ')'); return -1; }
  const usernames = sheet.getRange(2, UCOL.username, lastRow - 1, 1).getValues();
  const target = String(username || '').trim().toLowerCase();
  // TEMP DEBUG — remove once the DKapoore3 lookup issue is confirmed fixed.
  Logger.log('findUserRow: searching for target=[' + target + '] (length=' + target.length + ')');
  Logger.log('findUserRow: usernames in sheet = ' + JSON.stringify(usernames.map(r => String(r[0] || ''))));
  for (let i = 0; i < usernames.length; i++) {
    const cellVal = String(usernames[i][0] || '').trim().toLowerCase();
    if (cellVal === target) {
      Logger.log('findUserRow: MATCH found at row ' + (i + 2));
      return i + 2; // +2 because data starts at row 2 and i is 0-based
    }
  }
  Logger.log('findUserRow: NO MATCH for [' + target + ']');
  return -1;
}

// Find the row number (1-based) for a given numeric user_id. Returns -1 if not found.
function findUserRowById(sheet, userId) {
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) return -1;
  const ids = sheet.getRange(2, UCOL.user_id, lastRow - 1, 1).getValues();
  const target = parseInt(userId, 10);
  for (let i = 0; i < ids.length; i++) {
    if (parseInt(ids[i][0], 10) === target) return i + 2;
  }
  return -1;
}

function rowToUserObject(sheet, row) {
  const values = sheet.getRange(row, 1, 1, USERS_HEADER.length).getValues()[0];
  const obj = {};
  USERS_HEADER.forEach((name, i) => { obj[name] = values[i]; });
  return obj;
}

// Generate the next numeric user_id. Uses LockService to avoid race
// conditions when two signups happen at almost the same moment.
function getNextUserId(sheet) {
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const lastRow = sheet.getLastRow();
    if (lastRow < 2) return 1;
    const ids = sheet.getRange(2, UCOL.user_id, lastRow - 1, 1).getValues();
    let max = 0;
    ids.forEach(r => { const n = parseInt(r[0], 10); if (!isNaN(n) && n > max) max = n; });
    return max + 1;
  } finally {
    lock.releaseLock();
  }
}

// ============================================================
// MAIN ENTRY POINT - Ye function POST request receive karta hai
// ============================================================
function doPost(e) {
  try {
    const data = JSON.parse(e.postData.contents);
    const action = data.action;

    // ── Cloud Auth actions require the shared secret ──────────────────────
    const AUTH_ACTIONS = ['signup', 'login_lookup', 'get_user', 'get_user_by_id', 'list_users',
                           'update_account', 'delete_user', 'update_last_login', 'send_email'];
    if (AUTH_ACTIONS.indexOf(action) !== -1) {
      if (!checkAuth(data)) {
        return ContentService
          .createTextOutput(JSON.stringify({ success: false, message: "Unauthorized — invalid API secret" }))
          .setMimeType(ContentService.MimeType.JSON);
      }
      if (action === 'send_email') {
        return handleSendEmail(data);
      }
      return handleAuthAction(action, data);
    }

    if (action === "new_registration") {
      handleNewRegistration(data);
    } else if (action === "approved") {
      handleApproved(data);
    } else if (action === "new_bill") {
      handleNewBill(data);
    } else if (action === "saveSale") {
      handleSaveSale(data);
    }

    return ContentService
      .createTextOutput(JSON.stringify({ success: true }))
      .setMimeType(ContentService.MimeType.JSON);

  } catch (err) {
    return ContentService
      .createTextOutput(JSON.stringify({ success: false, error: err.message }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

// ============================================================
// EMAIL DELIVERY (Phase 3 patch) — used for OTP emails specifically.
// Runs via MailApp on Google's own infrastructure, so it works even when
// the backend host (Render, etc.) blocks outbound SMTP ports — which was
// the actual root cause of OTP emails silently never arriving before.
// This function ONLY sends email; OTP generation/hashing/expiry/validation
// all still happen in Python — Apps Script never decides whether a code
// is valid, it just delivers whatever message Python asks it to send.
// ============================================================
function handleSendEmail(data) {
  const out = (obj) => ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
  try {
    if (!data.to || !data.subject || !data.body) {
      return out({ success: false, message: "Missing to/subject/body" });
    }
    MailApp.sendEmail({
      to: data.to,
      subject: data.subject,
      htmlBody: data.body,
      name: "GroceryPOS"
    });
    return out({ success: true });
  } catch (err) {
    return out({ success: false, message: "MailApp error: " + err.message });
  }
}

// ============================================================
// CLOUD AUTH — routes signup/login/admin-management actions
// ============================================================
function handleAuthAction(action, data) {
  const out = (obj) => ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);

  const sheet = getUsersSheet();

  if (action === 'signup') {
    const existingRow = findUserRow(sheet, data.username);
    if (existingRow !== -1) {
      return out({ success: false, message: "Username already exists" });
    }
    const lock = LockService.getScriptLock();
    lock.waitLock(10000);
    let newId;
    try {
      newId = getNextUserId(sheet);
      const now = new Date().toISOString();
      const row = [];
      row[UCOL.user_id - 1] = newId;
      row[UCOL.username - 1] = data.username || '';
      row[UCOL.password_hash - 1] = data.password_hash || '';
      row[UCOL.full_name - 1] = data.full_name || '';
      row[UCOL.email - 1] = data.email || '';
      row[UCOL.whatsapp - 1] = data.whatsapp || '';
      row[UCOL.city - 1] = data.city || '';
      row[UCOL.store_type - 1] = data.store_type || '';
      row[UCOL.subscription_plan - 1] = data.subscription_plan || 'free';
      row[UCOL.plan_amount - 1] = data.plan_amount || 0;
      row[UCOL.expiry_date - 1] = data.expiry_date || '';
      row[UCOL.account_status - 1] = data.account_status || 'Inactive';
      row[UCOL.device_limit - 1] = data.device_limit || 1;
      row[UCOL.allowed_ips - 1] = data.allowed_ips || '';
      row[UCOL.trial_bills_used - 1] = data.trial_bills_used || 0;
      row[UCOL.payment_status - 1] = data.payment_status || 'pending';
      row[UCOL.upi_used - 1] = data.upi_used || '';
      row[UCOL.receipt_path - 1] = data.receipt_path || '';
      row[UCOL.settings_password_hash - 1] = '';
      row[UCOL.created_date - 1] = now;
      row[UCOL.last_login - 1] = '';
      sheet.appendRow(row);
    } finally {
      lock.releaseLock();
    }
    return out({ success: true, user_id: newId });
  }

  if (action === 'login_lookup' || action === 'get_user') {
    const row = findUserRow(sheet, data.username);
    if (row === -1) return out({ success: false, message: "User not found" });
    const user = rowToUserObject(sheet, row);
    return out({ success: true, user: user });
  }

  if (action === 'get_user_by_id') {
    const row = findUserRowById(sheet, data.user_id);
    if (row === -1) return out({ success: false, message: "User not found" });
    const user = rowToUserObject(sheet, row);
    return out({ success: true, user: user });
  }

  if (action === 'list_users') {
    const lastRow = sheet.getLastRow();
    if (lastRow < 2) return out({ success: true, users: [] });
    const values = sheet.getRange(2, 1, lastRow - 1, USERS_HEADER.length).getValues();
    const users = values
      .filter(r => r[UCOL.username - 1]) // skip fully blank rows
      .map(r => {
        const obj = {};
        USERS_HEADER.forEach((name, i) => { obj[name] = r[i]; });
        return obj;
      });
    return out({ success: true, users: users });
  }

  if (action === 'update_account') {
    // Prefer looking up by numeric user_id when provided (stable identity —
    // works even when the admin is renaming the username in this same call).
    // Falls back to username lookup for callers that only know the username.
    let row = -1;
    if (data.user_id !== undefined && data.user_id !== null && data.user_id !== '') {
      row = findUserRowById(sheet, data.user_id);
    }
    if (row === -1 && data.username) {
      row = findUserRow(sheet, data.username);
    }
    if (row === -1) return out({ success: false, message: "User not found" });

    // If renaming, make sure the new username isn't already taken by someone else
    const fields = data.fields || {};
    if (fields.username) {
      const clash = findUserRow(sheet, fields.username);
      if (clash !== -1 && clash !== row) {
        return out({ success: false, message: "Username already taken" });
      }
    }
    Object.keys(fields).forEach(key => {
      if (UCOL[key]) {
        sheet.getRange(row, UCOL[key]).setValue(fields[key]);
      }
    });
    return out({ success: true });
  }

  if (action === 'update_last_login') {
    const row = findUserRow(sheet, data.username);
    if (row === -1) return out({ success: false, message: "User not found" });
    sheet.getRange(row, UCOL.last_login).setValue(new Date().toISOString());
    return out({ success: true });
  }

  if (action === 'delete_user') {
    let row = -1;
    if (data.user_id !== undefined && data.user_id !== null && data.user_id !== '') {
      row = findUserRowById(sheet, data.user_id);
    }
    if (row === -1 && data.username) {
      row = findUserRow(sheet, data.username);
    }
    if (row === -1) return out({ success: false, message: "User not found" });
    sheet.deleteRow(row);
    return out({ success: true });
  }

  return out({ success: false, message: "Unknown auth action: " + action });
}

// ============================================================
// FUNCTION 1: Naya Registration aane par
// ============================================================
function handleNewRegistration(data) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(SHEET_REGISTRATIONS);

  // Sheet nahi hai to banao
  if (!sheet) {
    sheet = ss.insertSheet(SHEET_REGISTRATIONS);
    // Header row
    sheet.appendRow([
      "Sr No", "Date & Time", "Full Name", "Username",
      "Email", "WhatsApp", "City", "Store Type",
      "Plan", "Amount (₹)", "UPI Used", "Status"
    ]);
    // Header ko bold aur color karo
    sheet.getRange(1, 1, 1, 12)
      .setBackground("#4CAF50")
      .setFontColor("white")
      .setFontWeight("bold");
    sheet.setFrozenRows(1);
  }

  const lastRow = sheet.getLastRow();
  const srNo = lastRow; // Header ke baad first row = 1

  sheet.appendRow([
    srNo,
    formatDateTime(data.timestamp),
    data.full_name || "",
    data.username || "",
    data.email || "",
    data.whatsapp || "",
    data.city || "",
    data.store_type || "",
    data.plan || "",
    data.plan_amount || 0,
    data.upi_used || "",
    "⏳ Pending"
  ]);

  // Auto resize columns
  sheet.autoResizeColumns(1, 12);
}

// ============================================================
// FUNCTION 2: User Approve hone par
// ============================================================
function handleApproved(data) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  // ---- Registrations sheet mein status update karo ----
  const regSheet = ss.getSheetByName(SHEET_REGISTRATIONS);
  if (regSheet) {
    const usernameCol = 4; // Column D = Username
    const statusCol   = 12; // Column L = Status
    const lastRow = regSheet.getLastRow();

    for (let i = 2; i <= lastRow; i++) {
      const cell = regSheet.getRange(i, usernameCol).getValue();
      if (cell === data.username) {
        regSheet.getRange(i, statusCol).setValue("✅ Approved");
        regSheet.getRange(i, statusCol).setBackground("#C8E6C9"); // light green
        break;
      }
    }
  }

  // ---- Approved Users sheet mein entry karo ----
  let sheet = ss.getSheetByName(SHEET_APPROVED);
  if (!sheet) {
    sheet = ss.insertSheet(SHEET_APPROVED);
    sheet.appendRow([
      "Sr No", "Username", "Plan", "Approved On", "Expiry Date", "Days Left"
    ]);
    sheet.getRange(1, 1, 1, 6)
      .setBackground("#2196F3")
      .setFontColor("white")
      .setFontWeight("bold");
    sheet.setFrozenRows(1);
  }

  const lastRow = sheet.getLastRow();
  const expiryDate = data.expiry ? new Date(data.expiry) : "";
  const approvedDate = data.approved_at ? new Date(data.approved_at) : new Date();

  // Days left formula - dynamically calculate karta rahega
  const newRow = lastRow + 1;
  sheet.appendRow([
    lastRow, // sr no
    data.username || "",
    data.plan || "",
    formatDateTime(data.approved_at),
    expiryDate ? Utilities.formatDate(expiryDate, "Asia/Kolkata", "dd-MM-yyyy") : "",
    "" // Days Left - formula se
  ]);

  // Days Left mein formula dalo
  if (expiryDate) {
    const expiryCell = sheet.getRange(newRow, 5).getA1Notation();
    sheet.getRange(newRow, 6).setFormula(`=IF(${expiryCell}="","",DAYS(${expiryCell},TODAY()))`);
  }

  sheet.autoResizeColumns(1, 6);
}

// ============================================================
// FUNCTION 3: Naya Bill bana to (optional - backend mein call nahi hai abhi)
// ============================================================
function handleNewBill(data) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(SHEET_BILLS);

  if (!sheet) {
    sheet = ss.insertSheet(SHEET_BILLS);
    sheet.appendRow([
      "Sr No", "Date & Time", "Username", "Shop Name",
      "Bill No", "Customer", "Items Count", "Total (₹)", "Payment Mode"
    ]);
    sheet.getRange(1, 1, 1, 9)
      .setBackground("#FF9800")
      .setFontColor("white")
      .setFontWeight("bold");
    sheet.setFrozenRows(1);
  }

  const lastRow = sheet.getLastRow();
  sheet.appendRow([
    lastRow,
    formatDateTime(data.timestamp),
    data.username || "",
    data.shop_name || "",
    data.bill_no || "",
    data.customer || "Walk-in",
    data.items_count || 0,
    data.total || 0,
    data.payment_mode || "Cash"
  ]);

  sheet.autoResizeColumns(1, 9);
}

// ============================================================
// HELPER: Date/Time format karo India time mein
// ============================================================
function formatDateTime(isoString) {
  if (!isoString) return "";
  try {
    const date = new Date(isoString);
    return Utilities.formatDate(date, "Asia/Kolkata", "dd-MM-yyyy HH:mm:ss");
  } catch (e) {
    return isoString;
  }
}

// ============================================================
// TEST FUNCTION - Script editor mein run karke test karo
// ============================================================
function testScript() {
  // Test new registration
  handleNewRegistration({
    action: "new_registration",
    full_name: "Ramesh Kumar",
    username: "ramesh_shop",
    email: "ramesh@example.com",
    whatsapp: "9876543210",
    city: "Solapur",
    store_type: "grocery",
    plan: "monthly",
    plan_amount: 299,
    upi_used: "ramesh@upi",
    timestamp: new Date().toISOString()
  });

  // Test approval
  handleApproved({
    action: "approved",
    username: "ramesh_shop",
    plan: "monthly",
    approved_at: new Date().toISOString(),
    expiry: new Date(Date.now() + 30*24*60*60*1000).toISOString()
  });

  Logger.log("✅ Test completed! Check your Google Sheet.");
}

// ============================================================
// FRONTEND SYNC ACTIONS — App se aane wale requests
// ============================================================

// GET requests handle karo (ping, getProducts)
function doGet(e) {
  const action = e.parameter.action || 'ping';

  if (action === 'ping') {
    return ContentService
      .createTextOutput(JSON.stringify({ status: 'ok', message: 'GroceryPOS Google Apps Script ✅' }))
      .setMimeType(ContentService.MimeType.JSON);
  }

  if (action === 'getProducts') {
    return getProductsFromSheet();
  }

  if (action === 'getAnalytics') {
    return getAnalyticsFromSheet();
  }

  return ContentService
    .createTextOutput(JSON.stringify({ status: 'ok' }))
    .setMimeType(ContentService.MimeType.JSON);
}

// ── GET Products from Products sheet ──
function getProductsFromSheet() {
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const sheet = ss.getSheetByName('Products');
    if (!sheet) {
      return ContentService.createTextOutput(JSON.stringify({ products: [] }))
        .setMimeType(ContentService.MimeType.JSON);
    }
    const rows = sheet.getDataRange().getValues();
    if (rows.length < 2) {
      return ContentService.createTextOutput(JSON.stringify({ products: [] }))
        .setMimeType(ContentService.MimeType.JSON);
    }
    const headers = rows[0].map(h => String(h).trim().toLowerCase());
    const nameIdx = headers.findIndex(h => h.includes('name'));
    const priceIdx = headers.findIndex(h => h.includes('price'));
    const catIdx = headers.findIndex(h => h.includes('cat'));
    const bcIdx = headers.findIndex(h => h.includes('barcode') || h.includes('code'));
    const taxIdx = headers.findIndex(h => h.includes('tax'));
    const stockIdx = headers.findIndex(h => h.includes('stock') || h.includes('qty'));

    const products = [];
    for (let i = 1; i < rows.length; i++) {
      const r = rows[i];
      const name = r[nameIdx] ? String(r[nameIdx]).trim() : '';
      const price = parseFloat(r[priceIdx]) || 0;
      if (!name || price <= 0) continue;
      products.push({
        name,
        price,
        category: catIdx >= 0 ? (r[catIdx] || 'General') : 'General',
        barcode: bcIdx >= 0 ? String(r[bcIdx] || '') : '',
        tax: taxIdx >= 0 ? parseFloat(r[taxIdx]) || 0 : 0,
        stock: stockIdx >= 0 ? parseInt(r[stockIdx]) || 0 : 0,
      });
    }
    return ContentService.createTextOutput(JSON.stringify({ products }))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService.createTextOutput(JSON.stringify({ products: [], error: err.message }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

// ── POST: saveSale — invoice Sheet mein save karo ──
function handleSaveSale(data) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName('Sales');
  if (!sheet) {
    sheet = ss.insertSheet('Sales');
    sheet.appendRow(['Date', 'Bill No', 'Customer', 'Items', 'Subtotal', 'Tax', 'Discount', 'Total', 'Payment Mode', 'Username']);
    sheet.getRange(1, 1, 1, 10).setBackground('#2196F3').setFontColor('white').setFontWeight('bold');
    sheet.setFrozenRows(1);
  }
  const cart = data.cart || [];
  const itemsSummary = cart.map(i => `${i.name}×${i.quantity}`).join(', ');
  sheet.appendRow([
    formatDateTime(data.date || new Date().toISOString()),
    data.bill_number || '',
    data.customer_name || 'Walk-in',
    itemsSummary,
    parseFloat(data.subtotal || 0).toFixed(2),
    parseFloat(data.tax_total || 0).toFixed(2),
    parseFloat(data.discount || 0).toFixed(2),
    parseFloat(data.final_amount || 0).toFixed(2),
    data.payment_mode || 'Cash',
    data.username || '',
  ]);
  sheet.autoResizeColumns(1, 10);
}

// ── Analytics ──
function getAnalyticsFromSheet() {
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const sheet = ss.getSheetByName('Sales');
    if (!sheet || sheet.getLastRow() < 2) {
      return ContentService.createTextOutput(JSON.stringify({ today: 0, week: 0, month: 0 }))
        .setMimeType(ContentService.MimeType.JSON);
    }
    const rows = sheet.getDataRange().getValues();
    const now = new Date();
    let today = 0, week = 0, month = 0;
    for (let i = 1; i < rows.length; i++) {
      const dateStr = rows[i][0];
      const total = parseFloat(rows[i][7]) || 0;
      const d = new Date(dateStr);
      if (isNaN(d)) continue;
      const diff = (now - d) / (1000 * 60 * 60 * 24);
      if (diff < 1) today += total;
      if (diff < 7) week += total;
      month += total;
    }
    return ContentService.createTextOutput(JSON.stringify({ today, week, month }))
      .setMimeType(ContentService.MimeType.JSON);
  } catch(e) {
    return ContentService.createTextOutput(JSON.stringify({ error: e.message }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

// saveSale and syncCustomers are handled in doPost above via action === "saveSale"
