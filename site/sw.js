// Офлайн-кэш: сначала сеть (чтобы обновления доезжали), при её отсутствии кэш.
const CACHE = 'universal-v2';
const FILES = ['./', 'index.html', 'manifest.webmanifest', 'vendor/qrcode.js', 'vendor/pdf-lib.min.js',
  'vendor/jszip.min.js', 'img/icon-192.png', 'img/icon-512.png'];
self.addEventListener('install', e => e.waitUntil(caches.open(CACHE).then(c => c.addAll(FILES)).then(() => self.skipWaiting())));
self.addEventListener('activate', e => e.waitUntil(
  caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())));
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET' || new URL(e.request.url).origin !== location.origin) return;
  e.respondWith(fetch(e.request).then(r => {
    const copy = r.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); return r;
  }).catch(() => caches.match(e.request)));
});
