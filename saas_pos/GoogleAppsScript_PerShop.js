/**
 * ╔══════════════════════════════════════════════════════════════════╗
 * ║         GroceryPOS — Per-Shop Google Apps Script                ║
 * ║         Version: 1.0  |  For: Individual Shop Owners           ║
 * ╠══════════════════════════════════════════════════════════════════╣
 * ║                                                                  ║
 * ║  YE FILE SIRF SHOP OWNER KE APNE GOOGLE SHEET KE LIYE HAI.     ║
 * ║  Admin ke Auth Sheet se iska koi lena dena nahi hai.            ║
 * ║                                                                  ║
 * ║  Is Script se ye kaam hote hain:                                ║
 * ║   1. Products sheet se rates/products POS app mein load karo    ║
 * ║   2. Har sale/bill automatically Sales sheet mein save ho       ║
 * ║   3. Item-wise sales analytics Sales_Items sheet mein           ║
 * ║   4. App se connection check (ping)                             ║
 * ║                                                                  ║
 * ╠══════════════════════════════════════════════════════════════════╣
 * ║  SETUP — Sirf 5 steps (ek baar karna hai):                     ║
 * ║                                                                  ║
 * ║  Step 1: Apna Google Sheet kholo (ya naya banao)                ║
 * ║  Step 2: Extensions → Apps Script                               ║
 * ║  Step 3: Ye poora code paste karo (purana sab delete karke)     ║
 * ║  Step 4: Deploy → New Deployment → Web App                      ║
 * ║           Execute as : Me                                        ║
 * ║           Who has access : Anyone                               ║
 * ║           → Deploy → Copy the URL                               ║
 * ║  Step 5: POS App → Settings → Cloud tab → GAS URL mein paste   ║
 * ║                                                                  ║
 * ║  ⚠️  Jab bhi code change karo, naya deployment banana padega:  ║
 * ║       Deploy → Manage Deployments → Edit → New Version → Deploy ║
 * ╚══════════════════════════════════════════════════════════════════╝
 */

// ================================================================
// SHEET NAMES — Agar Sheet ka naam change karna ho to yahan karo
// ================================================================
const PRODUCTS_SHEET = 'Products';   // Product catalog (name, price, barcode etc.)
const SALES_SHEET    = 'Sales';      // Har completed bill ka summary record
const ITEMS_SHEET    = 'Sales_Items'; // Har bill ke individual items (analytics ke liye)


// ================================================================
// MAIN ENTRY — POST requests (App → Sheet likhna)
// ================================================================
function doPost(e) {
  try {
    const data   = JSON.parse(e.postData.contents);
    const action = data.action || '';

    if (action === 'saveSale' || action === 'save_invoice') {
      return saveSale(data);
    }

    if (action === 'ping') {
      return out({ status: 'ok', message: 'GroceryPOS Shop Script ✅', sheet: SpreadsheetApp.getActiveSpreadsheet().getName() });
    }

    return out({ success: false, message: 'Unknown action: ' + action });

  } catch (err) {
    return out({ success: false, error: err.message });
  }
}


// ================================================================
// MAIN ENTRY — GET requests (App → Sheet se padhna)
// ================================================================
function doGet(e) {
  const action = (e.parameter && e.parameter.action) || 'ping';

  if (action === 'ping') {
    return out({ status: 'ok', message: 'GroceryPOS Shop Script ✅', sheet: SpreadsheetApp.getActiveSpreadsheet().getName() });
  }

  if (action === 'getProducts') {
    return getProducts();
  }

  if (action === 'getAnalytics') {
    return getAnalytics();
  }

  return out({ status: 'ok' });
}


// ================================================================
// HELPER — JSON response banao
// ================================================================
function out(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}


// ================================================================
// HELPER — Date/Time India format mein
// ================================================================
function indiaDateTime(isoString) {
  try {
    const d = isoString ? new Date(isoString) : new Date();
    return Utilities.formatDate(d, 'Asia/Kolkata', 'dd-MM-yyyy HH:mm:ss');
  } catch (_) {
    return isoString || '';
  }
}


// ================================================================
// HELPER — Sheet getOrCreate (auto-create with headers if missing)
// ================================================================
function getOrCreateSheet(name, headers, headerColor) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(name);
  if (!sheet) {
    sheet = ss.insertSheet(name);
    sheet.appendRow(headers);
    sheet.getRange(1, 1, 1, headers.length)
      .setBackground(headerColor || '#4CAF50')
      .setFontColor('white')
      .setFontWeight('bold');
    sheet.setFrozenRows(1);
    sheet.autoResizeColumns(1, headers.length);
  }
  return sheet;
}


// ================================================================
// 1. GET PRODUCTS — Products sheet se catalog fetch karo
// ================================================================
// Sheet format expected (header row must have these column names —
// exact name nahi chahiye, sirf ye keywords ho toh chalega):
//   name / item / product  →  product name
//   price / rate / mrp     →  selling price (₹)
//   category / cat / type  →  category (optional)
//   barcode / code / sku   →  barcode (optional)
//   tax / gst / vat        →  tax % (optional)
//   stock / qty / quantity →  stock count (optional)
//   unit / uom             →  unit e.g. kg, pcs (optional)
// ================================================================
function getProducts() {
  try {
    const ss    = SpreadsheetApp.getActiveSpreadsheet();
    const sheet = ss.getSheetByName(PRODUCTS_SHEET);

    if (!sheet || sheet.getLastRow() < 2) {
      return out({ products: [], message: `"${PRODUCTS_SHEET}" sheet nahi mili ya khali hai. Pehle products add karo.` });
    }

    const rows    = sheet.getDataRange().getValues();
    const headers = rows[0].map(h => String(h).trim().toLowerCase());

    // Column index finder — keywords se dhundho
    function col(keywords) {
      return headers.findIndex(h => keywords.some(k => h.includes(k)));
    }

    const nameIdx  = col(['name', 'item', 'product', 'description']);
    const priceIdx = col(['price', 'rate', 'mrp', 'selling']);
    const catIdx   = col(['category', 'cat', 'type', 'group']);
    const bcIdx    = col(['barcode', 'code', 'sku', 'ean']);
    const taxIdx   = col(['tax', 'gst', 'vat', 'igst']);
    const stockIdx = col(['stock', 'qty', 'quantity', 'available']);
    const unitIdx  = col(['unit', 'uom', 'measure']);

    if (nameIdx === -1 || priceIdx === -1) {
      return out({
        products: [],
        message: `Products sheet mein "Name" aur "Price" columns hone chahiye. ` +
                 `Current headers: ${headers.join(', ')}`
      });
    }

    const products = [];
    for (let i = 1; i < rows.length; i++) {
      const r     = rows[i];
      const name  = r[nameIdx] ? String(r[nameIdx]).trim() : '';
      const price = parseFloat(r[priceIdx]) || 0;
      if (!name || price <= 0) continue;   // blank/invalid rows skip

      products.push({
        name,
        price,
        category : catIdx   >= 0 ? String(r[catIdx]   || 'General').trim() : 'General',
        barcode  : bcIdx    >= 0 ? String(r[bcIdx]    || '').trim()        : '',
        tax      : taxIdx   >= 0 ? parseFloat(r[taxIdx])   || 0            : 0,
        stock    : stockIdx >= 0 ? parseInt(r[stockIdx])   || 0            : 0,
        unit     : unitIdx  >= 0 ? String(r[unitIdx]  || '').trim()        : '',
      });
    }

    return out({ products, total: products.length });

  } catch (err) {
    return out({ products: [], error: err.message });
  }
}


// ================================================================
// 2. SAVE SALE — Completed bill Sales sheet mein save karo
//    + individual items Sales_Items sheet mein
// ================================================================
function saveSale(data) {
  try {
    // ── Summary row in Sales sheet ──────────────────────────────
    const salesSheet = getOrCreateSheet(SALES_SHEET, [
      'Sr', 'Date & Time', 'Bill No', 'Customer', 'Mobile',
      'Table No', 'Waiter', 'Items Count', 'Subtotal (₹)',
      'Tax (₹)', 'Add Charge (₹)', 'Discount (₹)',
      'Net Amount (₹)', 'Payment Mode', 'Shop'
    ], '#2196F3');

    const sr         = salesSheet.getLastRow();  // auto Sr No
    const cart       = Array.isArray(data.cart) ? data.cart :
                       (data.items ? JSON.parse(data.items) : []);
    const itemsCount = cart.length;
    const subtotal   = parseFloat(data.subtotal    || 0);
    const tax        = parseFloat(data.tax_total   || 0);
    const addCharge  = parseFloat(data.additional_charge || 0);
    const discount   = parseFloat(data.discount    || 0);
    const netAmt     = parseFloat(data.final_amount || subtotal + tax + addCharge - discount);

    salesSheet.appendRow([
      sr,
      indiaDateTime(data.date || data.timestamp),
      data.invoice_number || data.bill_number || '',
      data.customer_name  || 'Walk-in',
      data.customer_mobile || '',
      data.table_no       || '',
      data.waiter         || '',
      itemsCount,
      subtotal.toFixed(2),
      tax.toFixed(2),
      addCharge.toFixed(2),
      discount.toFixed(2),
      netAmt.toFixed(2),
      data.payment_mode   || 'Cash',
      data.shop           || '',
    ]);
    salesSheet.autoResizeColumns(1, 15);

    // ── Item-wise rows in Sales_Items sheet ─────────────────────
    if (cart.length > 0) {
      const itemsSheet = getOrCreateSheet(ITEMS_SHEET, [
        'Date & Time', 'Bill No', 'Item Name', 'Category',
        'Qty', 'Rate (₹)', 'Tax %', 'Line Total (₹)', 'Shop'
      ], '#9C27B0');

      const billNo   = data.invoice_number || data.bill_number || '';
      const dateStr  = indiaDateTime(data.date || data.timestamp);
      const shopName = data.shop || '';

      const itemRows = cart.map(item => [
        dateStr,
        billNo,
        item.name      || '',
        item.category  || 'General',
        item.quantity  || 1,
        parseFloat(item.price || 0).toFixed(2),
        parseFloat(item.tax   || 0),
        (parseFloat(item.price || 0) * (item.quantity || 1)).toFixed(2),
        shopName,
      ]);

      // Batch append — faster than one-by-one
      if (itemRows.length > 0) {
        const startRow = itemsSheet.getLastRow() + 1;
        itemsSheet.getRange(startRow, 1, itemRows.length, itemRows[0].length)
          .setValues(itemRows);
      }
    }

    return out({ success: true, message: 'Sale saved ✅' });

  } catch (err) {
    return out({ success: false, error: err.message });
  }
}


// ================================================================
// 3. ANALYTICS — Sales sheet se totals nikaalo
//    Returns: today, week, month totals + top 5 selling items
// ================================================================
function getAnalytics() {
  try {
    const ss    = SpreadsheetApp.getActiveSpreadsheet();
    const sheet = ss.getSheetByName(SALES_SHEET);

    if (!sheet || sheet.getLastRow() < 2) {
      return out({ today: 0, week: 0, month: 0, topItems: [] });
    }

    const rows = sheet.getDataRange().getValues();
    const now  = new Date();
    let today = 0, week = 0, month = 0;

    // Net Amount is column index 12 (0-based), Date is index 1
    for (let i = 1; i < rows.length; i++) {
      const dateStr = String(rows[i][1] || '');
      const total   = parseFloat(rows[i][12]) || 0;
      // Parse "dd-MM-yyyy HH:mm:ss" → Date
      let d;
      try {
        const parts = dateStr.split(' ')[0].split('-');
        d = new Date(`${parts[2]}-${parts[1]}-${parts[0]}`);
      } catch (_) { continue; }
      if (isNaN(d)) continue;
      const diffDays = (now - d) / (1000 * 60 * 60 * 24);
      if (diffDays < 1)  today += total;
      if (diffDays < 7)  week  += total;
      if (diffDays < 30) month += total;
    }

    // Top 5 items from Sales_Items sheet
    let topItems = [];
    try {
      const iSheet = ss.getSheetByName(ITEMS_SHEET);
      if (iSheet && iSheet.getLastRow() > 1) {
        const iRows = iSheet.getDataRange().getValues();
        const counts = {};
        // Item name is col 2 (0-based), Qty is col 4
        for (let i = 1; i < iRows.length; i++) {
          const name = String(iRows[i][2] || '').trim();
          const qty  = parseFloat(iRows[i][4]) || 1;
          if (!name) continue;
          counts[name] = (counts[name] || 0) + qty;
        }
        topItems = Object.entries(counts)
          .sort((a, b) => b[1] - a[1])
          .slice(0, 5)
          .map(([name, qty]) => ({ name, qty }));
      }
    } catch (_) {}

    return out({ today, week, month, topItems });

  } catch (err) {
    return out({ today: 0, week: 0, month: 0, error: err.message });
  }
}


// ================================================================
// SETUP HELPER — Ye function pehli baar run karo:
//   Script Editor → Run → setupSheets
//   Ye Products, Sales, Sales_Items sheets bana dega sample data ke saath
// ================================================================
function setupSheets() {
  // Products sheet with sample data
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  // Products
  let pSheet = ss.getSheetByName(PRODUCTS_SHEET);
  if (!pSheet) {
    pSheet = ss.insertSheet(PRODUCTS_SHEET);
    pSheet.appendRow(['Name', 'Price', 'Category', 'Barcode', 'Tax %', 'Stock', 'Unit']);
    pSheet.getRange(1, 1, 1, 7).setBackground('#4CAF50').setFontColor('white').setFontWeight('bold');
    pSheet.setFrozenRows(1);

    // Sample products
    const samples = [
      ['Amul Milk 500ml',   28,   'Dairy',     '8901030010214', 0,   50,  'pcs'],
      ['Tata Salt 1kg',     20,   'Grocery',   '8901263021492', 5,   30,  'kg'],
      ['Parle G 200g',      15,   'Biscuits',  '8901719110146', 12,  100, 'pcs'],
      ['Aashirvaad Atta 5kg', 250,'Flour',     '8901030980148', 0,   20,  'kg'],
      ['Surf Excel 500g',   90,   'Detergent', '8901030523021', 18,  15,  'pcs'],
    ];
    samples.forEach(row => pSheet.appendRow(row));
    pSheet.autoResizeColumns(1, 7);
    Logger.log('✅ Products sheet created with 5 sample products.');
  } else {
    Logger.log('ℹ️  Products sheet already exists — skipped.');
  }

  // Sales sheet (empty, auto-created when first sale happens)
  getOrCreateSheet(SALES_SHEET,  [
    'Sr', 'Date & Time', 'Bill No', 'Customer', 'Mobile',
    'Table No', 'Waiter', 'Items Count', 'Subtotal (₹)',
    'Tax (₹)', 'Add Charge (₹)', 'Discount (₹)',
    'Net Amount (₹)', 'Payment Mode', 'Shop'
  ], '#2196F3');

  getOrCreateSheet(ITEMS_SHEET, [
    'Date & Time', 'Bill No', 'Item Name', 'Category',
    'Qty', 'Rate (₹)', 'Tax %', 'Line Total (₹)', 'Shop'
  ], '#9C27B0');

  Logger.log('✅ Setup complete! Sheets ready: Products, Sales, Sales_Items.');
  Logger.log('👉 Next step: Deploy as Web App and copy the URL into POS Settings → Cloud.');
}


// ================================================================
// TEST — Script Editor → Run → testConnection
//   Verify karein ki script properly kaam kar raha hai deploy se pehle
// ================================================================
function testConnection() {
  Logger.log('=== GroceryPOS Per-Shop Script — Connection Test ===');
  Logger.log('Sheet name: ' + SpreadsheetApp.getActiveSpreadsheet().getName());

  // Test getProducts
  const pResult = JSON.parse(getProducts().getContent());
  Logger.log('Products found: ' + pResult.products.length);
  if (pResult.products.length > 0) {
    Logger.log('First product: ' + JSON.stringify(pResult.products[0]));
  }

  // Test saveSale (dry run with dummy data)
  const sResult = JSON.parse(saveSale({
    bill_number   : 'TEST-001',
    date          : new Date().toISOString(),
    customer_name : 'Test Customer',
    payment_mode  : 'Cash',
    subtotal      : 100,
    tax_total     : 5,
    discount      : 0,
    additional_charge: 0,
    final_amount  : 105,
    shop          : 'Test Shop',
    cart: [{ name: 'Test Item', price: 100, quantity: 1, category: 'General', tax: 5 }]
  }).getContent());
  Logger.log('saveSale test: ' + JSON.stringify(sResult));

  Logger.log('✅ Test complete! Check Sales and Sales_Items sheets for the test row.');
}
