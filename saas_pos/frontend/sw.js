// GroceryPOS service worker — minimal, required for the app to qualify as
// an installable PWA (Chrome/Android will not show the install prompt, and
// PWABuilder/Bubblewrap will not accept the site as a valid TWA source,
// without an active service worker). This deliberately does NOT cache
// aggressively — POS billing data must always be fresh — it only enables
// installability and a basic offline fallback shell.
const CACHE_NAME = 'grocerypos-shell-v1';
const SHELL_FILES = ['./app.html'];

self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_FILES).catch(() => {}))
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Network-first — always try live data; only fall back to the cached shell
// if the device is genuinely offline (billing app must never show stale
// prices/stock when a connection is actually available).
self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;
  event.respondWith(
    fetch(event.request).catch(() => caches.match(event.request).then((r) => r || caches.match('./app.html')))
  );
});
