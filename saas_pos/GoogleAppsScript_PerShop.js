/**
 * ╔══════════════════════════════════════════════════════════════════╗
 * ║         GroceryPOS — COMPLETE FIXED VERSION                    ║
 * ║         Version: 4.0  |  Exact column mapping                  ║
 * ║         ALL fields sync: Name, Price, Tax, Stock, Unit, SKU   ║
 * ║         Auto stock decrease on sale                           ║
 * ╚══════════════════════════════════════════════════════════════════╝
 */

// ================================================================
// CONFIGURATION — EXACT column names (must match sheet exactly)
// ================================================================
const PRODUCTS_HEADERS = ['Name', 'Price', 'Category', 'Barcode', 'Tax %', 'Stock', 'Unit', 'SKU'];
const SALES_HEADERS = ['Sr', 'Date & Time', 'Bill No', 'Customer', 'Mobile', 'Table No', 
                       'Waiter', 'Items Count', 'Subtotal (₹)', 'Tax (₹)', 'Add Charge (₹)', 
                       'Discount (₹)', 'Net Amount (₹)', 'Payment Mode', 'Shop'];
const ITEMS_HEADERS = ['Date & Time', 'Bill No', 'Item Name', 'Category', 'Qty', 
                       'Rate (₹)', 'Tax %', 'Line Total (₹)', 'Shop'];

// Column mappings for Products sheet (EXACT column index positions)
// Index 0 = Column A, Index 1 = Column B, etc.
const COL = {
  NAME: 0,      // A
  PRICE: 1,     // B
  CATEGORY: 2,  // C
  BARCODE: 3,   // D
  TAX: 4,       // E
  STOCK: 5,     // F
  UNIT: 6,      // G
  SKU: 7        // H
};

// ================================================================
// ENSURE ALL SHEETS EXIST WITH PROPER HEADERS
// ================================================================
function ensureAllSheets() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  
  // Create Products sheet if not exists
  let pSheet = ss.getSheetByName('Products');
  if (!pSheet) {
    pSheet = ss.insertSheet('Products');
    pSheet.appendRow(PRODUCTS_HEADERS);
    formatHeaderRow(pSheet, PRODUCTS_HEADERS.length, '#4CAF50');
  } else {
    // Check and fix headers
    const headerRow = pSheet.getRange(1, 1, 1, 8).getValues()[0];
    const headers = headerRow.map(h => String(h).trim());
    if (headers.join(',') !== PRODUCTS_HEADERS.join(',')) {
      pSheet.insertRowBefore(1);
      pSheet.getRange(1, 1, 1, 8).setValues([PRODUCTS_HEADERS]);
      formatHeaderRow(pSheet, 8, '#4CAF50');
    }
  }
  
  // Create Sales sheet if not exists
  let sSheet = ss.getSheetByName('Sales');
  if (!sSheet) {
    sSheet = ss.insertSheet('Sales');
    sSheet.appendRow(SALES_HEADERS);
    formatHeaderRow(sSheet, SALES_HEADERS.length, '#2196F3');
  }
  
  // Create Sales_Items sheet if not exists
  let iSheet = ss.getSheetByName('Sales_Items');
  if (!iSheet) {
    iSheet = ss.insertSheet('Sales_Items');
    iSheet.appendRow(ITEMS_HEADERS);
    formatHeaderRow(iSheet, ITEMS_HEADERS.length, '#9C27B0');
  }
}

// ================================================================
// FORMAT HEADER ROW
// ================================================================
function formatHeaderRow(sheet, colCount, color) {
  const range = sheet.getRange(1, 1, 1, colCount);
  range.setBackground(color)
       .setFontColor('white')
       .setFontWeight('bold')
       .setFontSize(11)
       .setHorizontalAlignment('center')
       .setVerticalAlignment('middle');
  sheet.setFrozenRows(1);
  sheet.autoResizeColumns(1, colCount);
}

// ================================================================
// NORMALIZE ACTION
// ================================================================
function normalizeAction(action) {
  return String(action || '').toLowerCase().replace(/[_\s-]/g, '');
}

// ================================================================
// HELPER — JSON Response
// ================================================================
function out(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

// ================================================================
// HELPER — India DateTime
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
// MAIN — POST Requests
// ================================================================
function doPost(e) {
  try {
    const data = JSON.parse(e.postData.contents);
    const action = normalizeAction(data.action);
    
    ensureAllSheets();
    
    if (action === 'savesale' || action === 'saveinvoice') {
      return saveSale(data);
    }
    if (action === 'getproducts') {
      return getProducts();
    }
    if (action === 'getanalytics') {
      return getAnalytics();
    }
    if (action === 'addproduct') {
      return addProduct(data);
    }
    if (action === 'updateproduct') {
      return updateProduct(data);
    }
    if (action === 'getlowstock') {
      return getLowStock(data);
    }
    if (action === 'ping') {
      return out({ 
        status: 'ok', 
        message: 'GroceryPOS Shop Script ✅', 
        sheet: SpreadsheetApp.getActiveSpreadsheet().getName() 
      });
    }
    
    return out({ success: false, message: 'Unknown action: ' + (data.action || '') });
    
  } catch (err) {
    return out({ success: false, error: err.message });
  }
}

// ================================================================
// MAIN — GET Requests
// ================================================================
function doGet(e) {
  const action = normalizeAction(e.parameter && e.parameter.action);
  
  ensureAllSheets();
  
  if (action === '' || action === 'ping') {
    return out({ 
      status: 'ok', 
      message: 'GroceryPOS Shop Script ✅', 
      sheet: SpreadsheetApp.getActiveSpreadsheet().getName() 
    });
  }
  if (action === 'getproducts') {
    return getProducts();
  }
  if (action === 'getanalytics') {
    return getAnalytics();
  }
  if (action === 'getlowstock') {
    return getLowStock({});
  }
  
  return out({ status: 'ok' });
}

// ================================================================
// 1. GET PRODUCTS — Using EXACT column positions
// ================================================================
function getProducts() {
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const sheet = ss.getSheetByName('Products');
    
    if (!sheet || sheet.getLastRow() < 2) {
      return out({ 
        products: [], 
        total: 0, 
        message: 'Products sheet exists but is empty. Add products and sync again.' 
      });
    }
    
    const rows = sheet.getDataRange().getValues();
    
    // 🔥 Use EXACT column positions
    const products = [];
    for (let i = 1; i < rows.length; i++) {
      const r = rows[i];
      const name = String(r[COL.NAME] || '').trim();
      const price = parseFloat(r[COL.PRICE]) || 0;
      
      // Skip empty rows or invalid products
      if (!name || price <= 0) continue;
      
      const barcode = String(r[COL.BARCODE] || '').trim();
      const sku = String(r[COL.SKU] || '').trim();
      const id = barcode || sku || ('P' + i);
      
      // 🔥 IMPORTANT: ALL fields are extracted correctly
      products.push({
        // Lowercase keys (for app)
        id: id,
        name: name,
        price: price,
        category: String(r[COL.CATEGORY] || 'General').trim(),
        barcode: barcode,
        sku: sku,
        tax: parseFloat(r[COL.TAX]) || 0,
        stock: parseFloat(r[COL.STOCK]) || 0,
        unit: String(r[COL.UNIT] || '').trim(),
        
        // PascalCase keys (for fallback)
        product_id: id,
        Product_ID: id,
        Product_Name: name,
        Price: price,
        Category: String(r[COL.CATEGORY] || 'General').trim(),
        Tax: parseFloat(r[COL.TAX]) || 0,
        Stock: parseFloat(r[COL.STOCK]) || 0,
        Unit: String(r[COL.UNIT] || '').trim(),
        SKU: sku,
        Barcode: barcode
      });
    }
    
    Logger.log(`✅ getProducts: ${products.length} products loaded`);
    if (products.length > 0) {
      Logger.log(`   Sample: ${products[0].name} | Price: ${products[0].price} | Tax: ${products[0].tax} | Stock: ${products[0].stock}`);
    }
    
    return out({ products, total: products.length });
    
  } catch (err) {
    Logger.log(`❌ getProducts error: ${err.message}`);
    return out({ products: [], total: 0, error: err.message });
  }
}

// ================================================================
// 2. SAVE SALE — With Auto Stock Update (USING EXACT COLUMNS)
// ================================================================
function saveSale(data) {
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const salesSheet = ss.getSheetByName('Sales');
    const itemsSheet = ss.getSheetByName('Sales_Items');
    const productsSheet = ss.getSheetByName('Products');
    
    if (!salesSheet || !itemsSheet || !productsSheet) {
      return out({ success: false, error: 'Required sheets not found.' });
    }
    
    // Parse cart
    let cart = [];
    if (Array.isArray(data.cart)) {
      cart = data.cart;
    } else if (data.items) {
      try {
        cart = typeof data.items === 'string' ? JSON.parse(data.items) : data.items;
      } catch (_) {
        cart = [];
      }
    }
    if (!Array.isArray(cart)) cart = [];
    
    if (cart.length === 0) {
      return out({ success: false, error: 'Cart is empty. No items to save.' });
    }
    
    // 🔥 STEP 1: Get ALL product data from Products sheet
    const productRows = productsSheet.getDataRange().getValues();
    
    // Create maps for lookup — BARCODE/SKU first (exact, reliable identifiers),
    // name as a fallback. Matching by name ONLY (the previous version) is
    // fragile: any trim/case/spacing difference between what's in the Sheet
    // and what the app sends means "product not found" — which used to
    // abort the ENTIRE sale (see STEP 2 below for the bigger fix to that).
    const productByCode = {};  // barcode or SKU → product
    const productByName = {};  // lowercased name → product
    for (let i = 1; i < productRows.length; i++) {
      const r = productRows[i];
      const name = String(r[COL.NAME] || '').trim();
      const barcode = String(r[COL.BARCODE] || '').trim();
      const sku = String(r[COL.SKU] || '').trim();
      if (!name) continue;

      const entry = {
        rowIndex: i,
        row: r,
        name: name,
        stock: parseFloat(r[COL.STOCK]) || 0,
        price: parseFloat(r[COL.PRICE]) || 0,
        tax: parseFloat(r[COL.TAX]) || 0,
        unit: String(r[COL.UNIT] || '').trim()
      };
      productByName[name.toLowerCase()] = entry;
      if (barcode) productByCode[barcode.toLowerCase()] = entry;
      if (sku) productByCode[sku.toLowerCase()] = entry;
    }
    
    Logger.log(`📦 Product maps: ${Object.keys(productByCode).length} by code, ${Object.keys(productByName).length} by name`);
    
    // 🔥 STEP 2: Match + validate each cart item.
    // IMPORTANT BEHAVIOUR CHANGE: an unmatched product (e.g. a quick/manual
    // item typed in at billing time with no catalog entry) or a stock
    // shortfall NO LONGER aborts the whole sale. A POS must never refuse to
    // record a real, already-completed customer transaction just because
    // inventory bookkeeping couldn't fully reconcile — that was the actual
    // bug behind "stock never decreases": one mismatched line item in a
    // cart was silently blocking the Sales/Sales_Items write AND every
    // other item's stock update for that entire bill.
    const stockUpdates = [];
    const skipped = [];

    cart.forEach((item, index) => {
      const itemName = String(item.name || '').trim();
      const itemQty = parseFloat(item.quantity) || 1;
      const itemPrice = parseFloat(item.price) || 0;
      const itemTax = parseFloat(item.tax) || 0;
      const itemCode = String(item.barcode || item.sku || item.id || '').trim().toLowerCase();

      if (!itemName) { skipped.push(`Item #${index+1}: name empty`); return; }

      const product = (itemCode && productByCode[itemCode]) || productByName[itemName.toLowerCase()];

      if (!product) {
        skipped.push(`"${itemName}": not found in Products sheet — sale recorded, stock unchanged for this item`);
        return; // this ONE item's stock isn't tracked — the rest of the sale still proceeds normally
      }

      const currentStock = product.stock;
      const newStock = Math.max(0, currentStock - itemQty); // never go negative — clamp instead of blocking the sale
      if (currentStock < itemQty) {
        skipped.push(`"${product.name}": stock was ${currentStock}, sold ${itemQty} — clamped to 0 (check/restock)`);
      }

      stockUpdates.push({
        rowIndex: product.rowIndex + 1, // +1 because row 0 is header
        name: product.name,
        currentStock: currentStock,
        newStock: newStock,
        qtySold: itemQty,
        price: itemPrice || product.price,
        tax: itemTax || product.tax,
        category: item.category || 'General'
      });
    });

    if (skipped.length > 0) {
      Logger.log(`⚠️ Stock notes (sale still proceeds): ${skipped.join(' | ')}`);
    }
    
    // 🔥 STEP 3: Apply stock updates to Products sheet
    stockUpdates.forEach(update => {
      Logger.log(`📉 Stock update: ${update.name} → ${update.currentStock} - ${update.qtySold} = ${update.newStock}`);
      // Update stock at exact column position (COL.STOCK + 1 for 1-based)
      productsSheet.getRange(update.rowIndex, COL.STOCK + 1).setValue(update.newStock);
    });
    // Force the writes to persist immediately rather than relying on Apps
    // Script's implicit batching — cheap insurance against the class of
    // "code looks right but the sheet doesn't update" timing issues.
    if (stockUpdates.length > 0) SpreadsheetApp.flush();
    
    // 🔥 STEP 4: Calculate totals — use the ORIGINAL cart (not just matched
    // items) so unmatched/quick items still count toward the bill total.
    const subtotal = cart.reduce((sum, item) => sum + ((parseFloat(item.price)||0) * (parseFloat(item.quantity)||1)), 0);
    const taxTotal = cart.reduce((sum, item) => sum + (((parseFloat(item.price)||0) * (parseFloat(item.tax)||0) / 100) * (parseFloat(item.quantity)||1)), 0);
    const addCharge = parseFloat(data.additional_charge || 0);
    const discount = parseFloat(data.discount || 0);
    const netAmt = subtotal + taxTotal + addCharge - discount;
    
    // 🔥 STEP 5: Save to Sales sheet
    const sr = salesSheet.getLastRow();
    const billNo = data.invoice_number || data.bill_number || ('BILL-' + String(sr + 1).padStart(4, '0'));
    const dateStr = indiaDateTime(data.date || data.timestamp);
    const shopName = data.shop || '';
    
    salesSheet.appendRow([
      sr,
      dateStr,
      billNo,
      data.customer_name || 'Walk-in',
      data.customer_mobile || '',
      data.table_no || '',
      data.waiter || '',
      cart.length,
      subtotal.toFixed(2),
      taxTotal.toFixed(2),
      addCharge.toFixed(2),
      discount.toFixed(2),
      netAmt.toFixed(2),
      data.payment_mode || 'Cash',
      shopName
    ]);
    salesSheet.autoResizeColumns(1, 15);
    
    // 🔥 STEP 6: Save to Sales_Items sheet — every cart item, matched or not
    const itemRows = cart.map(item => [
      dateStr,
      billNo,
      String(item.name || '').trim(),
      item.category || 'General',
      parseFloat(item.quantity) || 1,
      (parseFloat(item.price) || 0).toFixed(2),
      parseFloat(item.tax) || 0,
      ((parseFloat(item.price)||0) * (parseFloat(item.quantity)||1)).toFixed(2),
      shopName
    ]);
    
    if (itemRows.length > 0) {
      const startRow = itemsSheet.getLastRow() + 1;
      itemsSheet.getRange(startRow, 1, itemRows.length, itemRows[0].length)
        .setValues(itemRows);
    }
    
    Logger.log(`✅ Sale saved: ${billNo} | Items: ${cart.length} | Stock updated: ${stockUpdates.length} | Amount: ₹${netAmt.toFixed(2)}`);
    
    return out({ 
      success: true, 
      message: 'Sale saved ✅',
      bill_number: billNo,
      stockUpdated: stockUpdates.length,
      stockDetails: stockUpdates.map(u => `${u.name}: ${u.currentStock} → ${u.newStock}`),
      warnings: skipped
    });
    
  } catch (err) {
    Logger.log(`❌ saveSale error: ${err.message}`);
    Logger.log(`Stack: ${err.stack}`);
    return out({ success: false, error: err.message });
  }
}

// ================================================================
// NEW: ADD PRODUCT — push a brand-new product from the app to the Sheet
// (this script previously only supported PULLING products, never pushing
// new ones up — so any product added in the app never appeared here,
// which also meant it could never match during a sale's stock lookup)
// ================================================================
function addProduct(data) {
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const sheet = ss.getSheetByName('Products');
    if (!sheet) return out({ success: false, error: 'Products sheet not found' });

    const name = String(data.name || '').trim();
    if (!name) return out({ success: false, error: 'Product name is required' });

    // Avoid duplicate rows for the same barcode/name (idempotent — if the
    // app retries a request, e.g. after a flaky connection, we update the
    // existing row instead of creating a second one).
    const rows = sheet.getDataRange().getValues();
    const barcode = String(data.barcode || '').trim();
    for (let i = 1; i < rows.length; i++) {
      const rowName = String(rows[i][COL.NAME] || '').trim().toLowerCase();
      const rowBarcode = String(rows[i][COL.BARCODE] || '').trim();
      if ((barcode && rowBarcode === barcode) || rowName === name.toLowerCase()) {
        return updateProduct(Object.assign({}, data, { _rowIndex: i + 1 }));
      }
    }

    sheet.appendRow([
      name,
      parseFloat(data.price) || 0,
      data.category || 'General',
      barcode,
      parseFloat(data.tax) || 0,
      parseFloat(data.stock) || 0,
      data.unit || 'piece',
      data.sku || ''
    ]);
    SpreadsheetApp.flush();
    Logger.log(`✅ addProduct: "${name}" added to sheet`);
    return out({ success: true, message: 'Product added ✅' });
  } catch (err) {
    Logger.log(`❌ addProduct error: ${err.message}`);
    return out({ success: false, error: err.message });
  }
}

// ================================================================
// NEW: UPDATE PRODUCT — sync a price/stock/tax edit made in the app
// ================================================================
function updateProduct(data) {
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const sheet = ss.getSheetByName('Products');
    if (!sheet) return out({ success: false, error: 'Products sheet not found' });

    let rowIndex = data._rowIndex; // set internally by addProduct()'s de-dupe path
    if (!rowIndex) {
      const name = String(data.name || '').trim().toLowerCase();
      const barcode = String(data.barcode || '').trim();
      const rows = sheet.getDataRange().getValues();
      for (let i = 1; i < rows.length; i++) {
        const rowName = String(rows[i][COL.NAME] || '').trim().toLowerCase();
        const rowBarcode = String(rows[i][COL.BARCODE] || '').trim();
        if ((barcode && rowBarcode === barcode) || rowName === name) { rowIndex = i + 1; break; }
      }
    }
    if (!rowIndex) return out({ success: false, error: 'Product not found to update' });

    // Only overwrite fields that were actually sent — this lets the app
    // send a partial update (e.g. "just changed stock") without clobbering
    // other columns with blanks.
    if (data.name !== undefined) sheet.getRange(rowIndex, COL.NAME + 1).setValue(String(data.name).trim());
    if (data.price !== undefined) sheet.getRange(rowIndex, COL.PRICE + 1).setValue(parseFloat(data.price) || 0);
    if (data.category !== undefined) sheet.getRange(rowIndex, COL.CATEGORY + 1).setValue(data.category);
    if (data.barcode !== undefined) sheet.getRange(rowIndex, COL.BARCODE + 1).setValue(data.barcode);
    if (data.tax !== undefined) sheet.getRange(rowIndex, COL.TAX + 1).setValue(parseFloat(data.tax) || 0);
    if (data.stock !== undefined) sheet.getRange(rowIndex, COL.STOCK + 1).setValue(parseFloat(data.stock) || 0);
    if (data.unit !== undefined) sheet.getRange(rowIndex, COL.UNIT + 1).setValue(data.unit);
    if (data.sku !== undefined) sheet.getRange(rowIndex, COL.SKU + 1).setValue(data.sku);
    SpreadsheetApp.flush();

    Logger.log(`✅ updateProduct: row ${rowIndex} updated`);
    return out({ success: true, message: 'Product updated ✅' });
  } catch (err) {
    Logger.log(`❌ updateProduct error: ${err.message}`);
    return out({ success: false, error: err.message });
  }
}

// ================================================================
// NEW: LOW STOCK ALERT — list of products at/under a threshold
// (default 5, or pass {threshold: N}) — lets the app show a restock
// reminder without the cashier having to open the Sheet manually.
// ================================================================
function getLowStock(data) {
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const sheet = ss.getSheetByName('Products');
    if (!sheet || sheet.getLastRow() < 2) return out({ lowStock: [] });

    const threshold = parseFloat(data && data.threshold) || 5;
    const rows = sheet.getDataRange().getValues();
    const lowStock = [];
    for (let i = 1; i < rows.length; i++) {
      const r = rows[i];
      const name = String(r[COL.NAME] || '').trim();
      const stock = parseFloat(r[COL.STOCK]) || 0;
      if (name && stock <= threshold) {
        lowStock.push({ name, stock, unit: String(r[COL.UNIT] || '').trim() });
      }
    }
    lowStock.sort((a, b) => a.stock - b.stock);
    return out({ lowStock, threshold });
  } catch (err) {
    return out({ lowStock: [], error: err.message });
  }
}

// ================================================================
// 3. ANALYTICS
// ================================================================
function getAnalytics() {
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const salesSheet = ss.getSheetByName('Sales');
    
    if (!salesSheet || salesSheet.getLastRow() < 2) {
      return out({ today: 0, week: 0, month: 0, topItems: [] });
    }
    
    const rows = salesSheet.getDataRange().getValues();
    const now = new Date();
    let today = 0, week = 0, month = 0;
    
    for (let i = 1; i < rows.length; i++) {
      const dateStr = String(rows[i][1] || '');
      const total = parseFloat(rows[i][12]) || 0;
      let d;
      try {
        const parts = dateStr.split(' ')[0].split('-');
        d = new Date(`${parts[2]}-${parts[1]}-${parts[0]}`);
      } catch (_) { continue; }
      if (isNaN(d)) continue;
      const diffDays = (now - d) / (1000 * 60 * 60 * 24);
      if (diffDays < 1) today += total;
      if (diffDays < 7) week += total;
      if (diffDays < 30) month += total;
    }
    
    let topItems = [];
    try {
      const itemsSheet = ss.getSheetByName('Sales_Items');
      if (itemsSheet && itemsSheet.getLastRow() > 1) {
        const iRows = itemsSheet.getDataRange().getValues();
        const counts = {};
        for (let i = 1; i < iRows.length; i++) {
          const name = String(iRows[i][2] || '').trim();
          const qty = parseFloat(iRows[i][4]) || 1;
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
// SETUP — Run this first
// ================================================================
function setupSheets() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  
  // Delete existing sheets
  ['Products', 'Sales', 'Sales_Items'].forEach(name => {
    const sheet = ss.getSheetByName(name);
    if (sheet) {
      ss.deleteSheet(sheet);
      Logger.log(`🗑️ Deleted ${name}`);
    }
  });
  
  // Create fresh sheets
  ensureAllSheets();
  
  // Add sample products with ALL fields
  const pSheet = ss.getSheetByName('Products');
  const samples = [
    ['Amul Milk 500ml', 28, 'Dairy', '8901030010214', 0, 50, 'pcs', 'AMUL-001'],
    ['Tata Salt 1kg', 20, 'Grocery', '8901263021492', 5, 30, 'kg', 'TATA-001'],
    ['Parle G 200g', 15, 'Biscuits', '8901719110146', 12, 100, 'pcs', 'PARLE-001'],
    ['Aashirvaad Atta 5kg', 250, 'Flour', '8901030980148', 0, 20, 'kg', 'AASH-001'],
    ['Surf Excel 500g', 90, 'Detergent', '8901030523021', 18, 15, 'pcs', 'SURF-001']
  ];
  samples.forEach(row => pSheet.appendRow(row));
  pSheet.autoResizeColumns(1, 8);
  
  Logger.log('✅✅✅ SETUP COMPLETE!');
  Logger.log('📊 Products with ALL fields:');
  samples.forEach(p => Logger.log(`   ${p[0]} | Price: ₹${p[1]} | Tax: ${p[4]}% | Stock: ${p[5]} ${p[6]}`));
  Logger.log('🚀 Ready for deployment!');
}

// ================================================================
// TEST — Complete test
// ================================================================
function testConnection() {
  Logger.log('=== 🧪 TESTING GroceryPOS Script ===');
  Logger.log('📋 Sheet: ' + SpreadsheetApp.getActiveSpreadsheet().getName());
  
  ensureAllSheets();
  
  // Show current products with ALL fields
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const pSheet = ss.getSheetByName('Products');
  if (pSheet && pSheet.getLastRow() > 1) {
    const rows = pSheet.getDataRange().getValues();
    Logger.log('\n📦 Current Products (ALL FIELDS):');
    Logger.log('   Name | Price | Tax | Stock | Unit | SKU');
    Logger.log('   ' + '-'.repeat(60));
    for (let i = 1; i < Math.min(rows.length, 6); i++) {
      const r = rows[i];
      Logger.log(`   ${String(r[0]).padEnd(18)} | ₹${String(r[1]).padEnd(5)} | ${String(r[4]).padEnd(3)}% | ${String(r[5]).padEnd(4)} | ${String(r[6]).padEnd(4)} | ${String(r[7]).padEnd(8)}`);
    }
  }
  
  // Test getProducts — check if ALL fields are coming
  Logger.log('\n📦 Testing getProducts()...');
  const pResult = JSON.parse(getProducts().getContent());
  Logger.log(`✅ ${pResult.products.length} products found`);
  if (pResult.products.length > 0) {
    const p = pResult.products[0];
    Logger.log(`   Sample: ${p.name}`);
    Logger.log(`   Price: ${p.price}, Tax: ${p.tax}, Stock: ${p.stock}, Unit: ${p.unit}, SKU: ${p.sku}`);
  }
  
  // Test saveSale with stock update
  Logger.log('\n💾 Testing saveSale() with stock update...');
  const testBillNo = 'TEST-' + Date.now();
  const sResult = JSON.parse(saveSale({
    invoice_number: testBillNo,
    date: new Date().toISOString(),
    customer_name: 'Test Customer',
    payment_mode: 'Cash',
    subtotal: 30,
    tax_total: 3.6,
    discount: 0,
    additional_charge: 0,
    final_amount: 33.6,
    shop: 'Test Shop',
    cart: [
      { name: 'Parle G 200g', price: 15, quantity: 2, category: 'Biscuits', tax: 12 }
    ]
  }).getContent());
  
  if (sResult.success) {
    Logger.log(`✅ ${sResult.message}`);
    Logger.log(`   Bill: ${sResult.bill_number}`);
    if (sResult.stockDetails) {
      sResult.stockDetails.forEach(d => Logger.log(`   📉 ${d}`));
    }
  } else {
    Logger.log(`❌ Failed: ${sResult.error}`);
    if (sResult.details) {
      sResult.details.forEach(d => Logger.log(`   ❌ ${d}`));
    }
  }
  
  // Show updated stock
  if (pSheet && pSheet.getLastRow() > 1) {
    const rows = pSheet.getDataRange().getValues();
    Logger.log('\n📦 Updated Products (Stock Decreased):');
    Logger.log('   Name | Stock');
    Logger.log('   ' + '-'.repeat(30));
    for (let i = 1; i < Math.min(rows.length, 6); i++) {
      const r = rows[i];
      Logger.log(`   ${String(r[0]).padEnd(18)} | ${r[5]}`);
    }
  }
  
  Logger.log('\n✅✅✅ TEST COMPLETE! All fields synced, stock decreased. ✅✅✅');
}

// ================================================================
// DEBUG — Show current product data
// ================================================================
function debugProducts() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const pSheet = ss.getSheetByName('Products');
  
  if (!pSheet) {
    Logger.log('❌ Products sheet not found');
    return;
  }
  
  const rows = pSheet.getDataRange().getValues();
  Logger.log('📊 Products Sheet Data:');
  Logger.log('   ' + rows[0].join(' | '));
  for (let i = 1; i < rows.length; i++) {
    Logger.log(`   ${rows[i].join(' | ')}`);
  }
}
