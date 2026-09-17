/* Service worker.
 *
 * Only the shell is cached, and only so the office opens instantly and shows
 * something sane when the wifi drops on the way to the kitchen. Office state
 * is never cached: a stale ledger shown as current would be worse than a
 * spinner. Neither is anything that failed or was refused.
 */

const SHELL = "forge-shell-v1";
const FILES = [
  "/",
  "/static/office.css",
  "/static/office.js",
  "/static/scene.js",
  "/static/icon.svg",
  "/static/icon-192.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL).then((cache) => cache.addAll(FILES)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  // Live state always comes from the office, or not at all.
  if (url.pathname.startsWith("/api/")) return;

  event.respondWith(
    fetch(request)
      .then((response) => {
        // Never cache a redirect to the sign-in page, or an error.
        if (response.ok && response.type === "basic") {
          const copy = response.clone();
          caches.open(SHELL).then((cache) => cache.put(request, copy)).catch(() => {});
        }
        return response;
      })
      .catch(() => caches.match(request).then((hit) => hit || caches.match("/")))
  );
});
