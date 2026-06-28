// ══════════════════════════════════════════════════
// Tapunto Service Worker v2.0
// © 2026 Alicia Prats · tapunto.app
// ══════════════════════════════════════════════════

const CACHE_NAME = 'tapunto-v2';
const CACHE_FONTS = 'tapunto-fonts-v1';

// Ficheros que se cachean en la instalación (app shell)
const STATIC_ASSETS = [
  '/',
  '/index.html',
  '/manifest.json',
  '/icon-192.png',
  '/icon-512.png',
];

// Fuentes de Google que se cachean al primer uso
const FONT_DOMAINS = [
  'fonts.googleapis.com',
  'fonts.gstatic.com',
];

// ── Instalación: cachear app shell ──
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(STATIC_ASSETS))
      .then(() => self.skipWaiting())
  );
});

// ── Activación: limpiar cachés viejas ──
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(k => k !== CACHE_NAME && k !== CACHE_FONTS)
          .map(k => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

// ── Fetch: estrategia según tipo de recurso ──
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // Fuentes: Cache First (duran mucho)
  if (FONT_DOMAINS.some(d => url.hostname.includes(d))) {
    event.respondWith(
      caches.open(CACHE_FONTS).then(cache =>
        cache.match(event.request).then(cached => {
          if (cached) return cached;
          return fetch(event.request).then(response => {
            if (response && response.status === 200) {
              cache.put(event.request, response.clone());
            }
            return response;
          });
        })
      )
    );
    return;
  }

  // API de Google Drive / OAuth: Network Only (no cachear nunca)
  if (url.hostname.includes('googleapis.com') || 
      url.hostname.includes('accounts.google.com')) {
    event.respondWith(fetch(event.request).catch(() =>
      new Response('{"error":"offline"}', { 
        headers: { 'Content-Type': 'application/json' } 
      })
    ));
    return;
  }

  // App shell (HTML, manifest, iconos): Network First con fallback a caché
  if (url.origin === self.location.origin) {
    event.respondWith(
      fetch(event.request)
        .then(response => {
          if (response && response.status === 200) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then(cache => 
              cache.put(event.request, clone)
            );
          }
          return response;
        })
        .catch(() => caches.match(event.request)
          .then(cached => cached || caches.match('/index.html'))
        )
    );
    return;
  }

  // Resto: network normal
  event.respondWith(fetch(event.request).catch(() => 
    caches.match(event.request)
  ));
});

// ── Push notifications ──
self.addEventListener('push', event => {
  if (!event.data) return;
  let data = {};
  try { data = event.data.json(); } catch(e) { 
    data = { title: 'Tapunto', body: event.data.text() }; 
  }
  event.waitUntil(
    self.registration.showNotification(data.title || 'Tapunto', {
      body: data.body || '',
      icon: '/icon-192.png',
      badge: '/icon-96.png',
      tag: data.tag || 'tapunto-notif',
      data: data.url || '/',
      vibrate: [200, 100, 200],
    })
  );
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil(
    clients.matchAll({ type: 'window' }).then(list => {
      for (const client of list) {
        if (client.url.includes(self.location.origin) && 'focus' in client)
          return client.focus();
      }
      return clients.openWindow(event.notification.data || '/');
    })
  );
});
