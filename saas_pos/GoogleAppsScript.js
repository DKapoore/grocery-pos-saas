/**
 * GroceryPOS SaaS - Google Apps Script
 * 
 * SETUP INSTRUCTIONS:
 * 1. Google Sheets kholo → Extensions → Apps Script
 * 2. Ye poora code paste karo
 * 3. Deploy → New Deployment → Web App
 * 4. "Who has access" → Anyone
 * 5. Deploy karo aur URL copy karo
 * 6. Wo URL .env mein GAS_WEBHOOK_URL mein daalo
 */

// ============================================================
// SHEET NAMES - Agar change karna ho to yahan se karo
// ============================================================
const SHEET_REGISTRATIONS = "Registrations";
const SHEET_APPROVED      = "Approved Users";
const SHEET_BILLS         = "Bills";

// ============================================================
// MAIN ENTRY POINT - Ye function POST request receive karta hai
// ============================================================
function doPost(e) {
  try {
    const data = JSON.parse(e.postData.contents);
    const action = data.action;

    if (action === "new_registration") {
      handleNewRegistration(data);
    } else if (action === "approved") {
      handleApproved(data);
    } else if (action === "new_bill") {
      handleNewBill(data);
    } else if (action === "saveSale") {
      handleSaveSale(data);
    }
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
