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
// GET request ke liye (testing ke liye)
// ============================================================
function doGet(e) {
  return ContentService
    .createTextOutput(JSON.stringify({ status: "GroceryPOS Google Apps Script is Running ✅" }))
    .setMimeType(ContentService.MimeType.JSON);
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
