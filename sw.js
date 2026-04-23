const CACHE_NAME = 'dentalos-v1';
const ASSETS = [
  '/',
  '/index.html',
  'https://unpkg.com/dexie/dist/dexie.js',
  'https://cdn.tailwindcss.com'
];

// تثبيت ملفات الواجهة في الذاكرة
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS))
  );
});

// استدعاء الملفات من الذاكرة عند فقدان الإنترنت
self.addEventListener('fetch', (event) => {
  event.respondWith(
    caches.match(event.request).then((response) => {
      return response || fetch(event.request);
    })
  );
});
