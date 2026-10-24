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
    own: ["Мои объекты", "home"], plan: ["Планер", "calendar"], sub: ["Подписка", "card"], billing: ["Оплаты", "coins"],
    buyer: ["Покупатели", "briefcase"],
  };
  // 24.10b: отметка вида на карточке (образец Артёма)
  const PILL = { search: "Новое по поиску", price: "Цена в избранном", gone: "Снят с продажи", client: "Клиент", own: "Мой объект",
    plan: "Планер", sub: "Подписка", billing: "Оплата", buyer: "Есть покупатель" };
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
    let title = n.title, body = n.body || "", big = "";
    if (n.kind === "buyer") title = title.replace(/^Под ваш объект есть покупатель\s*—\s*/, "");
    if (n.kind === "price") {   // «Квартира…: 5,5 млн ₽ → 5,3 млн ₽» → крупно «5,5 → 5,3 млн ₽  −0,2 млн»
      const m = body.match(/^(.*):\s*([\d\s,.]+)\s*(млн|тыс\.?)?[^→]*→\s*([\d\s,.]+)\s*(млн ₽|тыс\.? ₽[^ ]*|₽[^ ]*|млн|тыс\.?)?/);
      if (m) {
        const diff = (n.title.match(/\sна\s(.+)$/) || [])[1] || "";
        big = `<p class="nt2-big">${esc(m[2].trim())} <span>→</span> ${esc(m[4].trim())}<small>${esc((m[5] || m[3] || "").trim())}</small>
          ${diff ? `<em class="${up ? "up" : ""}">${up ? "+" : "−"}${esc(diff.replace(/\s*₽$/, ""))}</em>` : ""}</p>`;
        title = ""; body = m[1];
      } else {   // старый вид: «Цена снизилась: Евро-2, 38 м² — 6,75 млн ₽ (−250 тыс.)» + «ЖК … · было 7 млн ₽»
        const t = n.title.match(/^Цена \S+:\s*(.+?)\s+—\s+([\d\s,.]+)\s*(млн ₽|тыс\.? ₽\S*|₽\S*)\s*\(([−+-])\s*(.+?)\)/);
        if (t) {
          const was = (body.match(/было\s+([\d\s,.]+)/) || [])[1];
          big = `<p class="nt2-big">${was ? `${esc(was.trim())} <span>→</span> ` : ""}${esc(t[2].trim())}<small>${esc(t[3])}</small>
            <em class="${up ? "up" : ""}">${t[4] === "+" ? "+" : "−"}${esc(t[5])}</em></p>`;
          title = t[1]; body = body.replace(/\s*·?\s*было\s+[\d\s,.]+\s*\S*\s*₽?/, "");
        }
      }
    }
    return `<article class="nt2-it k-${esc(n.kind)}${up ? " up" : ""}${isNew ? " new" : ""}${n.kind === "gone" || n.is_active === 0 ? " off" : ""}" style="--i:${Math.min(i, 12)}">
      <span class="nt2-ico">${n.photo ? `<img src="${esc(n.photo)}" alt="" loading="lazy">` : ic(iconFor(n))}</span>
      <div class="nt2-b">
        <div class="nt2-top"><span class="nt2-pill k-${esc(n.kind)}">${esc(PILL[n.kind] || "Уведомление")}</span><time>${hhmm(n.ts)}</time></div>
        ${big}${title ? `<b class="nt2-t">${esc(title)}</b>` : ""}
        ${body ? `<p>${esc(body)}</p>` : ""}
        ${acts ? `<div class="nt2-acts">${acts}</div>` : ""}
      </div>
      ${link ? `<a class="nt2-go" href="${esc(link)}" data-nid="${n.id}" aria-label="${n.kind === "search" ? "Показать новые" : "Открыть"}">${ic("chevron")}</a>` : ""}
    </article>`;
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
    const on = $("ntFilter").querySelector(".on");
    if (on) on.scrollIntoView({ block: "nearest", inline: "nearest" });
    tabArrows();
  }
  // 24.10c: вкладки не помещаются — листаются колёсиком, пальцем и стрелками по краям
  function tabArrows() {
    const nav = $("ntFilter"), w = $("ntTabsW");
    if (!nav || !w) return;
    w.classList.toggle("can-l", nav.scrollLeft > 4);
    w.classList.toggle("can-r", nav.scrollLeft + nav.clientWidth < nav.scrollWidth - 4);
  }
  if ($("ntFilter")) {
    $("ntFilter").addEventListener("scroll", tabArrows, { passive: true });
    $("ntFilter").addEventListener("wheel", (e) => {
      const nav = e.currentTarget;
      if (nav.scrollWidth <= nav.clientWidth || Math.abs(e.deltaX) > Math.abs(e.deltaY)) return;
      e.preventDefault(); nav.scrollLeft += e.deltaY;
    }, { passive: false });
    addEventListener("resize", tabArrows);
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
  function renderSide() {   // 24.10b: крупные цифры — что отслеживаю и сколько поисков
    const fav = data.favorites || 0;
    $("ntTrack").innerHTML = `<a class="nt2-num" href="/work"><b>${fav}</b><span>${plural(fav, "объект", "объекта", "объектов")}<br>в избранном</span></a>
      <p class="nt2-hint">Сообщим, если цена изменится или объект снимут.</p>
      ${data.own ? `<p class="nt2-num sm"><b>${data.own}</b><span>${plural(data.own, "ваш объект", "ваших объекта", "ваших объектов")}</span></p>
        <p class="nt2-hint">Спросим «ещё актуален?» до конца срока.</p>` : ""}
      ${!data.is_admin && data.access_until ? (() => { const left = Math.ceil((data.access_until * 1000 - Date.now()) / 864e5);
        return `<p class="nt2-num sm"><b>${Math.max(0, left)}</b><span>${plural(Math.max(0, left), "день", "дня", "дней")} доступа</span></p>
          <p class="nt2-hint">${left > 0 ? "Предупредим за 2 дня до конца." : "Доступ закончился — <a href=\"/pay\">продлите</a>."}</p>`; })() : ""}`;
    const n = data.saved.length;
    $("ntSavedNum").innerHTML = `<b class="${n ? "" : "zero"}">${n}</b><span>${plural(n, "поиск", "поиска", "поисков")}<br>с уведомлениями</span>`;
    $("ntMail").textContent = data.email ? ` и на почту ${data.email}` : "";
    $("ntSaved").innerHTML = n ? data.saved.map((s) => `<li>
        <a href="${esc(s.url)}"><b>${esc(s.title)}</b><small>слежу с ${new Date(s.created * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "long" })}</small></a>
        <button type="button" class="nt2-x" data-unsave="${s.id}" aria-label="Не следить" title="Не следить">${ic("x")}</button></li>`).join("") : "";
  }
  function render() {
    renderTabs(); renderFeed(); renderSide();
    $("ntReadAll").hidden = !data.unread;
    const today = data.items.filter((n) => dayLabel(n.ts) === "Сегодня").length;
    $("ntSub").innerHTML = `<b>${data.items.length}</b> всего <b>${today}</b> сегодня${unreadAtOpen.size ? ` <b class="hot">${unreadAtOpen.size}</b> новых` : ""}
      <span>поиски, избранное, ваши объекты, планер и клиенты — в одной ленте</span>`;
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
    if (b && b.dataset.tarr) { const nav = $("ntFilter"); nav.scrollBy({ left: Number(b.dataset.tarr) * nav.clientWidth * .7, behavior: "smooth" }); return; }
    if (!b) {   // нажатие по карточке — как по стрелке (кроме выделения текста)
      const it = e.target.closest(".nt2-it"), go = it && it.querySelector(".nt2-go");
      if (go && !String(getSelection())) go.click();
      return;
    }
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
