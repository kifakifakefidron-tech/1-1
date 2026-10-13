/* 1+1 · уведомления v2: вкладки по видам со счётчиками, лента по дням, справа — поиски и что отслеживаем.
   API: /api/notices (GET — всё; POST {id?} — прочитано), /api/saved (DELETE {id} — не следить). */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const ic = (n) => (window.ic ? window.ic(n) : "");
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const plural = (n, a, b, c) => { const m = n % 10, h = n % 100; return m === 1 && h !== 11 ? a : m >= 2 && m <= 4 && (h < 10 || h >= 20) ? b : c; };
  // вид → [название вкладки, иконка]
  const KINDS = {
    search: ["Поиски", "search"], price: ["Цена", "trend-down"], gone: ["Сняты", "x-circle"], client: ["Клиенты", "users"],
    own: ["Мои объекты", "home"], plan: ["Планер", "calendar"], sub: ["Подписка", "card"],
  };
  let data = null, filter = "";
  const unreadAtOpen = new Set();   // что было непрочитанным при открытии — подсвечиваем до ухода со страницы

  const hhmm = (ts) => new Date(ts * 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  function dayLabel(ts) {
    const d = new Date(ts * 1000), t = new Date();
    const k = (x) => `${x.getFullYear()}-${x.getMonth()}-${x.getDate()}`;
    if (k(d) === k(t)) return "Сегодня";
    const y = new Date(t); y.setDate(t.getDate() - 1);
    if (k(d) === k(y)) return "Вчера";
    return d.toLocaleDateString("ru-RU", { day: "numeric", month: "long", ...(d.getFullYear() !== t.getFullYear() ? { year: "numeric" } : {}) });
  }
  function toast(text) {
    const t = $("toast");
    t.textContent = text;
    try { t.showPopover(); } catch { t.hidden = false; }
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { try { t.hidePopover(); } catch { t.hidden = true; } }, 2200);
  }
  async function call(url, body, method) {
    const opts = { credentials: "same-origin", method: method || (body !== undefined ? "POST" : "GET") };
    if (body !== undefined) { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(body); }
    let r = await fetch(url, opts);
    for (let i = 0; i < 3 && r.status === 503; i++) { await new Promise((res) => setTimeout(res, 1200 * (i + 1))); r = await fetch(url, opts); }
    let d = {};
    try { d = await r.json(); } catch { /* пусто */ }
    return { ok: r.ok, status: r.status, data: d };
  }

  function iconFor(n) {
    if (n.kind === "price") return /вырос|подорож|выше/i.test(n.title) ? "trend" : "trend-down";
    return (KINDS[n.kind] || ["", "bell"])[1];
  }
  function itemHTML(n, i) {
    let link = n.url || (n.listing_id ? `/?open=${n.listing_id}` : "");
    // выделение новых объектов — только при первом открытии этого уведомления
    try { if (n.kind === "search" && localStorage.getItem(`seen:${n.id}`)) link = link.replace(/&since=\d+/, ""); } catch { /* */ }
    const acts = (n.actions || []).map((a, j) => `<a class="nt2-btn${j ? "" : " main"}" href="${esc(a.url)}" data-nid="${n.id}">${esc(a.label)}</a>`).join("");
    const isNew = unreadAtOpen.has(n.id);
    const up = n.kind === "price" && iconFor(n) === "trend";
    return `<article class="nt2-it k-${esc(n.kind)}${up ? " up" : ""}${isNew ? " new" : ""}${n.kind === "gone" || n.is_active === 0 ? " off" : ""}" style="--i:${Math.min(i, 12)}">
      <span class="nt2-ico">${n.photo ? `<img src="${esc(n.photo)}" alt="" loading="lazy"><i>${ic(iconFor(n))}</i>` : ic(iconFor(n))}</span>
      <div class="nt2-b">
        <div class="nt2-top"><b>${esc(n.title)}</b><time>${hhmm(n.ts)}</time></div>
        ${n.body ? `<p>${esc(n.body)}</p>` : ""}
        ${acts || link ? `<div class="nt2-acts">${acts || `<a class="nt2-btn" href="${esc(link)}" data-nid="${n.id}">${n.kind === "search" ? "Показать новые" : "Открыть"}${ic("chevron")}</a>`}</div>` : ""}
      </div></article>`;
  }

  function renderTabs() {
    const cnt = {};
    data.items.forEach((n) => { cnt[n.kind] = (cnt[n.kind] || 0) + 1; });
    const unr = {};
    data.items.forEach((n) => { if (unreadAtOpen.has(n.id)) unr[n.kind] = (unr[n.kind] || 0) + 1; });
    const tab = (k, label, n, u) => `<button type="button" data-f="${k}" class="${filter === k ? "on" : ""}">${label}
      ${n ? `<b class="${u ? "hot" : ""}">${u || n}</b>` : ""}</button>`;
    $("ntFilter").innerHTML = tab("", "Все", data.items.length, unreadAtOpen.size)
      + Object.entries(KINDS).filter(([k]) => cnt[k]).map(([k, [label]]) => tab(k, label, cnt[k], unr[k])).join("");
  }
  function renderFeed() {
    const list = data.items.filter((n) => !filter || n.kind === filter);
    if (!list.length) {
      $("ntList").innerHTML = `<div class="nt2-empty">
        <svg viewBox="0 0 120 120" aria-hidden="true"><circle cx="60" cy="60" r="44" class="a"/><path d="M60 34c-11 0-18 8-18 19v11l-6 9h48l-6-9V53c0-11-7-19-18-19z" class="b"/>
          <path d="M53 78a7 7 0 0 0 14 0" class="b"/><circle cx="82" cy="38" r="9" class="c"/></svg>
        <b>${filter ? "Здесь пока ничего нет" : "Пока тихо"}</b>
        <span>Нажмите «Следить» у поиска или отметьте объекты сердечком — сообщим о новых объектах, снижении цены и снятии с продажи.</span>
        <a class="pill" href="/">Перейти к поиску</a></div>`;
      return;
    }
    let html = "", day = "";
    list.forEach((n, i) => {
      const d = dayLabel(n.ts);
      if (d !== day) { html += `<h3 class="nt2-day">${d}</h3>`; day = d; }
      html += itemHTML(n, i);
    });
    $("ntList").innerHTML = html;
  }
  function renderSide() {
    $("ntMail").textContent = data.email ? ` и на почту ${data.email}` : "";
    $("ntSaved").innerHTML = data.saved.length ? data.saved.map((s) => `<li>
        <span class="nt2-sico">${ic("bell")}</span>
        <a href="${esc(s.url)}"><b>${esc(s.title)}</b><small>слежу с ${new Date(s.created * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "long" })}</small></a>
        <button type="button" class="nt2-x" data-unsave="${s.id}" aria-label="Не следить" title="Не следить">${ic("x")}</button></li>`).join("")
      : `<li class="nt2-none">Пока нет ни одного поиска.</li>`;
    const fav = data.favorites || 0;
    const tile = (icon, n, label, text, href) => `<a class="nt2-tile" href="${href}">${ic(icon)}<span><b>${n}</b> ${label}</span><small>${text}</small></a>`;
    let html = tile("heart", fav, plural(fav, "объект в избранном", "объекта в избранном", "объектов в избранном"),
      "Сообщим, если цена изменится или объект снимут", "/work");
    if (data.own) html += tile("home", data.own, plural(data.own, "ваш объект", "ваших объекта", "ваших объектов"),
      "Спросим «ещё актуален?» за 10, 5, 2 и 1 день до конца срока", "/my");
    if (!data.is_admin && data.access_until) {
      const left = Math.ceil((data.access_until * 1000 - Date.now()) / 864e5);
      const d = new Date(data.access_until * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
      html += left > 0 ? tile("card", left, plural(left, "день доступа", "дня доступа", "дней доступа"), `До ${d} — предупредим за 2 дня`, "/pay")
        : tile("card", 0, "дней доступа", "Доступ закончился — продлите", "/pay");
    }
    $("ntTrack").innerHTML = html;
  }
  function render() {
    renderTabs(); renderFeed(); renderSide();
    $("ntReadAll").hidden = !data.unread;
    $("ntSub").textContent = unreadAtOpen.size
      ? `${unreadAtOpen.size} ${plural(unreadAtOpen.size, "новое", "новых", "новых")} с прошлого захода`
      : "Новые объекты по поискам, цены в избранном, ваши объекты, планер и клиенты — в одной ленте.";
  }

  async function load() {
    const r = await call("/api/notices");
    if (r.status === 401) { $("ntGuest").hidden = false; return; }
    if (!r.ok) { $("ntApp").hidden = false; $("ntList").innerHTML = `<div class="nt2-empty"><b>Не получилось загрузить</b><span>Обновите страницу.</span></div>`; return; }
    data = r.data;
    data.items.sort((a, b) => b.ts - a.ts || b.id - a.id);
    data.items.forEach((n) => { if (!n.read) unreadAtOpen.add(n.id); });
    $("ntApp").hidden = false;
    render();
    // посмотрели — значит прочитали (подсветка новых остаётся, пока страница открыта)
    if (data.unread) setTimeout(async () => { await call("/api/notices", {}); data.unread = 0; $("ntReadAll").hidden = true; }, 1500);
  }

  document.addEventListener("click", async (e) => {
    const a = e.target.closest("a[data-nid]");
    if (a) { try { localStorage.setItem(`seen:${a.dataset.nid}`, "1"); } catch { /* */ } return; }
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.f !== undefined) { filter = b.dataset.f; renderTabs(); renderFeed(); return; }
    if (b.id === "ntReadAll") {
      await call("/api/notices", {});
      data.items.forEach((n) => { n.read = 1; }); data.unread = 0; unreadAtOpen.clear(); render();
      return;
    }
    if (b.dataset.unsave) {
      await call("/api/saved", { id: Number(b.dataset.unsave) }, "DELETE");
      data.saved = data.saved.filter((s) => s.id !== Number(b.dataset.unsave));
      renderSide();
      toast("Больше не следим за этим поиском");
    }
  });
  load();
})();
