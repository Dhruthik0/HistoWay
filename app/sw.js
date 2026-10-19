// App shell: network first (so updates from the laptop show up), cache when offline.
// Packs (pack.json + MP3s): cache first; app.js stores them on "Download for offline".
// transformers.js for offline Whisper: cache first (the URL is version-pinned), in its own cache that survives
// shell updates. It keeps the model and onnxruntime files in its own 'transformers-cache'.
const SHELL = 'shell-v3';
const FILES = ['./', 'index.html', 'app.js', 'engine.js', 'live.js', 'stt.js', 'ask.js', 'manifest.webmanifest', 'icon.svg'];
const LIB = 'https://cdn.jsdelivr.net/npm/@huggingface/transformers@4.2.0/';

self.addEventListener('install', e => e.waitUntil(caches.open(SHELL).then(c => c.addAll(FILES)).then(() => self.skipWaiting())));
self.addEventListener('activate', e => e.waitUntil(caches.keys()
  .then(keys => Promise.all(keys.filter(k => k.startsWith('shell-') && k !== SHELL).map(k => caches.delete(k))))
  .then(() => self.clients.claim())));

self.addEventListener('fetch', e => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method === 'GET' && req.url.startsWith(LIB)) {
    e.respondWith(caches.match(req).then(hit => hit || fetch(req).then(res => {
      if (res.ok) { const copy = res.clone(); caches.open('lib-transformers-4.2.0').then(c => c.put(req, copy)); }
      return res;
    })));
    return;
  }
  if (req.method !== 'GET' || url.origin !== location.origin) return;  // Wikipedia and the Worker go straight to the network
  const path = url.pathname;
  if (path.includes('/packs/') && !path.endsWith('/index.json')) {
    e.respondWith(caches.match(req).then(hit => hit || fetch(req)));
    return;
  }
  e.respondWith(fetch(req).then(res => {
    if (res.ok && !path.endsWith('/index.json')) { const copy = res.clone(); caches.open(SHELL).then(c => c.put(req, copy)); }
    return res;
  }).catch(() => caches.match(req, { ignoreSearch: true }).then(hit => hit || Response.error())));
});
