// App shell: network first (so updates from the laptop show up), cache when offline.
// Packs (pack.json + MP3s): cache first; app.js stores them on "Download for offline".
const SHELL = 'shell-v1';
const FILES = ['./', 'index.html', 'app.js', 'engine.js', 'manifest.webmanifest', 'icon.svg'];

self.addEventListener('install', e => e.waitUntil(caches.open(SHELL).then(c => c.addAll(FILES)).then(() => self.skipWaiting())));
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const path = new URL(req.url).pathname;
  if (path.includes('/packs/') && !path.endsWith('/index.json')) {
    e.respondWith(caches.match(req).then(hit => hit || fetch(req)));
    return;
  }
  e.respondWith(fetch(req).then(res => {
    if (res.ok && !path.endsWith('/index.json')) { const copy = res.clone(); caches.open(SHELL).then(c => c.put(req, copy)); }
    return res;
  }).catch(() => caches.match(req, { ignoreSearch: true }).then(hit => hit || Response.error())));
});
