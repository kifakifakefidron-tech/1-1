/* 1+1 · страница уведомлений: новые объекты по подпискам, цена и снятие объектов из избранного,
   окончание доступа, актуальность своих объектов. API: /api/notices, /api/saved. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const plural = (n, a, b, c) => { const m = n % 10, h = n % 100; return m === 1 && h !== 11 ? a : m >= 2 && m <= 4 && (h < 10 || h >= 20) ? b : c; };
  let data = null;
  let filter = "";

  function ago(ts) {
    const s = Date.now() / 1000 - ts;
    if (s < 3600) return `${Math.max(1, Math.round(s / 60))} мин назад`;
    if (s < 86400) return `${Math.round(s / 3600)} ч назад`;
    return new Date(ts * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
  }
  function toast(text) {
    const t = $("toast");
    t.textContent = text;
    t.classList.add("show");
    setTimeout(() => t.classList.remove("show"), 2200);
  }
  async function call(url, body, method) {
    const opts = { credentials: "same-origin", method: method || (body !== undefined ? "POST" : "GET") };
    if (body !== undefined) { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(body); }
    const r = await fetch(url, opts);
    let d = {};
    try { d = await r.json(); } catch { /* пусто */ }
    return { ok: r.ok, status: r.status, data: d };
  }

  function itemHTML(n) {
    const link = n.url || (n.listing_id ? `/?open=${n.listing_id}` : "");
    const acts = (n.actions || []).map((a, i) => `<a class="pill${i ? " light" : ""}" href="${esc(a.url)}">${esc(a.label)}</a>`).join("");
    return `<li class="nt-item${n.read ? "" : " unread"}" data-kind="${esc(n.kind)}">
      <span class="nt-ico k-${esc(n.kind)}">${n.photo ? `<img src="${esc(n.photo)}" alt="" loading="lazy">` : esc(n.icon)}</span>
      <div class="nt-body">
        <b>${esc(n.title)}</b>
        ${n.body ? `<p>${esc(n.body)}</p>` : ""}
        <div class="nt-meta">${esc(ago(n.ts))}${link ? ` · <a href="${esc(link)}">${n.kind === "search" ? "Показать" : "Открыть"} →</a>` : ""}</div>
        ${acts ? `<div class="nt-acts">${acts}</div>` : ""}
      </div></li>`;
  }

  function render() {
    const list = data.items.filter((n) => !filter || n.kind === filter);
    $("ntList").innerHTML = list.length ? list.map(itemHTML).join("")
      : `<li class="nt-empty">${filter ? "Здесь пока ничего нет." : "Пока уведомлений нет. Подпишитесь на поиск или добавьте объекты в избранное — изменения появятся здесь."}</li>`;
    $("ntReadAll").hidden = !data.unread;
    $("ntMail").textContent = data.email ? ` и на почту ${data.email}` : "";
    $("ntSaved").innerHTML = data.saved.length ? data.saved.map((s) => `<li>
        <a href="${esc(s.url)}">${esc(s.title)}</a>
        <span class="meta">с ${new Date(s.created * 1000).toLocaleDateString("ru-RU")}</span>
        <button type="button" class="chip" data-unsave="${s.id}">Не следить</button></li>`).join("")
      : `<li class="nt-empty">Пока нет. <a href="/">Настроить поиск →</a></li>`;
    const fav = data.favorites || 0;
    const track = [`<li><b>${fav}</b> ${plural(fav, "объект", "объекта", "объектов")} в избранном — сообщим, если изменится цена
        или объект снимут с сайта. <a href="/?fav=1">Открыть избранное →</a></li>`];
    if (data.own) track.push(`<li><b>${data.own}</b> ${plural(data.own, "ваш объект", "ваших объекта", "ваших объектов")} —
        напомним подтвердить актуальность за 3 дня до конца срока. <a href="/?agent=1">Кабинет агента →</a></li>`);
    if (!data.is_admin && data.access_until) {
      const d = new Date(data.access_until * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
      track.push(`<li>Доступ к номерам ${data.access_until * 1000 > Date.now() ? `до <b>${d}</b> — предупредим за 2 дня до конца` : "закончился"}.</li>`);
    }
    $("ntTrack").innerHTML = track.join("");
  }

  async function load() {
    const r = await call("/api/notices");
    if (r.status === 401) { $("ntGuest").hidden = false; return; }
    if (!r.ok) { $("ntList").innerHTML = `<li class="nt-empty">Не получилось загрузить — обновите страницу.</li>`; return; }
    data = r.data;
    $("ntApp").hidden = false;
    render();
    // Посмотрели — значит прочитали (подсветка новых остаётся до следующего захода)
    if (data.unread) setTimeout(() => call("/api/notices", {}), 1500);
  }

  document.addEventListener("click", async (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.f !== undefined) {
      filter = b.dataset.f;
      document.querySelectorAll("#ntFilter .chip").forEach((x) => x.classList.toggle("on", x === b));
      render();
    } else if (b.id === "ntReadAll") {
      await call("/api/notices", {});
      data.items.forEach((n) => { n.read = 1; }); data.unread = 0; render();
    } else if (b.dataset.unsave) {
      await call("/api/saved", { id: Number(b.dataset.unsave) }, "DELETE");
      data.saved = data.saved.filter((s) => s.id !== Number(b.dataset.unsave));
      render();
      toast("Больше не следим за этим поиском");
    }
  });
  load();
})();
