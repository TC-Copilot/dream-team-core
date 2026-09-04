// Service worker for The Dream Team PWA.
// Everything here is local-first: this app only ever talks to 127.0.0.1, so caching is purely
// about instant loads and a friendly offline page - never about hiding a network dependency.
//
// Bump CACHE_VERSION whenever a static asset changes so the activate step evicts old caches
// instead of serving stale HTML/JS forever.
const CACHE_VERSION = "v13";
const CACHE_PREFIX = "dream-team-";
const PRECACHE = `dream-team-precache-${CACHE_VERSION}`;
const RUNTIME = `dream-team-runtime-${CACHE_VERSION}`;
const CURRENT_CACHES = [PRECACHE, RUNTIME];

const PRECACHE_URLS = [
  "/",
  "/index.html",
  "/activity-log.html",
  "/architecture.html",
  "/ledger.html",
  "/metric-detail.html",
  "/operating-loop.html",
  "/ooo-register.html",
  "/ooo-timeline.js",
  "/results-history.html",
  "/offline.html",
  "/styles.css",
  "/app.js",
  "/privacy-mask.js",
  "/pwa.js",
  "/manifest.webmanifest",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/icons/icon-512-maskable.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(PRECACHE).then((cache) => cache.addAll(PRECACHE_URLS)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(
        names
          .filter((name) => name.startsWith(CACHE_PREFIX) && !CURRENT_CACHES.includes(name))
          .map((name) => caches.delete(name))
      )
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("message", (event) => {
  if (event.data?.type !== "REFRESH_APP_CACHES") return;
  event.waitUntil(
    caches.keys()
      .then((names) => Promise.all(
        names.filter((name) => name.startsWith(CACHE_PREFIX)).map((name) => caches.delete(name))
      ))
      .then(() => caches.open(PRECACHE))
      .then((cache) => cache.addAll(PRECACHE_URLS))
      .then(() => event.ports[0]?.postMessage({ ok: true, cacheVersion: CACHE_VERSION }))
      .catch((error) => {
        event.ports[0]?.postMessage({ ok: false, error: String(error) });
        throw error;
      })
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);

  // Never intercept anything but GET: POST/PUT/DELETE carry auth/session and mutating intent that
  // must always hit the real server, and the Cache API cannot store non-GET requests anyway.
  if (request.method !== "GET") {
    return;
  }

  // Only handle same-origin requests; leave everything else (if any) to the network.
  if (url.origin !== self.location.origin) {
    return;
  }

  // The SSE stream is a long-lived connection the app depends on for live updates. A cached or
  // intercepted response would break it immediately, so let it pass straight through untouched.
  if (url.pathname === "/api/events") {
    return;
  }

  // All other /api/ calls are dynamic, possibly authenticated JSON - always go to the network.
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(
      fetch(request).catch(() =>
        new Response(JSON.stringify({ ok: false, error: "offline" }), {
          status: 503,
          headers: { "Content-Type": "application/json" },
        })
      )
    );
    return;
  }

  // Page navigations: try the network first for freshness, fall back to cache, then to the
  // offline page so the app never shows a bare browser error while the server is unreachable.
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(RUNTIME).then((cache) => cache.put(request, copy));
          return response;
        })
        .catch(() =>
          caches.match(request).then((cached) => cached || caches.match("/offline.html"))
        )
    );
    return;
  }

  // Static assets (css/js/icons): cache-first for instant loads, refreshed in the background.
  event.respondWith(
    caches.match(request).then((cached) => {
      const fetchPromise = fetch(request)
        .then((response) => {
          if (response && response.status === 200) {
            const copy = response.clone();
            caches.open(RUNTIME).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(() => cached);
      return cached || fetchPromise;
    })
  );
});
