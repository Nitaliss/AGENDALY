// Tapunto Service Worker v5.0
// © 2026 Alicia Prats · tapunto.app
const CACHE_NAME = 'tapunto-v5.0';
const ASSETS = [
  '/',
  '/index.html',
  '/manifest.json',
  '/icono-72.png',
  '/icono-96.png',
  '/icono-128.png',
  '/icono-144.png',
  '/icono-152.png',
  '/icono-192.png',
  '/icono-384.png',
  '/icono-512.png',
];
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE_NAME)
      .then(c => c.addAll(ASSETS))
      .then(() => self.skipWaiting())
  );
});
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});
self.addEventListener('fetch', e => {
  if (e.request.url.includes('googleapis') ||
      e.request.url.includes('generativelanguage') ||
      e.request.url.includes('script.google') ||
      e.request.url.includes('wa.me') ||
      e.request.url.includes('calendar.google') ||
      e.request.url.includes('mail.google') ||
      e.request.url.includes('fonts.gstatic')) return;
  e.respondWith(
    fetch(e.request)
      .then(res => {
        if (!res || res.status !== 200 || res.type === 'opaque') return res;
        const clone = res.clone();
        caches.open(CACHE_NAME).then(c => c.put(e.request, clone));
        return res;
      })
      .catch(() => caches.match(e.request)
        .then(cached => cached || caches.match('/index.html'))
      )
  );
});
self.addEventListener('message', e => {
  if (e.data && e.data.type === 'SKIP_WAITING') self.skipWaiting();
});
