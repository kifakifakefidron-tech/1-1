/* 1+1 · помощник приложения (service worker): быстрые повторные открытия и понятная страница без интернета.
   Файлы сайта (/static/…?v=N) — из кэша (у каждого обновления своя версия в адресе), страницы — из сети,
   без сети — последняя открытая версия или «нет интернета». Данные (API) и фото агентов не кэшируем. */
const STATIC = "oneplus-static-v1";
const PAGES = "oneplus-pages-v1";
const OFFLINE = `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>1+1</title><body style="margin:0;font:17px system-ui;background:#035352;color:#F3E8BC;display:grid;place-items:center;min-height:100vh;text-align:center;padding:20px">
<div><h1 style="font-size:28px">Нет интернета</h1><p>Проверьте связь — страница откроется, как только интернет появится.</p>
<button onclick="location.reload()" style="margin-top:12px;padding:12px 22px;border-radius:999px;border:0;background:#F3E8BC;color:#035352;font:600 16px system-ui">Обновить</button></div>`;

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => ![STATIC, PAGES].includes(k)).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin || url.pathname.startsWith("/api/") || url.pathname.startsWith("/c/")) return;
  if (url.pathname.startsWith("/static/") && url.searchParams.has("v")) {   // версия в адресе — можно смело брать из кэша
    e.respondWith(caches.open(STATIC).then(async (c) => {
      const hit = await c.match(req);
      if (hit) return hit;
      const res = await fetch(req);
      if (res.ok) c.put(req, res.clone());
      return res;
    }));
    return;
  }
  if (req.mode === "navigate") {   // страницы — всегда свежие из сети; без сети — последняя сохранённая
    e.respondWith(fetch(req).then((res) => {
      if (res.ok) { const copy = res.clone(); caches.open(PAGES).then((c) => c.put(url.pathname, copy)); }
      return res;
    }).catch(async () => (await caches.match(url.pathname)) || new Response(OFFLINE, { headers: { "Content-Type": "text/html; charset=utf-8" } })));
  }
});
