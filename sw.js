// Service worker de Pipeta: permite instalarla como app y abrirla aunque la conexión falle.
// La página y el contenido se piden siempre primero a internet (para tenerlos al día);
// si no hay conexión, se usa la última copia guardada.
const VERSION = "pipeta-v4";
const BASE = ["./", "./index.html", "./manifest.webmanifest", "./icons/icon-192.png", "./icons/icon-512.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(VERSION).then(c => c.addAll(BASE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== VERSION).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return; // fuentes externas: el navegador se encarga
  const fresh = req.mode === "navigate" || url.pathname.endsWith("/data/feed.json") || url.pathname.endsWith("/index.html");
  if (fresh) {
    e.respondWith(fetch(req).then(res => {
      const copy = res.clone();
      caches.open(VERSION).then(c => c.put(url.pathname.endsWith("feed.json") ? "./data/feed.json" : req, copy));
      return res;
    }).catch(() => caches.match(url.pathname.endsWith("feed.json") ? "./data/feed.json" : req)
      .then(r => r || caches.match("./index.html"))));
  } else {
    e.respondWith(caches.match(req).then(r => r || fetch(req).then(res => {
      const copy = res.clone(); caches.open(VERSION).then(c => c.put(req, copy)); return res;
    })));
  }
});
