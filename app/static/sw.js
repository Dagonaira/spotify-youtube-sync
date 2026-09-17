// Minimal service worker: makes the app installable on Android and lets the
// shell (HTML/CSS/JS/icons) load instantly on repeat visits. Deliberately
// does NOT touch /api/* - job status and account state must always come
// straight from the network, never from a stale cache.
//
// Bump CACHE_NAME whenever index.html/app.js/styles.css change - browsers
// only re-run install() (which refills the cache) when sw.js's own bytes
// change, so an update to the shell that doesn't also touch this file would
// otherwise stay cached indefinitely on an already-installed phone.
const CACHE_NAME = "crossplay-shell-v12";
const SHELL_ASSETS = ["/", "/index.html", "/styles.css", "/app.js", "/favicon.png", "/manifest.json"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(SHELL_ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.pathname.startsWith("/api/") || event.request.method !== "GET") return;

  event.respondWith(
    caches.match(event.request).then((cached) => {
      const network = fetch(event.request)
        .then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
          }
          return response;
        })
        .catch(() => cached);
      return cached || network;
    })
  );
});
