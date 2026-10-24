/* Карточка объекта — ОДНА на весь сайт (24.10c, Артём: «везде, где есть объекты, — как на главной»).
   Та же разметка и те же классы, что у ленты главной (.item, .item-photo 1:1, .item-head, .price, .item-title, .item-place),
   поэтому вид задают style/theme/glass.css. Страница добавляет своё через opts:
     top  — отметки поверх фото (слева сверху), sel — галочка выбора, extra — строки под адресом,
     foot — кнопки внизу, cls — свой класс, href — куда ведёт нажатие (по умолчанию /?open=ID — окно объекта, objview.js).
   LCard.html(o, opts) → <li>; LCard.grid(html, cls) → <ol class="results lc-grid">. Фото листаются стрелками. */
(() => {
  "use strict";
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ic = (n) => (window.ic ? window.ic(n) : "");
  const num = (x, d = 0) => Number(x).toLocaleString("ru-RU", { maximumFractionDigits: d });
  const plural = (n, a, b, c) => { const m = n % 100, d = n % 10; return m > 10 && m < 20 ? c : d === 1 ? a : d > 1 && d < 5 ? b : c; };
  function price(p, deal) {
    if (p == null || p === 0) return "Цена не указана";
    if (deal === "rent") return `${num(p)} ₽/мес`;
    return p >= 1e6 ? `${num(p / 1e6, 2)} млн ₽` : `${num(p)} ₽`;
  }
  const street = (o) => (o.street ? `ул. ${o.street}${o.house ? ", " + o.house : ""}` : "");
  function headline(o) { return o.complex ? `ЖК ${o.complex}` : o.district || street(o) || o.settlement || ""; }
  function address(o) {
    const head = headline(o), ds = [o.district, ...(o.extra_districts || [])].filter(Boolean).join(" / ");
    return [...new Set([ds, o.settlement, street(o)].filter((x) => x && x !== head))].join(" · ");
  }
  function ago(ts) {
    if (!ts) return "";
    const days = Math.floor((new Date(new Date().toDateString()) - new Date(new Date(ts * 1000).toDateString())) / 864e5);
    return days <= 0 ? "сегодня" : days === 1 ? "вчера" : days < 7 ? `${days} ${plural(days, "день", "дня", "дней")} назад`
      : new Date(ts * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
  }
  function meta(o) {
    const parts = [];
    if (o.first_seen) {
      const n = Math.max(0, Math.floor((Date.now() / 1000 - o.first_seen) / 86400));
      parts.push(n < 1 ? "Новое на 1+1" : `На 1+1 — ${n} ${plural(n, "день", "дня", "дней")}`);
    }
    if (o.last_seen) parts.push(`обновлено ${ago(o.last_seen)}`);
    return parts.join(" · ");
  }
  // логотип-заглушка — тот же, что в ленте главной (app.js LOGO)
  const LOGO = `<svg class="logo" viewBox="0 0 100 100" aria-hidden="true">
    <g class="walls" fill="none" stroke="currentColor" stroke-width="5" stroke-linecap="round" stroke-linejoin="round">
      <path pathLength="1" d="M4 40V4h92v92H4V60"/><path pathLength="1" d="M49 4v12"/>
      <path pathLength="1" d="M49 27v13h47"/><path pathLength="1" d="M49 59h13"/>
      <path pathLength="1" d="M72 59h24"/><path pathLength="1" d="M49 59v37"/></g>
    <text class="logo-txt" x="27" y="56.5" text-anchor="middle">1+1</text></svg>`;
  function html(o, opts = {}) {
    const photos = o.photos || (o.photo ? [o.photo] : []), n = photos.length, gone = o.is_active === 0 || o.is_active === false;
    const href = opts.href || `/?open=${o.id}`;
    const m2 = o.price_m2 && o.deal !== "rent" && !o.price_doubt ? `<div class="price-m2">${num(o.price_m2)} ₽/м²</div>` : "";
    const photo = n
      ? `<div class="item-photo" data-lc-ph="0" data-lc-photos="${esc(JSON.stringify(photos.slice(0, 20)))}"><img src="${esc(photos[0])}" alt="${esc(o.title || "")}" loading="lazy" decoding="async">${n > 1 ? `
          <button type="button" class="ph-nav prev" data-lc-step="-1" aria-label="Предыдущее фото">${ic("chevron-left")}</button>
          <button type="button" class="ph-nav next" data-lc-step="1" aria-label="Следующее фото">${ic("chevron")}</button>
          <span class="ph-count">1 / ${n}</span>` : ""}${opts.top ? `<div class="lc-top">${opts.top}</div>` : ""}${gone ? `<em class="lc-gone">${esc(opts.goneText || "Снят с сайта")}</em>` : ""}</div>`
      : `<div class="item-photo none">${LOGO}<span>${esc(opts.noPhoto || "Фото у агента")}</span>${opts.top ? `<div class="lc-top">${opts.top}</div>` : ""}${gone ? `<em class="lc-gone">${esc(opts.goneText || "Снят с сайта")}</em>` : ""}</div>`;
    const addr = address(o), mt = opts.meta != null ? opts.meta : meta(o);
    return `<li class="item has-photo lc${gone ? " gone" : ""}${opts.cls ? " " + opts.cls : ""}" data-lc="${o.id}" data-href="${esc(href)}" tabindex="0">
      ${photo}${opts.sel || ""}
      ${headline(o) ? `<h3 class="item-head">${esc(headline(o))}</h3>` : ""}
      <div class="item-price"><div class="price">${o.price_doubt ? "Цена уточняется" : esc(price(o.price, o.deal))}</div>${m2}</div>
      ${o.title ? `<div class="item-title">${esc(o.title)}</div>` : ""}
      ${addr ? `<div class="item-place">${esc(addr)}</div>` : ""}
      ${opts.extra || ""}
      ${mt || opts.foot ? `<div class="item-foot lc-foot">${mt ? `<div class="item-meta">${esc(mt)}</div>` : ""}${opts.foot || ""}</div>` : ""}
    </li>`;
  }
  const grid = (inner, cls = "") => `<ol class="results lc-grid${cls ? " " + cls : ""}">${inner}</ol>`;

  // фото — стрелками (как на главной), без перехода в окно
  document.addEventListener("click", (e) => {
    const st = e.target.closest("[data-lc-step]");
    if (st) {
      e.preventDefault(); e.stopPropagation();
      const box = st.closest("[data-lc-ph]"), list = JSON.parse(box.dataset.lcPhotos || "[]");
      const i = (Number(box.dataset.lcPh) + Number(st.dataset.lcStep) + list.length) % list.length;
      box.dataset.lcPh = i;
      box.querySelector("img").src = list[i];
      const c = box.querySelector(".ph-count");
      if (c) c.textContent = `${i + 1} / ${list.length}`;
      return;
    }
    // нажатие по карточке (не по кнопке, ссылке, полю) — открыть объект; ссылка /?open= — окно поверх страницы (objview.js)
    const card = e.target.closest("li.lc[data-href]");
    if (!card || e.target.closest("a, button, input, select, textarea, label, [data-no-open]") || String(getSelection())) return;
    const a = document.createElement("a");
    a.href = card.dataset.href;
    if (e.ctrlKey || e.metaKey) a.target = "_blank";
    a.style.display = "none";
    document.body.appendChild(a); a.click(); a.remove();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || !e.target.matches || !e.target.matches("li.lc[data-href]")) return;
    e.target.click();
  });
  window.LCard = { html, grid, headline, address, price };
})();
