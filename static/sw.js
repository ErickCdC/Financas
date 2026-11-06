/* Service worker do Finanças: app instalável, abre rápido mesmo com o servidor dormindo, recebe notificações. */
const VERSION = "fin-v2.1.1";
const SHELL = ["/", "/app.js", "/style.css", "/manifest.webmanifest", "/logo.svg", "/favicon.svg",
  "/icons/icon-192.png", "/icons/badge-96.png"];
const DATA = "fin-data";

self.addEventListener("install", e => {
  e.waitUntil(caches.open(VERSION).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== VERSION && k !== DATA).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname === "/health") return; // dados: sempre rede (o app cuida do cache)
  // Arquivos do app: responde do cache na hora e atualiza em segundo plano.
  const key = e.request.mode === "navigate" ? "/" : e.request;
  e.respondWith(caches.open(VERSION).then(async cache => {
    const cached = await cache.match(key);
    const net = fetch(e.request).then(r => { if (r.ok) cache.put(key, r.clone()); return r; }).catch(() => cached);
    return cached || net;
  }));
});

self.addEventListener("push", e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) { d = { body: e.data && e.data.text() }; }
  const title = d.title || "Finanças";
  e.waitUntil(self.registration.showNotification(title, {
    body: d.body || "", icon: "/icons/icon-192.png", badge: "/icons/badge-96.png",
    tag: d.tag || "fin", renotify: true, data: { url: d.url || "/#inicio" }, lang: "pt-BR",
  }));
});

self.addEventListener("notificationclick", e => {
  e.notification.close();
  const target = new URL((e.notification.data && e.notification.data.url) || "/#inicio", location.origin).href;
  e.waitUntil((async () => {
    const list = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const c of list) {
      if (new URL(c.url).origin === location.origin) {
        await c.focus();
        c.postMessage({ type: "navigate", url: target });
        return;
      }
    }
    await self.clients.openWindow(target);
  })());
});

self.addEventListener("pushsubscriptionchange", e => {
  e.waitUntil((async () => {
    const old = e.oldSubscription;
    const opts = old ? old.options : null;
    if (!opts) return;
    const sub = await self.registration.pushManager.subscribe(opts);
    await fetch("/api/push/subscribe", {
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ subscription: sub.toJSON(), label: "Aparelho (renovado)" }),
    });
  })());
});
