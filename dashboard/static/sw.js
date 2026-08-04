/* Shell-only cache. Scanner state is NEVER cached: a served-from-cache figure
 * is a stale number rendered cheerfully, which is the failure this dashboard
 * exists to prevent. /api and /ws always go to the network. */
const SHELL = "scanner-shell-v1";
const ASSETS = ["/", "/static/styles.css", "/static/app.js",
                "/static/manifest.webmanifest", "/static/icon.svg"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) =>
    Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k)))
  ).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith("/api") || url.pathname === "/ws") return;
  e.respondWith(
    fetch(e.request).catch(() => caches.match(e.request).then((r) => r || Response.error()))
  );
});
