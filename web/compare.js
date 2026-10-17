/* Сравнение объектов (/compare, 17.10): отдельная страница вместо окна. Объекты — из «Сравнить» (localStorage cmp)
   или по ссылке /compare?ids=1,2,3 (ею можно поделиться). Компьютер — таблица во всю ширину; телефон — колонки листаются
   вбок, подписи строк закреплены слева. «Только различия» прячет одинаковые строки. Объект открывается поверх (objview.js). */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const ic = (n) => (window.ic ? window.ic(n) : "");
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (n, d = 0) => Number(n).toLocaleString("ru-RU", { maximumFractionDigits: d });
  const plural = (n, a, b, c) => (n % 10 === 1 && n % 100 !== 11 ? a : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? b : c);
  const price = (p, deal) => (!p ? "—" : deal === "rent" ? `${num(Math.round(p / 1000))} тыс. ₽/мес` : `${num(p / 1e6, 2)} млн ₽`);
  const MAX = 10;
  const head = (o) => (o.complex ? `ЖК ${o.complex}` : o.district || (o.street ? `ул. ${o.street}${o.house ? ", " + o.house : ""}` : o.settlement || o.title));
  const days = (o) => (o.first_seen ? Math.max(0, Math.floor((Date.now() / 1000 - o.first_seen) / 86400)) : null);

  function toast(text, ms = 2400) {
    const t = $("toast");
    t.textContent = text;
    try { t.showPopover(); } catch { t.hidden = false; }
    clearTimeout(toast.t);
    toast.t = setTimeout(() => { try { t.hidePopover(); } catch { t.hidden = true; } }, ms);
  }
  const load = () => { try { return JSON.parse(localStorage.getItem("cmp") || "[]").filter(Number.isInteger).slice(0, MAX); } catch { return []; } };
  const save = (ids) => { try { localStorage.setItem("cmp", JSON.stringify(ids)); } catch { /* приватный режим */ } };
  const fromUrl = (new URLSearchParams(location.search).get("ids") || "").split(",").map(Number).filter((x) => Number.isInteger(x) && x > 0).slice(0, MAX);
  let ids = fromUrl.length ? fromUrl : load();
  if (fromUrl.length) { save(ids); history.replaceState(null, "", "/compare"); }   // по ссылке — сразу к себе в сравнение
  let list = [], onlyDiff = false;
  try { onlyDiff = localStorage.getItem("cmpDiff") === "1"; } catch { /* */ }
  $("cpDiff").checked = onlyDiff;

  // строки таблицы: подпись, значение, как показать, что лучше (low — меньше, high — больше)
  const ROWS = [
    ["Цена", (o) => (o.price_doubt ? null : o.price), (v, o) => price(v, o.deal), "low"],
    ["Цена за м²", (o) => (o.deal !== "rent" && !o.price_doubt ? o.price_m2 : null), (v) => `${num(v)} ₽`, "low"],
    ["К рынку", (o) => (o.market ? o.market.diff : null), (v) => (v < 0 ? `дешевле на ${-v}%` : v > 0 ? `дороже на ${v}%` : "по рынку"), "low"],
    ["Что", (o) => o.title],
    ["Комнат", (o) => (o.rooms == null ? null : o.rooms === 0 ? "студия" : String(o.rooms))],
    ["Площадь", (o) => o.area, (v) => `${num(v, 1)} м²`, "high"],
    ["Участок", (o) => o.land, (v) => `${num(v, 1)} сот.`, "high"],
    ["Этаж", (o) => (o.floor ? `${o.floor}${o.floors ? " из " + o.floors : ""}` : null)],
    ["ЖК", (o) => o.complex],
    ["Район", (o) => o.district],
    ["Адрес", (o) => (o.street ? `ул. ${o.street}${o.house ? ", " + o.house : ""}` : o.settlement)],
    ["На 1+1", days, (v) => (v < 1 ? "меньше суток" : `${v} ${plural(v, "день", "дня", "дней")}`), "high"],
    ["Цена менялась", (o) => (o.price_history && o.price_history.length > 1 ? o.price_history.length - 1 : null), (v) => `${v} ${plural(v, "раз", "раза", "раз")}`],
    ["Фото", (o) => (o.photos ? o.photos.length : 0) || null, (v) => `${v}`],
  ];
  const best = (vals, how) => {
    const xs = vals.filter((v) => typeof v === "number");
    if (!how || xs.length < 2 || xs.every((v) => v === xs[0])) return null;
    return how === "low" ? Math.min(...xs) : Math.max(...xs);
  };

  function render() {
    const n = list.length;
    $("cpSub").textContent = n ? `${n} ${plural(n, "объект", "объекта", "объектов")} из ${MAX} — лучшее в каждой строке подсвечено`
      : "До 10 объектов рядом — лучшее в каждой строке подсвечено";
    $("cpShare").hidden = $("cpClear").hidden = !n;
    if (!n) {
      $("cpBody").innerHTML = `<div class="cp-empty">${ic("scale")}<b>Сравнивать пока нечего</b>
        <span>В поиске или в окне объекта нажмите «Сравнить» у 2–5 объектов — они появятся здесь рядом.</span>
        <a class="pill" href="/">${ic("search")}К поиску</a></div>`;
      return;
    }
    const rows = ROWS.map(([label, get, fmt, how]) => {
      const vals = list.map(get);
      if (vals.every((v) => v == null || v === "")) return "";
      const same = vals.every((v) => String(v ?? "") === String(vals[0] ?? ""));
      if (onlyDiff && same && n > 1) return "";
      const b = best(vals, how);
      return `<tr${same ? ' class="same"' : ""}><th scope="row">${esc(label)}</th>${vals.map((v, i) => `<td${b != null && v === b ? ' class="best"' : ""}>
        ${v == null || v === "" ? '<span class="cp-dash">—</span>' : esc(fmt ? fmt(v, list[i]) : v)}${b != null && v === b ? `<i class="cp-best">${ic("check")}лучше</i>` : ""}</td>`).join("")}</tr>`;
    }).join("");
    $("cpBody").innerHTML = `<div class="cp-scroll" style="--n:${n}"><table class="cp-t">
      <thead><tr><th class="cp-corner">${n < 2 ? `<span class="cp-hint">Добавьте ещё объект</span>` : ""}</th>${list.map((o) => `<td class="cp-obj">
        <button type="button" class="cp-x" data-del="${o.id}" aria-label="Убрать из сравнения" title="Убрать">${ic("x")}</button>
        <a class="cp-ph" href="/?open=${o.id}">${o.photos && o.photos[0] ? `<img src="${esc(o.photos[0])}" alt="" loading="lazy">`
          : `<span class="ph-none"><img src="/static/favicon.svg" alt=""><small>Фото у агента</small></span>`}</a>
        <a class="cp-name" href="/?open=${o.id}">${esc(head(o))}</a>
        <b class="cp-price">${o.price_doubt ? "Цена уточняется" : esc(price(o.price, o.deal))}</b>
        ${o.is_active === 0 ? `<span class="cp-gone">Снят с продажи</span>` : ""}
      </td>`).join("")}</tr></thead>
      <tbody>${rows}</tbody></table></div>
      <p class="note cp-note">${n < 2 ? "Для сравнения нужно хотя бы два объекта." : "Нажмите на фото или название — объект откроется поверх страницы."}
        На телефоне колонки листаются вбок.</p>`;
  }

  async function fetchAll() {
    const got = await Promise.all(ids.map((id) => fetch(`/api/listings/${id}`, { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : null)).catch(() => null)));
    list = got.filter(Boolean);
    if (list.length !== ids.length) { ids = list.map((o) => o.id); save(ids); }   // удалённые объекты — убрать
    render();
  }

  document.addEventListener("click", async (e) => {
    const t = e.target.closest("button");
    if (!t) return;
    if (t.dataset.del) {
      const id = Number(t.dataset.del);
      ids = ids.filter((x) => x !== id); save(ids);
      const col = [...document.querySelectorAll(".cp-obj")].findIndex((td) => td.querySelector(`[data-del="${id}"]`));
      document.querySelectorAll(".cp-t tr").forEach((tr) => { const c = tr.children[col + 1]; if (c) c.classList.add("bye"); });
      setTimeout(() => { list = list.filter((o) => o.id !== id); render(); }, 220);
      return;
    }
    if (t.id === "cpClear") {
      if (!confirm("Убрать все объекты из сравнения?")) return;
      ids = []; save(ids); list = []; render(); return;
    }
    if (t.id === "cpShare") {
      const url = `${location.origin}/compare?ids=${ids.join(",")}`;
      try { await navigator.clipboard.writeText(url); toast("Ссылка на сравнение скопирована"); }
      catch { prompt("Скопируйте ссылку:", url); }
    }
  });
  $("cpDiff").addEventListener("change", (e) => {
    onlyDiff = e.target.checked;
    try { localStorage.setItem("cmpDiff", onlyDiff ? "1" : "0"); } catch { /* */ }
    render();
  });
  window.addEventListener("storage", (e) => { if (e.key === "cmp") { ids = load(); fetchAll(); } });   // добавили в другой вкладке
  document.querySelectorAll("[data-ic]").forEach((el) => { el.outerHTML = ic(el.dataset.ic); });
  fetchAll();
})();
