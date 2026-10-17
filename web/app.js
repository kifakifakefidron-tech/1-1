/* 1+1 · поиск объектов — страница поиска.
   Фильтры → /api/listings, /api/facets, /api/map; карточка → /api/listings/{id};
   вход для коллег → /api/login. Состояние фильтров хранится в адресе страницы,
   поэтому ссылкой на поиск можно поделиться. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const ic = (n) => (window.ic ? window.ic(n) : "");   // тонкие иконки (web/icons.js)
  const PAGE_SIZE = 32;  // делится и на 4 (компьютер), и на 2 (телефон)
  // ?embed=1 — окно объекта поверх другой страницы сайта (web/objview.js): показываем только окно объекта,
  // ссылки уводят всю вкладку, а закрытие окна сообщаем странице, которая его открыла
  const EMBED = new URLSearchParams(location.search).has("embed") && window.parent !== window;
  if (EMBED) {
    document.documentElement.classList.add("embed");
    const base = document.createElement("base");
    base.target = "_top";
    document.head.appendChild(base);
  }
  const toParent = (msg) => { if (EMBED) window.parent.postMessage({ ov: msg }, location.origin); };

  const state = {
    q: "", deal: "sale", types: [], rooms: [],
    priceMin: "", priceMax: "", areaMin: "", areaMax: "", landMin: "", landMax: "",
    notFirst: false, notLast: false, fresh: "", hot: false, districts: [], complexes: [],
    sort: "new", view: "list",
  };
  const DEFAULTS = JSON.parse(JSON.stringify(state));
  let savedList = [];       // сохранённые поиски («🔔 Следить»)
  let facetComplexes = [];  // ЖК с числом объектов — для выпадающих списков
  let activeSaved = 0;      // открыт сохранённый поиск
  let hitSince = 0;         // из уведомления: выделить объекты, появившиеся после этого времени
  let meta = { types: {}, access: false, access_required: false };
  let page = 1;
  let reqSeq = 0;
  let map = null, cluster = null;
  let favMode = false;      // показываем избранное вместо поиска
  let tgPoll = 0;           // ожидание входа через Telegram
  let loadedAt = 0;         // время сервера, когда загрузили список (для «новых объектов»)
  let lastTotal = 0;        // сколько объектов найдено — для кнопки «Показать N»

  // ─── форматирование ──────────────────────────────────────────────────────
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (x, d = 0) => Number(x).toLocaleString("ru-RU", { maximumFractionDigits: d });

  function fmtPrice(p, deal) {
    if (p == null) return "Цена не указана";
    if (deal === "rent") return `${num(p)} ₽/мес`;
    if (p >= 1e6) return `${num(p / 1e6, 2)} млн ₽`;
    return `${num(p)} ₽`;
  }
  function fmtShortPrice(p, deal) {
    if (p == null) return "—";
    if (deal === "rent") return `${num(p / 1000, 0)} т.₽`;
    return p >= 1e6 ? `${num(p / 1e6, 1)} млн` : `${num(p / 1000)} т.₽`;
  }
  function fmtAgo(ts) {
    if (!ts) return "";
    const d = new Date(ts * 1000), now = new Date();
    const days = Math.floor((new Date(now.toDateString()) - new Date(d.toDateString())) / 864e5);
    if (days <= 0) return "сегодня";
    if (days === 1) return "вчера";
    if (days < 7) return `${days} ${plural(days, "день", "дня", "дней")} назад`;
    return d.toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
  }
  function fmtDate(ts) {
    return new Date(ts * 1000).toLocaleString("ru-RU",
      { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  }
  function plural(n, one, few, many) {
    const m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return one;
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
    return many;
  }
  function headline(o) {
    if (o.complex) return `ЖК ${o.complex}`;
    if (o.district) return o.district;
    if (o.street) return `ул. ${o.street}${o.house ? ", " + o.house : ""}`;
    return o.settlement || "";
  }
  // Адрес под параметрами — без того, что уже стоит в заголовке
  function address(o) {
    const head = headline(o);
    const street = o.street ? `ул. ${o.street}${o.house ? ", " + o.house : ""}` : "";
    const ds = [o.district, ...(o.extra_districts || [])].filter(Boolean).join(" / ");
    return [...new Set([ds, o.settlement, street].filter((x) => x && x !== head))].join(" · ");
  }
  function place(o) {
    const street = o.street ? `ул. ${o.street}${o.house ? ", " + o.house : ""}` : "";
    return [o.complex && `ЖК ${o.complex}`, o.district, o.settlement, street].filter(Boolean).join(" · ");
  }
  // Цена в фильтре: продажа — в миллионах, аренда — в тысячах в месяц
  const priceMul = () => (state.deal === "rent" ? 1000 : 1e6);
  const toNum = (v) => {
    const n = parseFloat(String(v).replace(/\s/g, "").replace(",", "."));
    return Number.isFinite(n) ? n : null;
  };

  // ─── адрес страницы ↔ фильтры ────────────────────────────────────────────
  const URL_KEYS = {
    q: "q", deal: "deal", types: "type", rooms: "rooms", priceMin: "pmin", priceMax: "pmax",
    areaMin: "amin", areaMax: "amax", landMin: "lmin", landMax: "lmax", notFirst: "nf",
    notLast: "nl", fresh: "fresh", hot: "hot", districts: "district", complexes: "complex", sort: "sort", view: "view",
  };
  function readUrl() {
    const sp = new URLSearchParams(location.search);
    for (const [k, u] of Object.entries(URL_KEYS)) {
      if (!sp.has(u)) continue;
      const v = sp.get(u);
      if (Array.isArray(state[k])) state[k] = v.split("|").filter(Boolean);
      else if (typeof state[k] === "boolean") state[k] = v === "1";
      else state[k] = v;
    }
    state.rooms = state.rooms.map(Number).filter((n) => n >= 0 && n <= 4);
    if (state.deal !== "rent") state.deal = "sale";
    if (state.view !== "map") state.view = "list";
  }
  function writeUrl() {
    const sp = new URLSearchParams();
    for (const [k, u] of Object.entries(URL_KEYS)) {
      const v = state[k];
      if (Array.isArray(v)) { if (v.length) sp.set(u, v.join("|")); }
      else if (typeof v === "boolean") { if (v) sp.set(u, "1"); }
      else if (v !== "" && !(k === "deal" && v === "sale") && !(k === "sort" && v === "new")
               && !(k === "view" && v === "list")) sp.set(u, v);
    }
    if (openedId && $("detail").open) sp.set("open", openedId);   // открытый объект остаётся ссылкой в адресе
    const s = sp.toString();
    history.replaceState(null, "", s ? `?${s}` : location.pathname);
  }

  function apiParams(extra = {}) {
    const p = new URLSearchParams();
    if (state.q.trim()) p.set("q", state.q.trim());
    p.set("deal", state.deal);
    if (state.types.length) p.set("type", state.types.join(","));
    if (state.rooms.length) p.set("rooms", state.rooms.join(","));
    const pm = toNum(state.priceMin), px = toNum(state.priceMax);
    if (pm != null) p.set("price_min", Math.round(pm * priceMul()));
    if (px != null) p.set("price_max", Math.round(px * priceMul()));
    for (const [k, api] of [["areaMin", "area_min"], ["areaMax", "area_max"], ["landMin", "land_min"], ["landMax", "land_max"]]) {
      const n = toNum(state[k]);
      if (n != null) p.set(api, n);
    }
    if (state.notFirst) p.set("not_first", "1");
    if (state.hot) p.set("hot", "1");   // «🔥 Горячее»
    if (state.fresh === "new1") p.set("new_days", "1");   // «Новое сегодня»: впервые появились за сутки
    else if (state.fresh) p.set("fresh_days", state.fresh);
    for (const d of state.districts) p.append("district", d);
    for (const c of state.complexes) p.append("complex", c);
    p.set("sort", state.sort);
    if (hitSince) p.set("hl", hitSince);
    for (const [k, v] of Object.entries(extra)) p.set(k, v);
    return p;
  }

  // Запросы с тихими повторами: связь моргнула или сервер на миг занят — пробуем ещё,
  // и только если не вышло 3 раза — показываем ошибку. Ждём не дольше 15 секунд.
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  async function fetchRetry(url, opts = {}, tries = 3) {
    const idempotent = !opts.method || opts.method === "GET";
    let last;
    for (let i = 0; i < (idempotent ? tries : 1); i++) {
      const ctl = new AbortController();
      const timer = setTimeout(() => ctl.abort(), 15000);
      try {
        const r = await fetch(url, { credentials: "same-origin", ...opts, signal: ctl.signal });
        clearTimeout(timer);
        if (r.status < 500 || i === tries - 1) return r;
        last = r;
      } catch (e) {
        clearTimeout(timer);
        last = e;
      }
      await sleep(500 * (i + 1) * (i + 1));  // 0,5 с, 2 с
    }
    if (last instanceof Response) return last;
    throw last;
  }

  async function getJSON(url, opts) {
    const r = await fetchRetry(url, opts);
    if (!r.ok) throw new Error(`${r.status}`);
    return r.json();
  }

  // POST/GET с понятной ошибкой: { ok, status, data }
  async function call(url, body, method) {
    const opts = { credentials: "same-origin", method: method || (body !== undefined ? "POST" : "GET") };
    if (body !== undefined) { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(body); }
    try {
      const r = await fetchRetry(url, opts);
      let data = {};
      try { data = await r.json(); } catch { /* пустой ответ */ }
      return { ok: r.ok, status: r.status, data };
    } catch {
      return { ok: false, status: 0, data: { detail: "Нет связи с сайтом. Проверьте интернет." } };
    }
  }
  const me = () => meta.me || null;
  // Логотип «1+1» (план квартиры) — показываем почаще, чтобы нас запоминали
  const LOGO = `<svg class="logo" viewBox="0 0 100 100" aria-hidden="true">
    <g class="walls" fill="none" stroke="currentColor" stroke-width="5" stroke-linecap="round" stroke-linejoin="round">
      <path pathLength="1" d="M4 40V4h92v92H4V60"/><path pathLength="1" d="M49 4v12"/>
      <path pathLength="1" d="M49 27v13h47"/><path pathLength="1" d="M49 59h13"/>
      <path pathLength="1" d="M72 59h24"/><path pathLength="1" d="M49 59v37"/></g>
    <text class="logo-txt" x="27" y="56.5" text-anchor="middle">1+1</text></svg>`;
  const brandLine = (text) => `<div class="brand-line">${LOGO}<span><b>1+1</b> · ${text}</span></div>`;
  const HEART = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20.3s-7.6-4.7-9.3-9.6C1.5 7.1 4 3.9 7.4 3.9c2 0 3.6 1.2 4.6 2.8 1-1.6 2.6-2.8 4.6-2.8 3.4 0 5.9 3.2 4.7 6.8-1.7 4.9-9.3 9.6-9.3 9.6z"/></svg>`;
  const fmtDay = (ts) => new Date(ts * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "long" });

  // ─── отрисовка фильтров ─────────────────────────────────────────────────
  function syncControls() {
    $("q").value = state.q;
    document.querySelectorAll("[data-deal]").forEach((b) => b.classList.toggle("on", b.dataset.deal === state.deal));
    document.querySelectorAll("[data-view]").forEach((b) => b.classList.toggle("on", b.dataset.view === state.view));
    document.querySelectorAll("[data-room]").forEach((b) => {
      const on = state.rooms.includes(Number(b.dataset.room));
      b.classList.toggle("on", on); b.setAttribute("aria-pressed", on);
    });
    document.querySelectorAll("[data-type]").forEach((b) => {
      const on = state.types.includes(b.dataset.type);
      b.classList.toggle("on", on); b.setAttribute("aria-pressed", on);
    });
    for (const k of ["priceMin", "priceMax", "areaMin", "areaMax", "landMin", "landMax"]) $(k).value = state[k];
    $("notFirst").checked = state.notFirst;
    $("fresh").value = state.fresh;
    $("newTodayBtn").classList.toggle("on", state.fresh === "new1");
    $("newTodayBtn").setAttribute("aria-pressed", state.fresh === "new1");
    $("hotBtn").classList.toggle("on", state.hot);
    $("hotBtn").setAttribute("aria-pressed", state.hot);
    $("sort").value = state.sort;
    const rent = state.deal === "rent";
    setSliderFromState();
    renderComplexChosen();
    const fc = filtersCount();
    $("filterBadge").hidden = !fc;
    $("filterBadge").textContent = fc;
    // сколько выбрано среди «ещё фильтров» — видно на кнопке, даже когда они свёрнуты
    const mc = [state.areaMin || state.areaMax, state.landMin || state.landMax, state.notFirst, state.fresh]
      .filter(Boolean).length + state.districts.length + state.complexes.length;
    $("moreN").hidden = !mc;
    $("moreN").textContent = mc;
    $("resetBtn").hidden = !(fc - (state.deal === "rent" ? 1 : 0)) && !state.q;  // кнопка на виду, когда есть что сбрасывать
  }

  // ─── ползунок цены ──────────────────────────────────────────────────────
  // Шаги: мелкие там, где большинство цен, крупные — дальше. Края — «без ограничения».
  function range(from, to, step) { const a = []; for (let x = from; x <= to + 1e-9; x += step) a.push(+x.toFixed(2)); return a; }
  const STEPS = {
    sale: [...range(0, 10, 0.5), ...range(11, 20, 1), ...range(22, 40, 2), ...range(45, 60, 5), ...range(70, 100, 10)],
    rent: [...range(0, 60, 5), ...range(70, 150, 10), ...range(175, 300, 25)],
  };
  const steps = () => STEPS[state.deal === "rent" ? "rent" : "sale"];
  const nearest = (v) => { const st = steps(); let best = 0; st.forEach((x, i) => { if (Math.abs(x - v) < Math.abs(st[best] - v)) best = i; }); return best; };

  function priceText() {
    const unit = state.deal === "rent" ? "тыс. ₽/мес" : "млн";
    const a = toNum(state.priceMin), b = toNum(state.priceMax);
    const f = (x) => num(x, 1);
    if (a == null && b == null) return "любая";
    if (a != null && b != null) return `${f(a)} – ${f(b)} ${unit}`;
    return a != null ? `от ${f(a)} ${unit}` : `до ${f(b)} ${unit}`;
  }

  function paintSlider() {
    const max = steps().length - 1;
    const lo = +$("psMin").value, hi = +$("psMax").value;
    $("psFill").style.left = `${(lo / max) * 100}%`;
    $("psFill").style.right = `${100 - (hi / max) * 100}%`;
    $("priceValue").textContent = priceText();
  }

  function setSliderFromState() {
    const st = steps(), max = st.length - 1;
    $("psMin").max = $("psMax").max = max;
    const a = toNum(state.priceMin), b = toNum(state.priceMax);
    $("psMin").value = a == null ? 0 : nearest(a);
    $("psMax").value = b == null ? max : nearest(b);
    paintSlider();
  }

  function onSlider(which) {
    const st = steps(), max = st.length - 1;
    let lo = +$("psMin").value, hi = +$("psMax").value;
    if (lo > hi) { if (which === "min") lo = hi; else hi = lo; $("psMin").value = lo; $("psMax").value = hi; }
    state.priceMin = lo > 0 ? String(st[lo]) : "";
    state.priceMax = hi < max ? String(st[hi]) : "";
    paintSlider();
    refresh(350);
  }

  function renderTypes() {
    $("types").innerHTML = Object.entries(meta.types || {}).map(([k, label]) =>
      `<button type="button" class="chip" data-type="${esc(k)}" aria-pressed="false">${esc(label)}</button>`).join("");
  }

  function renderDistrictChosen() {
    $("districts").innerHTML = state.districts.map((d) =>
      `<button type="button" class="chip on x" data-district="${esc(d)}" aria-label="Убрать район ${esc(d)}">${esc(d)}</button>`).join("");
  }
  function renderComplexChosen() {
    $("complexChosen").innerHTML = state.complexes.map((c) =>
      `<button type="button" class="chip on x" data-complex="${esc(c)}" aria-label="Убрать ЖК ${esc(c)}">${esc(c)}</button>`).join("");
  }

  async function loadFacets() {
    const seq = reqSeq;
    let f;
    try { f = await getJSON(`/api/facets?${apiParams()}`); } catch { return; }
    if (seq !== reqSeq) return;
    const counts = new Map(f.districts.map((d) => [d.name, d.n]));
    districtCounts = counts;
    const names = [...f.districts.map((d) => d.name), ...state.districts.filter((d) => !counts.has(d))];
    // районы и ЖК — список с поиском и галочками (pickers.js); выбранные — ещё и чипами ниже (✕ — убрать)
    $("fDistricts").innerHTML = names.map((n) => `<option value="${esc(n)}" data-meta="${counts.get(n) ?? 0}"${state.districts.includes(n) ? " selected" : ""}>${esc(n)}</option>`).join("");
    facetComplexes = f.complexes;   // для выпадающего списка ЖК
    const cx = [...f.complexes, ...state.complexes.filter((c) => !f.complexes.some((x) => x.name === c)).map((c) => ({ name: c, n: 0 }))];
    $("fComplexes").innerHTML = cx.map((c) => `<option value="${esc(c.name)}" data-meta="${c.n}"${state.complexes.includes(c.name) ? " selected" : ""}>${esc(c.name)}</option>`).join("");
    renderDistrictChosen();
  }

  // ─── сравнение 2–3 объектов ─────────────────────────────────────────────
  const CMP_MAX = 5;
  let cmp = [];
  try { cmp = JSON.parse(localStorage.getItem("cmp") || "[]").filter(Number.isInteger).slice(0, CMP_MAX); } catch { cmp = []; }
  const cmpSave = () => { try { localStorage.setItem("cmp", JSON.stringify(cmp)); } catch { /* приватный режим */ } };
  function toggleCompare(id) {
    if (cmp.includes(id)) cmp = cmp.filter((x) => x !== id);
    else {
      if (cmp.length >= CMP_MAX) { toast(`Сравнить можно до ${CMP_MAX} объектов — уберите один`); return; }
      cmp.push(id);
    }
    cmpSave();
    document.querySelectorAll(`[data-cmp="${id}"]`).forEach((b) => b.classList.toggle("on", cmp.includes(id)));
    renderCmpBar();
    if (cmp.length === 1 && cmp.includes(id)) toast("Добавлено к сравнению — выберите ещё 1–2 объекта", 2600);
  }
  function renderCmpBar() {
    $("cmpBar").hidden = !cmp.length;
    $("cmpText").textContent = `⚖ К сравнению: ${cmp.length} из ${CMP_MAX}`;
    $("cmpOpen").disabled = cmp.length < 2;
  }
  async function openCompare() {
    const dlg = $("cmpDlg");
    $("cmpBody").innerHTML = `<p class="note">Загружаем…</p>`;
    openDlg(dlg);
    const list = (await Promise.all(cmp.map((id) => getJSON(`/api/listings/${id}`).catch(() => null)))).filter(Boolean);
    list.forEach((o) => known.set(o.id, o));
    const best = (vals, low = true) => {
      const xs = vals.filter((v) => v != null);
      if (xs.length < 2 || xs.every((v) => v === xs[0])) return null;   // всё одинаково — подсвечивать нечего
      return low ? Math.min(...xs) : Math.max(...xs);
    };
    const row = (label, get, fmt, pick) => {
      const vals = list.map(get);
      if (vals.every((v) => v == null || v === "")) return "";   // у всех пусто — строку не показываем
      const b = pick ? best(vals, pick === "low") : null;
      return `<tr><th>${esc(label)}</th>${vals.map((v) => `<td${b != null && v === b ? ' class="best"' : ""}>${v == null || v === "" ? "—" : esc(fmt ? fmt(v) : v)}</td>`).join("")}</tr>`;
    };
    const rooms = (o) => (o.rooms == null ? null : o.rooms === 0 ? "студия" : `${o.rooms}`);
    const floor = (o) => (o.floor ? `${o.floor}${o.floors ? " из " + o.floors : ""}` : null);
    $("cmpBody").innerHTML = `<div class="cmp-wrap"><table class="cmp-t">
      <thead><tr><th></th>${list.map((o) => `<td>
        ${o.photos && o.photos[0] ? `<img src="${esc(o.photos[0])}" alt="" loading="lazy">` : `<div class="cmp-nophoto">${LOGO}</div>`}
        <button type="button" class="link" data-open-id="${o.id}">${esc(headline(o) || o.title)}</button>
        <button type="button" class="cmp-x" data-cmp-del="${o.id}" aria-label="Убрать из сравнения">✕</button></td>`).join("")}</tr></thead>
      <tbody>
        ${row("Цена", (o) => o.price, (v) => fmtPrice(v, list[0].deal), "low")}
        ${row("Цена за м²", (o) => (o.deal !== "rent" ? o.price_m2 : null), (v) => `${num(v)} ₽`, "low")}
        ${row("Рынок", (o) => (o.market ? o.market.diff : null), (v) => (v < 0 ? `дешевле на ${-v}%` : v > 0 ? `дороже на ${v}%` : "по рынку"), "low")}
        ${row("Что", (o) => o.title)}
        ${row("Комнат", rooms)}
        ${row("Площадь", (o) => o.area, (v) => `${num(v, 1)} м²`, "high")}
        ${row("Участок", (o) => o.land, (v) => `${num(v, 1)} сот.`, "high")}
        ${row("Этаж", floor)}
        ${row("ЖК", (o) => o.complex)}
        ${row("Район", (o) => o.district)}
        ${row("Адрес", (o) => (o.street ? `ул. ${o.street}${o.house ? ", " + o.house : ""}` : o.settlement))}
        ${row("На 1+1", (o) => daysOnSale(o), (v) => daysText(v), "high")}
        ${row("Цена менялась", (o) => (o.price_history && o.price_history.length > 1 ? o.price_history.length - 1 : null), (v) => `${v} раз`)}
        ${row("Фото", (o) => (o.photos ? o.photos.length : 0) || null, (v) => `${v}`)}
      </tbody></table></div>
      <p class="note">Подсвечено лучшее значение в строке. Нажмите на название — откроется объект.</p>`;
  }

  // Статусы по объекту — простая CRM агента (видит только он)
  const STATUSES = { call: "📞 Звонил", show: "👀 Показ", think: "🤔 Думает", refuse: "❌ Отказ" };
  let favItems = [], favStatus = "";
  const myStatus = (id) => (me() && me().statuses ? me().statuses[id] : null);

  // ─── результаты ─────────────────────────────────────────────────────────
  // Отметки на карточке: новое за сутки, цена снизилась (за 2 недели), заметно ниже рынка
  function badgesHTML(o) {
    const now = loadedAt || Date.now() / 1000;
    const b = [];
    const st = myStatus(o.id);
    if (st) b.push(`<span class="mark crm crm-${st}">${STATUSES[st]}</span>`);
    if (o.source === "feed") b.push(`<span class="mark partner" title="Объект партнёра «СТРЕЛЫ»">${STRELY}СТРЕЛЫ</span>`);
    if (o.verified && o.source !== "feed") b.push(`<span class="mark verified" title="Агент подтвердил свой номер в 1+1">✓ ${innerWidth < 640 ? "Проверен" : "Проверенный агент"}</span>`);
    if (o.first_seen > now - 86400) b.push(`<span class="mark new">Новое</span>`);
    if (o.prev_price && o.price && o.prev_price > o.price && o.price_changed_at > now - 14 * 86400) {
      b.push(`<span class="mark down">Цена ↓ ${esc(fmtShortPrice(o.prev_price - o.price, o.deal))}</span>`);
    }
    if (o.market_diff != null) b.push(`<span class="mark good" title="${o.market_n ? `сравнили с ${o.market_n} похожими объектами` : ""}">ниже рынка на ${Math.abs(o.market_diff)}%</span>`);
    if (o.price_doubt) b.push(`<span class="mark doubt" title="Цена в объявлении записана неоднозначно — уточните у агента">цена под вопросом</span>`);
    return b.length ? `<div class="marks">${b.join("")}</div>` : "";
  }

  // Сколько дней объект в продаже (с первого появления у нас)
  const daysOnSale = (o) => Math.max(0, Math.floor(((loadedAt || Date.now() / 1000) - o.first_seen) / 86400));
  const daysText = (n) => (n < 1 ? "меньше суток" : `${n} ${plural(n, "день", "дня", "дней")}`);
  // «На 1+1 — 5 дней»: сколько объявление уже у нас (с первого появления в чатах риелторов)
  const onSite = (n) => (n < 1 ? "Новое на 1+1" : `На 1+1 — ${daysText(n)}`);
  const updated = (ts) => { const a = fmtAgo(ts); return a ? `обновлено ${a}` : ""; };

  // знак «СТРЕЛЫ» — две стрелки (бренд агентства-партнёра)
  const STRELY = `<svg class="strely-ic" viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 12.5L8 7M8 7V11M8 7H4"/><path d="M7.5 9.5L13 4M13 4V8M13 4H9"/></svg>`;

  // «Клиенту ▾» (ТЗ v2): одна кнопка вместо «В подборку» и «Клиенту». Выбрали клиента — объект в его подборке
  // (статус «Отправлен»), ссылку можно сразу скопировать. Новый клиент — прямо здесь. «Без клиента» — вторым пунктом.
  const PAIR_L = { matched: "подобран", sent: "отправлен", opened: "открыл", liked: "понравился", rejected: "не подходит",
    show: "показ", talks: "переговоры", deal: "сделка", lost: "отказ" };
  let cmData = null, cmFor = 0, cmDone = null;
  function cmDlg() {
    let dlg = $("clientDlg");
    if (!dlg) {
      dlg = document.createElement("dialog");
      dlg.id = "clientDlg";
      dlg.className = "small-dlg client-dlg";
      document.body.appendChild(dlg);
    }
    return dlg;
  }
  async function openClientMenu(id) {
    cmFor = id; cmDone = null;
    const dlg = cmDlg();
    const o = known.get(id) || {};
    dlg.innerHTML = `<div class="dlg-body"><div class="cm-head"><h2>Клиенту</h2>
        <button type="button" class="close" data-close-dlg aria-label="Закрыть">✕</button></div>
      ${o.title ? `<p class="cm-obj">${esc(headline(o) || o.title)} · ${esc(fmtPrice(o.price, o.deal))}</p>` : ""}
      <label class="cm-search">${ic("search")}<input id="cmQ" placeholder="Найти клиента" autocomplete="off" aria-label="Найти клиента"></label>
      <div class="cm-done" id="cmDone" hidden></div>
      <div class="cm-list"><p class="note">Загружаем…</p></div>
      <details class="cm-newbox"><summary>${ic("plus")}Новый клиент</summary>
        <form class="cm-new-f" id="cmNew" autocomplete="off"><input name="name" maxlength="80" placeholder="Имя" required>
          <input name="phone" maxlength="30" inputmode="tel" placeholder="Телефон (необязательно)">
          <button type="submit" class="pill">Создать и добавить</button></form></details>
      <button type="button" class="cm-nopick" data-pick-open="${id}">${ic("folder")}В подборку без клиента…</button></div>`;
    if (!dlg.open) dlg.showModal();
    const r = await call(`/api/clients?listing=${id}`);
    if (!r.ok) { dlg.querySelector(".cm-list").innerHTML = `<p class="note">Не получилось загрузить клиентов.</p>`; return; }
    cmData = r.data;
    cmRender();
    $("cmQ").addEventListener("input", cmRender);
    $("cmNew").addEventListener("submit", cmCreate);
  }
  function cmRender() {
    const box = cmDlg().querySelector(".cm-list");
    const q = ($("cmQ") && $("cmQ").value.trim().toLowerCase()) || "";
    const has = new Map(cmData.items.map((x) => [x.id, x.status]));
    const list = cmData.clients.filter((c) => !q || c.name.toLowerCase().includes(q))
      .sort((a, b) => (has.has(b.id) ? 1 : 0) - (has.has(a.id) ? 1 : 0));
    box.innerHTML = list.length ? list.map((c) => `<button type="button" class="cm-it${has.has(c.id) ? " on" : ""}" data-client-add="${c.id}">
        <span class="cm-ava">${esc((c.name || "К")[0].toUpperCase())}</span><span class="cm-name">${esc(c.name)}</span>
        <small>${has.has(c.id) ? esc(PAIR_L[has.get(c.id)] || has.get(c.id)) : "в подборку"}</small></button>`).join("")
      : `<p class="note">${cmData.clients.length ? "Никого не нашли." : "Клиентов пока нет — создайте ниже."}</p>`;
    const done = $("cmDone");
    done.hidden = !cmDone;
    if (cmDone) done.innerHTML = `${ic("check")}<span>Добавлено: <b>${esc(cmDone.name)}</b> — в подборке «${esc(cmDone.pick.title || "")}»</span>
      <button type="button" class="pill light" data-copy-url="${esc(cmDone.pick.url || "")}">${ic("link")}Скопировать ссылку</button>`;
  }
  async function cmAdd(cid) {
    const r = await call(`/api/clients/${cid}/add`, { listing_id: cmFor });
    if (!r.ok) { toast(r.data.detail || "Не получилось", 3500); return; }
    const c = cmData.clients.find((x) => x.id === cid) || { name: "" };
    cmDone = { name: c.name, pick: r.data.pick || {} };
    const rr = await call(`/api/clients?listing=${cmFor}`);
    if (rr.ok) cmData = rr.data;
    cmRender();
  }
  async function cmCreate(e) {
    e.preventDefault();
    const f = e.target;
    const r = await call("/api/clients", { name: f.name.value.trim(), phone: f.phone.value });
    if (!r.ok) { toast(r.data.detail || "Не получилось"); return; }
    cmData.clients.unshift({ id: r.data.client.id, name: r.data.client.name });
    f.reset(); f.closest("details").open = false;
    await cmAdd(r.data.client.id);
  }

  function itemHTML(o) {
    const hit = hitSince && o.first_seen > hitSince;   // новое в этом поиске — выделяем один раз
    const tag = (hit ? `<div class="marks"><span class="mark hit">Новое в вашем поиске</span></div>` : "") + badgesHTML(o);
    const m2 = o.price_m2 && o.deal !== "rent" ? `<div class="price-m2">${num(o.price_m2)} ₽/м²</div>` : "";
    const n = (o.photos || []).length;
    const photo = n
      ? `<div class="item-photo" data-ph="0"><img src="${esc(thumb(o.photos[0]))}" data-full="${esc(o.photos[0])}" alt="${esc(o.title)}" loading="lazy" decoding="async">${n > 1 ? `
          <button type="button" class="ph-nav prev" data-ph-step="-1" aria-label="Предыдущее фото">${ic("chevron-left")}</button>
          <button type="button" class="ph-nav next" data-ph-step="1" aria-label="Следующее фото">${ic("chevron")}</button>
          <span class="ph-count">1 / ${n}</span>` : ""}</div>`
      : `<div class="item-photo none" aria-hidden="true">${LOGO}<span>Фото у агента</span></div>`;
    const fav = me() && me().favorites.includes(o.id);
    const addr = address(o);
    return `<li class="item has-photo${hit ? " hit" : ""}" tabindex="0" data-id="${o.id}">
      ${photo}
      <button type="button" class="fav-btn${fav ? " on" : ""}" data-fav="${o.id}" aria-label="${fav ? "Убрать из избранного" : "В избранное"}">${HEART}</button>
      ${headline(o) ? `<h3 class="item-head">${esc(headline(o))}</h3>` : ""}
      <div class="item-price"><div class="price">${esc(fmtPrice(o.price, o.deal))}</div>${m2}</div>
      <div class="item-title">${esc(o.title)}</div>
      ${tag}
      ${addr ? `<div class="item-place">${esc(addr)}</div>` : ""}
      ${o.description ? `<p class="item-desc">${esc(o.description)}</p>` : ""}
      <div class="item-foot">
        <div class="item-meta" title="Сколько дней объявление на 1+1 (с первого появления в чатах риелторов) и когда его присылали последний раз"><span class="on-sale">${esc(onSite(daysOnSale(o)))}</span> · ${esc(updated(o.last_seen))}</div>
        ${favMode ? `<a class="plan-btn" href="/planner?add=show&listing=${o.id}" title="Запланировать показ или звонок" aria-label="Запланировать">📅</a>` : ""}
        ${me() ? `<button type="button" class="cmp-btn cli-btn" data-client-open="${o.id}" title="Клиенту: в подборку клиента" aria-label="Клиенту">${ic("users")}</button>` : ""}
        <button type="button" class="cmp-btn${cmp.includes(o.id) ? " on" : ""}" data-cmp="${o.id}" title="Сравнить" aria-label="Добавить к сравнению">⚖</button>
      </div>
    </li>`;
  }

  function skeletons(n) {
    return Array.from({ length: n }, () => `<li class="item skel" aria-hidden="true"><i></i><i></i><i></i><i></i></li>`).join("");
  }

  // Миниатюра для ленты (акт 14.10): у фото с сайта СТРЕЛ (Тильда) есть готовые уменьшенные копии — 480 px вместо 250–320 КБ.
  // Не загрузилась миниатюра — подставляем исходное фото (слушатель ошибок ниже).
  function thumb(u) {
    const m = /^https:\/\/static\.tildacdn\.com\/([^/]+)\/([^/?#]+)$/.exec(u || "");
    return m ? `https://optim.tildacdn.com/${m[1]}/-/resize/480x/${m[2]}` : u;
  }
  document.addEventListener("error", (e) => {
    const img = e.target;
    if (img.tagName === "IMG" && img.dataset.full && img.src !== img.dataset.full) img.src = img.dataset.full;
  }, true);
  // «Пишите как в чате»: как сервис понял фразу из строки поиска — чипами; ✕ убирает эту часть из запроса
  function understoodHTML(u) {
    if (!u || (!u.chips.length && !u.text)) return "";
    return `<span class="understood"><span class="u-lbl">Поняли так:</span>${u.chips.map((c) =>
      `<button type="button" class="chip u-chip" data-u-drop="${esc(c.drop)}" title="Убрать из запроса">${esc(c.label)} <i>✕</i></button>`).join("")}
      ${u.text ? `<span class="u-text">и в тексте: «${esc(u.text)}»</span>` : ""}</span>`;
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-u-drop]");
    if (!b) return;
    const drop = b.dataset.uDrop.toLowerCase().replace(/ё/g, "е");
    const q = state.q.toLowerCase().replace(/ё/g, "е");
    const at = q.indexOf(drop);
    state.q = (at >= 0 ? state.q.slice(0, at) + state.q.slice(at + drop.length) : state.q).replace(/\s+/g, " ").trim();
    $("q").value = state.q;
    refresh();
  });
  async function loadList(append = false, keepFav = false) {
    const seq = append ? reqSeq : ++reqSeq;
    if (!append) {
      page = 1;
      const res = $("results");
      if (!res.querySelector(".item:not(.skel)")) res.innerHTML = skeletons(8);
      else res.classList.add("busy");
    }
    let data;
    try {
      if (favMode) {
        if (!keepFav || !favItems.length) favItems = (await getJSON("/api/favorites")).items;   // фильтр по статусу — без запроса
        const items = favStatus ? favItems.filter((o) => (favStatus === "none" ? !o.status : o.status === favStatus)) : favItems;
        data = { total: items.length, page: 1, pages: 1, items };
      } else {
        data = await getJSON(`/api/listings?${apiParams({ page, size: PAGE_SIZE })}`);
      }
    } catch {
      if (seq === reqSeq) $("count").textContent = "Не получилось загрузить. Обновите страницу.";
      return;
    }
    if (seq !== reqSeq) return;
    $("results").classList.remove("busy");
    if (!append && !favMode) {
      loadedAt = data.now || Math.floor(Date.now() / 1000);
      lastTotal = data.total;
      hideNewPill();
    }
    $("sheetApply").textContent = data.total ? `Показать ${num(data.total)} ${plural(data.total, "объект", "объекта", "объектов")}` : "Ничего не нашлось";
    $("count").innerHTML = favMode
      ? `Избранное: ${num(data.total)} <button type="button" class="link-btn" id="favExit">← ко всем объектам</button>
        <a class="link-btn" href="/picks">📁 Подборки для клиентов</a> <a class="link-btn" href="/planner">📅 Планер</a>${crmChips()}`
      : esc(data.total ? `${num(data.total)} ${plural(data.total, "объект", "объекта", "объектов")}` : "Ничего не нашлось")
        + understoodHTML(data.understood);
    if (!append && data.understood && data.understood.deal && data.understood.deal !== state.deal) {   // «аренда 1к» — переключатель тоже на аренду
      state.deal = data.understood.deal;
      syncControls();
    }
    // Карточки появляются волной: у каждой своя небольшая задержка
    data.items.forEach((o) => known.set(o.id, o));
    const html = data.items.map((o, i) => itemHTML(o).replace('<li class="item', `<li style="--d:${Math.min(i, 12) * 35}ms" class="item`)).join("");
    if (append) $("results").insertAdjacentHTML("beforeend", html);
    else $("results").innerHTML = html || (favMode
      ? `<li class="empty"><span class="empty-logo">${LOGO}</span><b>Пока пусто</b>Нажмите ♡ на объекте, чтобы сохранить его сюда.</li>`
      : `<li class="empty"><span class="empty-logo">${LOGO}</span><b>Ничего не нашлось</b>Попробуйте убрать часть фильтров или изменить запрос.</li>`);
    $("loadMore").hidden = state.view !== "list" || data.page >= data.pages;
  }

  // Фильтр «Избранного» по статусам: сколько объектов в каждом
  function crmChips() {
    const n = (k) => favItems.filter((o) => (k === "none" ? !o.status : o.status === k)).length;
    const chip = (k, label) => `<button type="button" class="chip${favStatus === k ? " on" : ""}" data-crm-filter="${k}">${label} <b>${n(k) || ""}</b></button>`;
    const used = Object.keys(STATUSES).filter((k) => n(k));
    if (!used.length) return "";
    return `<div class="chips crm-chips"><button type="button" class="chip${favStatus ? "" : " on"}" data-crm-filter="">Все</button>
      ${used.map((k) => chip(k, STATUSES[k])).join("")}${n("none") ? chip("none", "Без статуса") : ""}</div>`;
  }

  // ─── карта ──────────────────────────────────────────────────────────────
  function ensureMap() {
    if (map || !window.L) return;
    map = L.map("mapBox", { scrollWheelZoom: true, zoomControl: false, attributionControl: false })
      .setView([45.035, 38.975], 11);
    L.control.zoom({ position: "bottomright" }).addTo(map);
    // Подпись источника карты обязательна по условиям OpenStreetMap — оставляем маленькой, без флага и «Leaflet»
    L.control.attribution({ prefix: false, position: "bottomleft" })
      .addAttribution('© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>')
      .addTo(map);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, className: "map-tiles" }).addTo(map);
    // Группы объектов — круглые значки с числом; чем больше объектов, тем крупнее
    cluster = L.markerClusterGroup ? L.markerClusterGroup({
      showCoverageOnHover: false, maxClusterRadius: 55, spiderfyOnMaxZoom: true,
      iconCreateFunction: (c) => {
        const n = c.getAllChildMarkers().reduce((t, m) => t + (m.options.n || 1), 0);   // метка ЖК = её число объектов
        const size = n < 10 ? 40 : n < 50 ? 48 : 58;
        return L.divIcon({ className: "cl-wrap", iconSize: [size, size], html: `<div class="cl"><span>${n}</span></div>` });
      },
    }) : L.layerGroup();
    map.addLayer(cluster);
    initDistrictLayer();
    $("mapBox").addEventListener("click", (e) => {
      const b = e.target.closest("[data-open]");
      if (b) openDetail(Number(b.dataset.open));
      const cx = e.target.closest("[data-cx-list]");
      if (cx) {   // метка ЖК → этот ЖК в фильтр и список
        if (!state.complexes.includes(cx.dataset.cxList)) state.complexes.push(cx.dataset.cxList);
        state.view = "list";
        map.closePopup();
        syncControls();
        refresh();
      }
    });
  }

  // Слой районов: контуры поверх подложки, под метками. Наведение — название и число объектов,
  // нажатие — район в фильтр (ещё раз — убрать). Включается кнопкой «Районы» на карте (запоминается).
  let districtLayer = null, districtShapes = [], districtCounts = new Map();
  const districtLabels = window.L ? L.layerGroup() : null;
  // Цвета — hex (Leaflet не понимает CSS-переменные): бирюза 1+1 и шампань
  const DZ_STYLE = { weight: 2, color: "#035352", opacity: .8, fillColor: "#e3cb8f", fillOpacity: .1, lineJoin: "round" };
  const DZ_HOVER = { weight: 3, color: "#035352", opacity: 1, fillColor: "#e3cb8f", fillOpacity: .26 };
  const DZ_ON = { weight: 3.5, color: "#b8892f", opacity: 1, fillColor: "#e3cb8f", fillOpacity: .32 };
  const DZ_HOVER_ON = { ...DZ_ON, fillOpacity: .42 };
  const dzOn = () => { try { return localStorage.getItem("mapDistricts") !== "0"; } catch { return true; } };
  function initDistrictLayer() {
    map.createPane("districts").style.zIndex = 350;
    map.on("zoomend", styleDistricts);
    const Ctl = L.Control.extend({
      onAdd() {
        const b = L.DomUtil.create("button", "dz-toggle");
        b.type = "button";
        b.innerHTML = `${ic("map")}<span>Районы</span>`;
        b.classList.toggle("on", dzOn());
        L.DomEvent.disableClickPropagation(b);
        b.addEventListener("click", () => {
          const on = !dzOn();
          try { localStorage.setItem("mapDistricts", on ? "1" : "0"); } catch { /* */ }
          b.classList.toggle("on", on);
          showDistricts();
        });
        return b;
      },
    });
    new Ctl({ position: "topleft" }).addTo(map);
    showDistricts();
  }
  async function showDistricts() {
    if (!dzOn()) { if (districtLayer) districtLayer.remove(); return; }
    if (!districtLayer) {
      let d;
      try { d = await getJSON("/api/districts/map"); } catch { return; }
      districtLayer = L.layerGroup();
      districtShapes = d.districts.map((z) => {
        const shape = L.polygon(z.rings, { pane: "districts", ...DZ_STYLE, className: "dz-shape" });
        shape.bindTooltip(() => {
          const n = districtCounts.get(z.name);
          return `<b>${esc(z.name)}</b><br>${n ? `${num(n)} ${plural(n, "объект", "объекта", "объектов")}` : "пока нет объектов"}`;
        }, { sticky: true, direction: "top", className: "dz-tip", opacity: 1 });
        shape.on("mouseover", () => shape.setStyle(state.districts.includes(z.name) ? DZ_HOVER_ON : DZ_HOVER));
        shape.on("mouseout", () => styleDistricts());
        shape.on("click", () => {
          const had = state.districts.includes(z.name);
          toggle(state.districts, z.name);
          toast(had ? `Район «${z.name}» убран из фильтра` : `Показываем район «${z.name}»`);
          syncControls();
          refresh();
        });
        shape.zoneName = z.name;
        districtLayer.addLayer(shape);
        // название района в его середине — видно при приближении (на всём городе — только у выбранных)
        const label = L.marker(shape.getBounds().getCenter(), { pane: "districts", interactive: false, keyboard: false,
          icon: L.divIcon({ className: "dz-label-wrap", iconSize: null, html: `<span class="dz-label">${esc(z.name)}</span>` }) });
        label.zoneName = z.name;
        districtLabels.addLayer(label);
        return shape;
      });
    }
    districtLayer.addTo(map);
    districtLabels.addTo(map);
    styleDistricts();
  }
  function styleDistricts() {
    for (const sh of districtShapes) sh.setStyle(state.districts.includes(sh.zoneName) ? DZ_ON : DZ_STYLE);
    const zoomed = map && map.getZoom() >= 13;
    districtLabels.eachLayer((l) => {
      const el = l.getElement();
      if (el) el.classList.toggle("show", zoomed || state.districts.includes(l.zoneName));
      if (el) el.classList.toggle("on", state.districts.includes(l.zoneName));
    });
  }

  function popupHTML(p, extra) {
    const head = p.complex ? `ЖК ${p.complex}` : (p.district || (p.street ? `ул. ${p.street}` : ""));
    return `<div class="map-card" data-pid="${p.id}">
      ${p.photo ? `<img src="${esc(thumb(p.photo))}" data-full="${esc(p.photo)}" alt="${esc(p.title)}" loading="lazy">` : ""}
      <div class="mc-body">
        ${head ? `<div class="mc-head">${esc(head)}</div>` : ""}
        <div class="mc-price">${esc(fmtPrice(p.price, state.deal))}</div>
        <div class="mc-title">${esc(p.title)}</div>
        ${p.level && p.level !== "house" ? `<div class="mc-approx">Место примерное — в объявлении нет точного адреса</div>` : ""}
        <div class="mc-extra">${extra ?? `<div class="skel"><i></i><i></i></div>`}</div>
        <span class="mc-more" data-open="${p.id}">Подробнее →</span>
        ${me() && me().is_admin ? `<button type="button" class="mc-geo" data-geo-edit="${p.id}">📍 поправить</button>` : ""}
        <span class="mc-logo" aria-hidden="true">${LOGO}</span>
      </div></div>`;
  }

  // В окошке метки: параметры, адрес, цена за м² и телефон (если есть доступ)
  async function fillPopup(popup, p) {
    const r = await call(`/api/listings/${p.id}`);
    if (!r.ok) { popup.setContent(popupHTML(p, "")); return; }
    const o = r.data;
    known.set(o.id, o);
    const addr = address(o);
    const phones = o.phones && o.phones.length
      ? `<div class="mc-phones">${o.phones.map((ph) => `<a class="mc-call" href="tel:${esc(ph)}">${esc(ph)}</a>
          <button type="button" class="mc-wa" data-max="${esc(ph)}">MAX</button>`).join("")}</div>`
      : o.phones_masked && o.phones_masked.length
          ? `<div class="mc-phones locked"><span>${esc(o.phones_masked[0])}</span>${me() ? "" : `<button type="button" class="mc-call" data-login>Войти и открыть номер</button>`}</div>` : "";
    // Окошко собираем заново уже с адресом и телефоном (update() вернул бы исходный текст)
    popup.setContent(popupHTML(p, `
      ${addr ? `<div class="mc-addr">${esc(addr)}</div>` : ""}
      ${o.price_m2 && o.deal !== "rent" ? `<div class="mc-m2">${esc(num(o.price_m2))} ₽/м² · ${esc(fmtAgo(o.last_seen))}</div>` : ""}
      ${phones}`));
  }

  async function loadMap() {
    ensureMap();
    if (!map) return;
    const seq = reqSeq;
    let res;
    try { res = await getJSON(`/api/map?${apiParams()}`); } catch { return; }
    if (seq !== reqSeq) return;
    const pts = res.points;
    const cxs = res.complexes || [];
    // Сколько найденных объектов на карте — чтобы было видно, что ничего не потерялось
    const onMap = pts.length + cxs.reduce((s, c) => s + c.n, 0);
    const off = res.total - onMap;
    const vague = res.approx_hidden ? ` ${num(res.approx_hidden)} — адрес уточняется (известна только улица или район).` : "";
    $("mapNote").textContent = off > 0
      ? `На карте ${num(onMap)} из ${num(res.total)}.${vague} ${res.pending ? `Ещё ${num(res.pending)} — ищем адрес на карте.` : ""} Все они есть в списке.`
      : `На карте все ${num(res.total)} ${plural(res.total, "объект", "объекта", "объектов")}.`;
    $("mapNote").hidden = !res.total;
    cluster.clearLayers();
    styleDistricts();
    const markers = pts.map((p) => {
      const icon = L.divIcon({
        className: "pin-wrap", iconSize: null,
        html: `<div class="pin${p.level && p.level !== "house" ? " approx" : ""}">${esc(fmtShortPrice(p.price, state.deal))}</div>`,
      });
      const mk = L.marker([p.lat, p.lon], { icon, riseOnHover: true })
        .bindPopup(popupHTML(p), { closeButton: false, className: "map-pop", offset: [0, -30], maxWidth: 280, minWidth: 250 });
      mk.on("popupopen", (e) => fillPopup(e.popup, p));
      return mk;
    });
    // ЖК без точного дома — одна метка ЖК с числом объектов (а не гора меток в одной точке); нажатие — ЖК в фильтр, списком
    cxs.forEach((c) => {
      const icon = L.divIcon({ className: "pin-wrap", iconSize: null,
        html: `<div class="pin cx">${esc(c.complex.length > 22 ? c.complex.slice(0, 21) + "…" : c.complex)} · ${num(c.n)}</div>` });
      const mk = L.marker([c.lat, c.lon], { icon, riseOnHover: true, n: c.n }).bindPopup(`<div class="map-card"><div class="mc-body">
          <div class="mc-head">ЖК ${esc(c.complex)}</div>
          <div class="mc-title">${num(c.n)} ${plural(c.n, "объект", "объекта", "объектов")}${c.price_min ? ` · от ${esc(fmtPrice(c.price_min, state.deal))}` : ""}</div>
          <div class="mc-approx">Точка — центр ЖК: дом в объявлениях не указан</div>
          <span class="mc-more" data-cx-list="${esc(c.complex)}">Показать списком →</span></div></div>`,
        { closeButton: false, className: "map-pop", offset: [0, -30], maxWidth: 280, minWidth: 220 });
      markers.push(mk);
    });
    if (cluster.addLayers) cluster.addLayers(markers); else markers.forEach((m) => cluster.addLayer(m));
    const chosen = districtShapes.filter((sh) => state.districts.includes(sh.zoneName));
    const all = pts.map((p) => [p.lat, p.lon]).concat(cxs.map((c) => [c.lat, c.lon]));
    if (all.length) map.flyToBounds(coreBounds(all).pad(0.1), { maxZoom: 15, duration: 0.6 });
    if (mapScrollPending) {
      mapScrollPending = false;
      requestAnimationFrame(() => { $("mapBox").scrollIntoView({ block: "start" }); map.invalidateSize(); });
    }
    else if (chosen.length) map.flyToBounds(chosen.reduce((b, sh) => b.extend(sh.getBounds()), L.latLngBounds([])).pad(0.1), { maxZoom: 15, duration: 0.6 });   // объектов нет — к району
    setTimeout(() => map.invalidateSize(), 0);
  }

  function applyView() {
    const isMap = state.view === "map";
    $("mapBox").hidden = !isMap;
    if (!isMap) $("mapNote").hidden = true;
    $("results").hidden = isMap;
    if (isMap) $("loadMore").hidden = true;
    // акт 14.10: карта — отдельный экран: на всю высоту, без приветствия, сразу на виду
    const was = document.body.classList.contains("map-mode");
    document.body.classList.toggle("map-mode", isMap);
    if (isMap && !was) mapScrollPending = true;   // прокрутим к карте, когда она нарисуется (loadMap)
  }
  let mapScrollPending = false;
  // Масштаб карты — по основной массе точек: одна далёкая точка (Тимашевск, Выселки) не уводит карту за город
  function coreBounds(ll) {
    if (ll.length < 20) return L.latLngBounds(ll);
    const med = (a) => a.slice().sort((x, y) => x - y)[a.length >> 1];
    const c = [med(ll.map((p) => p[0])), med(ll.map((p) => p[1]))];
    const near = ll.map((p) => [Math.hypot(p[0] - c[0], (p[1] - c[1]) * 0.7), p]).sort((a, b) => a[0] - b[0]);
    return L.latLngBounds(near.slice(0, Math.ceil(near.length * 0.95)).map((x) => x[1]));
  }

  // ─── всё вместе ─────────────────────────────────────────────────────────
  let timer = 0;
  function refresh(delay = 0) {
    if (activeSaved && !applyingSaved()) { activeSaved = 0; setHits(0); renderSaved(); }
    clearTimeout(timer);
    timer = setTimeout(() => {
      writeUrl();
      applyView();
      loadList();
      loadFacets();
      if (state.view === "map") loadMap();
    }, delay);
  }

  // ─── карточка объекта ───────────────────────────────────────────────────
  function fact(label, value) {
    return value == null || value === "" ? "" : `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`;
  }
  const MATCH = { new: "первое сообщение", repost: "репост", "same-object": "тот же объект" };

  // Раскрывающаяся строка окна объекта: иконка, название, короткая подсказка справа
  const acc = (icon, title, hint, body, opts = {}) => body ? `<details class="dv-acc${opts.cls ? " " + opts.cls : ""}"${opts.open ? " open" : ""}>
      <summary><span class="dv-acc-ic">${icon}</span><span class="dv-acc-t">${title}</span>${hint ? `<span class="dv-acc-h">${hint}</span>` : ""}</summary>
      <div class="dv-acc-b">${body}</div></details>` : "";

  // Журнал работы по объекту: заметка / звонок / показ — для клиента или подборки (видно и у клиента, и в подборке)
  const WK = { note: ["note", "Заметка"], call: ["phone", "Звонок"], show: ["home", "Показ"], meet: ["users", "Встреча"] };
  function journalHTML(o) {
    const work = o.work || [], ctx = o.work_ctx || { clients: [], picks: [] };
    const when = (ts) => new Date(ts * 1000).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
    const opts = [`<option value="">Без привязки</option>`]
      .concat(ctx.clients.map((c) => `<option value="c:${c.id}">Клиент · ${esc(c.name)}${c.has ? " ✓" : ""}</option>`))
      .concat(ctx.picks.map((p) => `<option value="p:${p.id}">Подборка · ${esc(p.title)}${p.has ? " ✓" : ""}</option>`)).join("");
    return `<button type="button" class="wk-open" data-wk-open="${o.id}">${ic("plus")}Новая запись <small>звонок, показ, заметка — для клиента или подборки</small></button>
      <form class="wk-add" data-wk-add="${o.id}" autocomplete="off" hidden>
        <div class="wk-kinds" role="radiogroup" aria-label="Что было">${Object.entries(WK).map(([k, [icon, t]], i) =>
          `<button type="button" role="radio" aria-checked="${i ? "false" : "true"}" class="${i ? "" : "on"}" data-wk-kind="${k}">${ic(icon)}${t}</button>`).join("")}</div>
        <textarea name="text" rows="2" maxlength="2000" placeholder="Что было: созвонились, показали, торг, ответ клиента…"></textarea>
        <div class="wk-row"><select name="to" aria-label="Для кого">${opts}</select></div>
        <div class="wk-acts"><button type="button" class="chip" data-wk-cancel>Отмена</button><button type="submit" class="pill">Записать</button></div>
        ${ctx.clients.length || ctx.picks.length ? "" : `<p class="note">Создайте клиента в «Клиентах» или подборку — заметки можно будет привязать к ним.</p>`}
      </form>
      ${work.length ? "" : `<p class="wk-empty">Записей пока нет.</p>`}
      <ul class="wk-list">${work.map((w) => `<li class="k-${esc(w.kind)}"><span class="wk-ic">${ic((WK[w.kind] || WK.note)[0])}</span>
        <div><p class="wk-h"><b>${(WK[w.kind] || WK.note)[1]}</b><time>${esc(when(w.ts))}</time>
          ${w.client_name ? `<a class="wk-tag" href="/clients?id=${w.client_id}" target="_top">${ic("user")}${esc(w.client_name)}</a>` : ""}
          ${w.pick_title ? `<a class="wk-tag" href="/picks?open=${w.collection_id}" target="_top">${ic("folder")}${esc(w.pick_title)}</a>` : ""}</p>
          ${w.text ? `<p class="wk-t">${esc(w.text)}</p>` : ""}</div>
        <button type="button" class="wk-del" data-wk-del="${w.id}" data-id="${o.id}" aria-label="Удалить запись">${ic("x")}</button></li>`).join("")}</ul>`;
  }

  function detailHTML(o) {
    const rooms = o.rooms == null ? null : o.rooms === 0 ? "студия" : o.rooms >= 5 ? "5+" : String(o.rooms);
    const floor = o.floor ? (o.floors ? `${o.floor} из ${o.floors}` : String(o.floor)) : (o.floors ? `этажей: ${o.floors}` : null);
    const facts = [
      ["Комнат", rooms], ["Площадь", o.area ? `${num(o.area, 1)} м²` : null], ["Участок", o.land ? `${num(o.land, 1)} сот.` : null],
      ["Этаж", floor], ["Тип", (meta.types || {})[o.type] || null], ["Район", o.district], ["ЖК", o.complex],
      ["Посёлок", o.settlement && o.settlement !== o.district ? o.settlement : null], ["Адрес", o.street ? `ул. ${o.street}${o.house ? ", " + o.house : ""}` : null],
    ].filter(([, v]) => v != null && v !== "").map(([k, v]) => `<div${String(v).length > 13 ? ' class="wide"' : ""}><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("");
    const photos = o.photos || [];
    const gallery = photos.length
      ? `<div class="dv-gal-wrap"><div class="dv-gal${photos.length === 1 ? " one" : ""}" data-lb="obj">${photos.map((u, i) => `<a href="${esc(u)}" aria-label="Открыть фото ${i + 1}"><img src="${esc(u)}" alt="${esc(o.title)} — фото ${i + 1}" ${i ? 'loading="lazy"' : ""}></a>`).join("")}</div>
        ${photos.length > 1 ? `<button type="button" class="ph-nav prev" data-gal-step="-1" aria-label="Назад">${ic("chevron-left")}</button>
          <button type="button" class="ph-nav next" data-gal-step="1" aria-label="Вперёд">${ic("chevron")}</button>` : ""}</div>`
      : "";
    const m = o.market;
    const marketTag = m && m.diff <= -3 ? `<span class="dv-tag good">−${-m.diff}% к рынку</span>` : m && m.diff >= 3 ? `<span class="dv-tag high">+${m.diff}% к рынку</span>` : "";
    const days = o.first_seen ? daysOnSale(o) : null;
    const u = me();
    const admin = u && u.is_admin;
    const st = o.status || myStatus(o.id);
    const inCmp = cmp.includes(o.id);
    const actions = [
      `<button type="button" class="dv-act${inCmp ? " on" : ""}" data-cmp="${o.id}"><i>⚖</i><span>${inCmp ? "В сравнении" : "Сравнить"}</span></button>`,
      u ? `<a class="dv-act" href="/planner?add=show&listing=${o.id}"><i>📅</i><span>Запланировать</span></a>` : "",
      u ? `<button type="button" class="dv-act" data-client-open="${o.id}"><i>${ic("users")}</i><span>Клиенту ▾</span></button>` : "",
      `<button type="button" class="dv-act" data-share="${o.id}"><i>${ic("share")}</i><span>Поделиться</span></button>`,
      u ? `<button type="button" class="dv-act" data-hide-open="${o.id}"><i>${ic("eye")}</i><span>Скрыть</span></button>` : "",
    ].join("");

    // раскрывающиеся строки
    const chart = priceChartHTML(o, true);
    const priceBody = (days != null ? `<p class="dv-line">⏳ ${days < 1 ? "<b>Новое на 1+1</b> — появилось сегодня" : `На 1+1 — <b>${esc(daysText(days))}</b>`}
          <small>считаем с первого появления объявления в чатах риелторов</small></p>` : "")
      + (m ? `<p class="dv-line">${m.diff <= -3 ? `Дешевле рынка на <b>${-m.diff}%</b>` : m.diff >= 3 ? `Дороже рынка на <b>${m.diff}%</b>` : "<b>По рынку</b>"}
          <small>средняя цена м² — ${esc(num(m.median))} ₽ · ${esc(m.base)}, ${m.n} ${plural(m.n, "объект", "объекта", "объектов")}</small></p>` : "")
      + chart;
    const priceHint = [days != null ? `${days < 1 ? "новое" : days + " дн."}` : "", chart ? "график" : ""].filter(Boolean).join(" · ");
    const pairs = o.pairs || [];
    const notesBody = u && !o.loading ? `<div class="wk-sec"><p class="wk-sh">Клиенты по объекту</p>
      ${pairs.length ? `<div class="pr-list">${pairs.map((p) => `<div class="pr-it"><a href="/clients?id=${p.id}" target="_top">${ic("user")}${esc(p.name)}</a>
        ${p.reaction ? `<span class="pr-re">ответ: ${esc({ like: "нравится", no: "не подходит", want_show: "хочет посмотреть", question: "вопрос" }[p.reaction] || "")}</span>` : ""}
        <select data-pair-set="${p.id}:${o.id}" aria-label="Статус для ${esc(p.name)}">${Object.entries(PAIR_L).map(([k, v]) =>
          `<option value="${k}"${p.status === k ? " selected" : ""}>${v[0].toUpperCase() + v.slice(1)}</option>`).join("")}</select></div>`).join("")}</div>`
        : `<p class="wk-empty">Объект ещё не отправлен ни одному клиенту.</p>`}
      <button type="button" class="wk-open" data-client-open="${o.id}">${ic("users")}Клиенту ▾ <small>в подборку клиента</small></button></div>
      <div class="wk-sec"><p class="wk-sh">Журнал — что, кому и когда</p><div class="wk" data-wk="${o.id}">${journalHTML(o)}</div></div>
      <div class="wk-sec"><p class="wk-sh">Личная заметка <small>видите только вы</small></p>
      <textarea class="d-note-text wk-note" data-note="${o.id}" rows="2" maxlength="2000" placeholder="Например: торг 100 т.р., собственник один">${esc(o.note || "")}</textarea>
      <span class="note d-note-saved" hidden>Сохранено</span></div>` : "";
    const when = (ts) => new Date(ts * 1000).toLocaleString("ru-RU", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
    const plans = (o.plans || []);
    const plansBody = plans.length ? `<ul class="dv-list">${plans.map((p) => `<li><a href="/planner?event=${p.id}">${PLAN_KINDS[p.kind] || ""} · ${esc(when(p.ts))}</a>${p.done ? " ✓" : ""}
      <small>${esc(p.title)}</small></li>`).join("")}</ul>` : "";
    const fit = o.fit_clients || [];   // ваши клиенты, которым объект подходит по пожеланиям и ещё не предложен
    const fitBody = fit.length ? `<ul class="dv-list">${fit.map((c) => `<li><a href="/clients?id=${c.id}" target="_top">${esc(c.name)}</a>
        <small>${esc(c.stage_label)}</small>
        <button type="button" class="pill light" data-fit-add="${c.id}" data-fit-for="${o.id}">В подборку клиента</button></li>`).join("")}</ul>` : "";
    const same = o.same || [];
    const sameBody = same.length ? `<p class="note">Тот же или очень похожий объект у других агентов — сверьте детали.</p>
      <ul class="dv-list">${same.map((s) => `<li><button type="button" class="link" data-open-id="${s.id}">${esc(fmtPrice(s.price, o.deal))}</button>
        <small>${esc(s.title)} · ${esc(fmtAgo(s.last_seen))}</small></li>`).join("")}</ul>` : "";
    const hist = (o.history || []).map((h) => {
      const who = meta.access ? [h.chat, h.sender].filter(Boolean).join(" · ") : "";
      return `<li><span>${esc(fmtDate(h.ts))}${h.price ? " — " + esc(fmtPrice(h.price, o.deal)) : ""}</span><small>${esc(who || MATCH[h.match] || "")}</small></li>`;
    }).join("");
    const chats = (o.seen_count > 1 ? `Присылали ${o.seen_count} ${plural(o.seen_count, "раз", "раза", "раз")}` : "")
      + (o.chats > 1 ? ` в ${o.chats} ${plural(o.chats, "чат", "чата", "чатов")}` : "");
    const histBody = (hist ? `<ul class="dv-list dv-hist">${hist}</ul>` : "")
      + (meta.access && o.fragment ? `<p class="dv-sub-h">Исходное сообщение</p><pre class="d-source">${esc(o.fragment)}</pre>` : "");
    const geoNote = o.geo_status === "manual" ? "точка поставлена вручную" : o.geo_status === "learned" ? "точка из ваших прошлых правок"
      : o.geo_status === "feed" ? "точка из фида СТРЕЛ" : o.lat ? "точка найдена по адресу" : "точки нет";
    const adminPhotos = admin && photos.length ? `<p class="dv-sub-h">Фото — убрать лишнее (не вернётся из фида и чатов)</p>
      <div class="adm-ph">${photos.map((u) => `<span><img src="${esc(u)}" alt="" loading="lazy">
        <button type="button" data-photo-del="${esc(u)}" data-id="${o.id}" aria-label="Удалить фото" title="Удалить фото">${ic("trash")}</button></span>`).join("")}</div>` : "";
    const adminBody = admin ? `${adminPhotos}<div class="dv-edhist"><button type="button" class="chip" data-hist-load="${o.id}">${ic("history")}История правок: что, кем, когда</button>
        <div class="dv-ed-list" id="dvEd${o.id}"></div></div><div class="dv-admin">
        <button type="button" class="chip" data-place-edit="${o.id}">✎ ЖК, район и цена${o.admin_fixed ? " ✓" : ""}</button>
        <button type="button" class="chip" data-geo-edit="${o.id}">📍 Точка на карте</button>
        <button type="button" class="chip" data-admin-act="deal" data-deal="${o.deal === "rent" ? "sale" : "rent"}" data-id="${o.id}">⇄ ${o.deal === "rent" ? "В продажу" : "В аренду"}</button>
        <button type="button" class="chip danger" data-admin-act="delete" data-id="${o.id}">🗑 Удалить навсегда</button>
        ${(o.phones || []).map((p) => `<button type="button" class="chip danger" data-ban-phone="${esc(p)}" title="Скрыть все объекты и сообщения от этого номера и не принимать новые">🚫 Всё от ${esc(p)}</button>`).join("")}
      </div><p class="note">${esc(geoNote)}${chats ? " · " + esc(chats) : ""}</p>` : "";

    return `
      ${gallery}
      <div class="dv">
        <div class="dv-head">
          ${badgesHTML(o)}
          <h2 class="dv-title" id="dTitle">${esc(headline(o) || o.title)}</h2>
          <p class="dv-subt">${esc([...new Set([headline(o) ? o.title : "", address(o)].filter(Boolean))].join(" · "))}
            ${address(o) ? `<a class="ya-link" href="https://yandex.ru/maps/35/krasnodar/?text=${encodeURIComponent(yaQuery(o))}" target="_blank" rel="noopener">на карте ↗</a>` : ""}</p>
          <div class="dv-price"><b>${esc(fmtPrice(o.price, o.deal))}</b>
            ${o.price_m2 && o.deal !== "rent" ? `<span>${esc(num(o.price_m2))} ₽/м²</span>` : ""}${marketTag}</div>
          ${o.text_price ? `<p class="dv-tp">${ic("alert")}<span>В тексте объявления (от ${esc(new Date(o.text_price.ts * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "long" }))})
            другая цена — ${esc(fmtPrice(o.text_price.price, o.deal))}. В карточке — <b>${esc(fmtPrice(o.price, o.deal))}</b> (из более свежего сообщения). Уточните у агента.</span></p>` : ""}
          ${o.price_doubt ? `<p class="dv-tp">${ic("alert")}<span>Цена записана в объявлении неоднозначно (например, «5400» без «тыс.» или «млн»). Уточните у агента —
            в сортировках по цене и фильтрах этот объект не участвует.</span></p>` : ""}
        </div>
        <div class="dv-acts">${actions}</div>
        ${contactHTML(o)}
        ${facts ? `<dl class="dv-facts">${facts}</dl>` : ""}
        ${o.lat && o.lon && !o.loading && ["street", "district"].includes(o.geo_level)
          ? `<p class="dv-vague">Адрес уточняется — в объявлении ${o.geo_level === "street" ? "указана только улица" : "указан только район"}, поэтому на карте объекта нет.</p>` : ""}
        ${o.lat && o.lon && !o.loading && !["street", "district"].includes(o.geo_level) ? `<div class="dv-map-wrap"><div class="dv-map" id="dvMap" aria-label="Объект на карте"></div>
          ${o.geo_approx ? `<p class="note dv-map-note">Место примерное — в объявлении нет точного адреса</p>` : ""}</div>` : ""}
        ${o.description ? `<div class="dv-desc clamp"><p>${esc(o.description)}</p><button type="button" class="dv-desc-more" data-desc-more>Читать полностью</button></div>` : ""}
        <div class="dv-accs">
          ${acc("📈", "Цена и рынок", priceHint, priceBody)}
          ${acc("📝", "Работа с объектом", [(o.pairs || []).length ? `клиентов: ${o.pairs.length}` : "", (o.work || []).length ? `записей: ${o.work.length}` : o.note ? "есть заметка" : ""].filter(Boolean).join(" · "), notesBody)}
          ${acc("📅", "В планере", plans.length ? `${plans.length}` : "", plansBody)}
          ${fit.length ? acc("🙋", "Подходит вашим клиентам", `${fit.length}`, fitBody, { cls: "good" }) : ""}
          ${acc("👥", "Похожие у других агентов", `${same.length}`, sameBody)}
          ${acc("🕓", "История", hist ? `${o.history.length}` : "", histBody)}
          ${o.can_edit ? `<a class="dv-acc-btn" href="/my/edit?id=${o.id}" target="_top"><span class="dv-acc-ic">✎</span>Изменить моё объявление</a>` : ""}
          ${acc("⚙", "Админ", "", adminBody, { cls: "admin" })}
          ${reportHTML(o)}
        </div>
        <p class="dv-foot">Появилось на 1+1: ${esc(fmtAgo(o.first_seen))} · последний раз присылали: ${esc(fmtAgo(o.last_seen))}</p>
        ${brandLine("поиск объектов Краснодара из риелторских чатов")}
      </div>`;
  }



  // Мини-карта в окне объекта: где он (примерное место — кругом)
  let detailMap = null;
  function drawDetailMap(o) {
    if (detailMap) { detailMap.remove(); detailMap = null; }
    const el = document.getElementById("dvMap");
    if (!el || !window.L || !o.lat || !o.lon || ["street", "district"].includes(o.geo_level)) return;
    detailMap = L.map(el, { zoomControl: false, scrollWheelZoom: false, dragging: !L.Browser.mobile, attributionControl: false })
      .setView([o.lat, o.lon], o.geo_approx ? 14 : 16);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19 }).addTo(detailMap);
    L.control.attribution({ prefix: false }).addAttribution("© OpenStreetMap").addTo(detailMap);   // без флажка и «Leaflet»; подпись OSM обязательна по их правилам
    if (o.geo_approx) {
      L.circle([o.lat, o.lon], { radius: 350, color: "#e3cb8f", weight: 2, dashArray: "6 6", fillColor: "#e3cb8f", fillOpacity: .12 }).addTo(detailMap);
    } else {
      L.marker([o.lat, o.lon], { icon: L.divIcon({ className: "dv-pin-wrap", html: '<span class="dv-pin"></span>', iconSize: [28, 28], iconAnchor: [14, 28] }) }).addTo(detailMap);
    }
    L.control.zoom({ position: "bottomright" }).addTo(detailMap);
    setTimeout(() => detailMap && detailMap.invalidateSize(), 250);   // окно раскрывается с анимацией
  }

  // «Скрыть для себя»: объект пропадает только у этого агента (продан, не сотрудничает…), вернуть — «В работе» → «Скрытые»
  function openHide(id) {
    let d = document.getElementById("hideDlg");
    if (!d) {
      d = document.createElement("dialog");
      d.id = "hideDlg";
      d.className = "small-dlg hide-dlg";
      document.body.appendChild(d);
    }
    d.innerHTML = `<div class="dlg-body"><h2>Скрыть объект для себя?</h2>
      <p class="note">Он пропадёт только у вас — в поиске и на карте. Остальные агенты его видят. Вернуть можно в «В работе» → «Скрытые».</p>
      <div class="hide-opts">
        <button type="button" class="hide-opt" data-hide-reason="sold" data-id="${id}">${ic("check-circle")}<span><b>Продан или снят</b><small>позвонил — объекта уже нет</small></span></button>
        <button type="button" class="hide-opt" data-hide-reason="no_coop" data-id="${id}">${ic("ban")}<span><b>Не сотрудничаю</b><small>с этим агентом или объектом не работаю</small></span></button>
        <button type="button" class="hide-opt" data-hide-reason="not_fit" data-id="${id}">${ic("x-circle")}<span><b>Не подходит</b><small>не мой формат — не показывать</small></span></button>
      </div>
      <div class="dlg-actions"><button type="button" class="pill light" data-close>Отмена</button></div></div>`;
    d.showModal();
  }
  async function hideListing(id, reason) {
    const r = await call("/api/hidden", { id, hide: true, reason });
    if (!r.ok) { toast("Не получилось — войдите и попробуйте ещё раз"); return; }
    const d = document.getElementById("hideDlg");
    if (d && d.open) d.close();
    if ($("detail").open) closeDlg($("detail"));
    document.querySelectorAll(`.item[data-id="${id}"]`).forEach((el) => { el.style.transition = "opacity .3s, transform .3s"; el.style.opacity = "0"; el.style.transform = "scale(.96)"; setTimeout(() => el.remove(), 300); });
    if (map) loadMap();
    toast("Скрыто для вас. Вернуть — «В работе» → «Скрытые»", 3500);
  }

  // График цены: ступеньки по датам смены цены, до сегодня
  function priceChartHTML(o) {   // внутренность строки «Цена и рынок»
    // смены цены в пределах 10 минут — это одно сообщение, разобранное повторно: берём последнюю
    const h = [];
    for (const x of (o.price_history || []).filter((p) => p.price > 0)) {
      if (h.length && x.ts - h[h.length - 1].ts < 600) h[h.length - 1] = { ts: h[h.length - 1].ts, price: x.price };
      else if (!h.length || h[h.length - 1].price !== x.price) h.push(x);
    }
    if (h.length < 2) return "";
    const end = Math.max(loadedAt || Date.now() / 1000, h[h.length - 1].ts + 1);
    const pts = [...h, { ts: end, price: h[h.length - 1].price }];
    const t0 = pts[0].ts, t1 = pts[pts.length - 1].ts;
    const ps = pts.map((x) => x.price), lo = Math.min(...ps), hi = Math.max(...ps);
    const W = 320, H = 110, P = 8;
    const x = (t) => P + ((t - t0) / Math.max(1, t1 - t0)) * (W - 2 * P);
    const y = (p) => (hi === lo ? H / 2 : P + (1 - (p - lo) / (hi - lo)) * (H - 2 * P));
    let d = `M${x(pts[0].ts).toFixed(1)},${y(pts[0].price).toFixed(1)}`;
    for (let i = 1; i < pts.length; i++) d += ` H${x(pts[i].ts).toFixed(1)} V${y(pts[i].price).toFixed(1)}`;
    const dots = h.map((p) => `<circle cx="${x(p.ts).toFixed(1)}" cy="${y(p.price).toFixed(1)}" r="3.5"><title>${esc(fmtDate(p.ts))}: ${esc(fmtPrice(p.price, o.deal))}</title></circle>`).join("");
    const first = h[0].price, last = h[h.length - 1].price;
    const diff = last - first;
    const word = diff < 0 ? `снизилась на ${esc(fmtShortPrice(-diff, o.deal))}` : diff > 0 ? `выросла на ${esc(fmtShortPrice(diff, o.deal))}` : "вернулась к первой";
    const rows = h.slice().reverse().map((p) => `<li><span>${esc(fmtDate(p.ts))}</span><b>${esc(fmtPrice(p.price, o.deal))}</b></li>`).join("");
    return `<p class="dv-line">📈 Цена ${word} · ${h.length} ${plural(h.length, "изменение", "изменения", "изменений")}</p>
      <svg viewBox="0 0 ${W} ${H}" class="d-chart-svg" role="img" aria-label="График цены"><path d="${d}"/>${dots}</svg>
      <ul class="d-chart-list">${rows}</ul>`;
  }



  // Планер: что уже запланировано по объекту + «📅 Запланировать» (объект попадёт в избранное)
  const PLAN_KINDS = { show: "🏠 Показ", call: "📞 Созвон", meet: "🤝 Встреча", task: "✅ Задача" };


  // ─── админ: удалить навсегда / перенести продажа ↔ аренда ───
  async function adminAction(id, act, deal) {
    if (act === "delete" && !confirm("Удалить объект навсегда?\nПовторы этого объявления и тот же объект больше не появятся.\nВернуть можно в админке → «Удалённые».")) return;
    const r = await call(`/api/admin/listings/${id}/action`, act === "delete" ? { action: "delete" } : { action: "deal", deal });
    if (!r.ok) { toast(r.data.detail || "Не получилось"); return; }
    if (act === "delete") {
      closeDlg($("detail"));
      document.querySelectorAll(`.item[data-id="${id}"]`).forEach((el) => { el.classList.add("leaving"); setTimeout(() => el.remove(), 350); });
      toast("🗑 Удалено навсегда. Вернуть — админка → «Удалённые»", 3500);
    } else {
      toast(deal === "rent" ? "⇄ Перенесено в аренду" : "⇄ Перенесено в продажу", 2400);
      openDetail(id);
      refresh();
    }
  }

  async function banPhone(phone) {
    if (!confirm(`Удалить всё от номера ${phone}?\nВсе его объекты и запросы скроются, сообщения удалятся, новые не будут приниматься.\nВернуть — админка → «Удалённые».`)) return;
    const r = await call("/api/admin/phones/ban", { phone });
    if (!r.ok) { toast(r.data.detail || "Не получилось"); return; }
    closeDlg($("detail"));
    toast(`🚫 Номер удалён: объектов ${r.data.listings}, сообщений ${r.data.messages}`, 3500);
    refresh();
  }

  // ─── подборки для клиента ───
  let pickFor = 0;
  async function openPicks(id) {
    pickFor = id;
    const r = await call(`/api/picks?listing=${id}`);
    if (r.status === 403) { toast(r.data.detail || "Подборки — по подписке", 3500); return; }
    if (!r.ok) { toast("Не получилось загрузить подборки"); return; }
    const inSet = new Set(r.data.with);
    const o = known.get(id) || {};
    $("pickFor").innerHTML = o.title ? `${o.photos && o.photos[0] ? `<img src="${esc(o.photos[0])}" alt="">` : ""}<span><b>${esc(headline(o) || o.title)}</b>
      <small>${esc(fmtPrice(o.price, o.deal))}</small></span>` : "";
    $("pickList").innerHTML = r.data.items.map((p) => `<li><button type="button" class="pk-tog${inSet.has(p.id) ? " on" : ""}" data-pick-tog="${p.id}">
      <span class="pk-tog-ic">${ic("folder")}</span><span class="pk-tog-t"><b>${esc(p.title)}</b>
      <small>${p.n} ${plural(p.n, "объект", "объекта", "объектов")}${p.views ? ` · клиент открывал ${p.views}` : ""}</small></span>
      <span class="pk-tog-mark">${ic("check")}</span></button></li>`).join("")
      || `<li class="pk-empty">Подборок пока нет — создайте первую ниже.</li>`;
    $("pickNewTitle").value = "";
    openDlg($("pickDlg"));
  }


  // Поделиться: на телефоне — системное меню (WhatsApp, Telegram…), на компьютере — копируем ссылку
  async function share(url, title) {
    let copied = false;
    try { await navigator.clipboard.writeText(url); copied = true; } catch { /* нет доступа к буферу */ }
    if (!copied) {   // старые браузеры: копируем через скрытое поле
      const ta = document.createElement("textarea");
      ta.value = url; ta.setAttribute("readonly", ""); ta.style.cssText = "position:fixed;opacity:0";
      document.body.appendChild(ta); ta.select();
      try { copied = document.execCommand("copy"); } catch { /* нет */ }
      ta.remove();
    }
    if (copied) toast("✓ Ссылка скопирована — вставьте её в чат", 3000);
    else prompt("Скопируйте ссылку на объект:", url);   // браузер не дал доступ к буферу обмена
    // На телефоне сразу предлагаем отправить (WhatsApp, Telegram…)
    if (navigator.share && window.matchMedia("(pointer: coarse)").matches) {
      try { await navigator.share({ url, title }); } catch { /* закрыли меню */ }
    }
  }

  // ─── админ: поправить точку объекта на карте ───────────────────────────
  let geoMap = null, geoMarker = null, geoId = 0;
  let geoQueue = "";        // разбираем очередь «Нет на карте» из админки
  const geoSkipped = new Set();
  // Что искать в Яндекс Картах: дом → ЖК → улица → район
  function yaQuery(o) {
    const place = o.settlement || "Краснодар";
    if (o.street) return `${place}, ${o.street}${o.house ? " " + o.house : ""}`;
    if (o.complex) return `ЖК ${o.complex}, Краснодар`;
    return `${o.district || ""} ${place}`.trim();
  }
  function openGeoEdit(id) {
    const o = known.get(id) || {};
    geoId = id;
    const dlg = $("geoDlg");
    $("geoTitle").textContent = o.title || "Объект";
    $("geoAddr").textContent = address(o) || "адрес не указан";
    $("geoYa").href = `https://yandex.ru/maps/35/krasnodar/?text=${encodeURIComponent(yaQuery(o))}`;
    openDlg(dlg);
    if (!window.L) { toast("Карта не загрузилась"); return; }
    const start = o.lat ? [o.lat, o.lon] : [45.035, 38.975];
    if (!geoMap) {
      geoMap = L.map("geoMap", { zoomControl: true, attributionControl: false });
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19 }).addTo(geoMap);
      const icon = L.divIcon({ className: "geo-pin-wrap", iconSize: [36, 46], iconAnchor: [18, 44],
        html: `<div class="geo-pin">${LOGO}</div>` });
      geoMarker = L.marker(start, { draggable: true, icon }).addTo(geoMap);
      geoMap.on("click", (e) => { geoMarker.setLatLng(e.latlng); showGeoNow(); });   // нажали на карту — точка переезжает туда
      geoMarker.on("dragend", showGeoNow);
    }
    geoMarker.setLatLng(start);
    $("geoCoords").value = "";
    $("geoSkip").hidden = !geoQueue;
    $("geoLeft").textContent = "";
    showGeoNow();
    // Окно открывается с анимацией — подстраиваем карту под его размер, когда оно раскрылось
    const fit = () => { geoMap.invalidateSize(); geoMap.setView(start, o.lat ? 16 : 12); };
    fit();
    setTimeout(fit, 120);
    setTimeout(fit, 500);
  }
  // Координаты из любого вида: «45.0355, 38.9753», «45,0355 38,9753», ссылка Яндекс Карт (ll=долгота,широта)
  // или Google Карт (@широта,долгота). Перепутанный порядок (долгота первой) исправляем сами.
  function parseCoords(text) {
    let t = String(text || "").trim();
    try { t = decodeURIComponent(t); } catch { /* не ссылка */ }
    let a, b;
    const ya = t.match(/[?&](?:pt|whatshere%5Bpoint%5D|whatshere\[point\])=(-?\d+\.\d+),(-?\d+\.\d+)/)   // метка
      || t.match(/[?&]ll=(-?\d+\.\d+),(-?\d+\.\d+)/);                                                      // центр карты
    const gm = t.match(/@(-?\d+\.\d+),(-?\d+\.\d+)/);
    if (ya) { a = +ya[2]; b = +ya[1]; }            // Яндекс: сначала долгота
    else if (gm) { a = +gm[1]; b = +gm[2]; }       // Google: сначала широта
    else {
      const nums = t.replace(/(\d),(\d)/g, "$1.$2").match(/-?\d+(?:\.\d+)?/g) || [];
      if (nums.length < 2) return null;
      a = +nums[0]; b = +nums[1];
    }
    if (a > 36 && a < 42 && b > 42 && b < 48) [a, b] = [b, a];
    if (!(a > 40 && a < 50 && b > 35 && b < 45)) return null;
    return [a, b];
  }
  function showGeoNow() {
    const ll = geoMarker.getLatLng();
    $("geoNow").textContent = `Сейчас: ${ll.lat.toFixed(6)}, ${ll.lng.toFixed(6)}`;
  }

  async function saveGeo(body) {
    const r = await call(`/api/admin/listings/${geoId}/geo`, body);
    if (!r.ok) { toast(r.data.detail || "Не получилось сохранить"); return; }
    const o = known.get(geoId);
    if (o) Object.assign(o, { lat: r.data.lat, lon: r.data.lon, geo_status: r.data.geo_status });
    closeDlg($("geoDlg"));
    const learned = r.data.learned || [];
    toast(body.auto ? "Точку найдём по адресу заново" : body.hide ? "Объект убран с карты"
      : `✓ Точка сохранена${learned.length ? ` и запомнена для: ${learned.join(", ")}` : ""}`
        + (r.data.applied ? `. Поправлено ещё объектов: ${r.data.applied}` : ""), 5000);
    if (geoQueue) { nextInQueue(); return; }
    if ($("detail").open && openedId === geoId) openDetail(geoId);
    if (state.view === "map") loadMap();
  }
  // Следующий объект из очереди админки
  async function nextInQueue() {
    const r = await call(`/api/admin/geo-queue?kind=${encodeURIComponent(geoQueue)}`);
    geoSkipped.add(geoId);
    const items = r.ok ? r.data.items || [] : [];
    const next = items.find((o) => !geoSkipped.has(o.id)) || items.find((o) => o.id !== geoId);
    if (!next) { toast("Очередь разобрана 🎉"); geoQueue = ""; return; }
    const left = r.data.counts[geoQueue];
    await openDetail(next.id);
    setTimeout(() => { openGeoEdit(next.id); $("geoLeft").textContent = `Осталось в очереди: ${left}`; }, 400);
  }

  // ─── админ: поправить ЖК и район и научить сервис ──────────────────────
  let placeId = 0, placeQueue = "";
  const placeSkipped = new Set();   // разобранные и пропущенные — в конец очереди
  function openPlaceEdit(id) {
    const o = known.get(id) || {};
    placeId = id;
    const f = $("placeForm");
    f.complex.value = o.complex || "";
    $("placeDistrict").value = o.district || "";
    // цена: продажа — в млн, аренда — в тыс. ₽/мес; поменяли — сервис запомнит для этого объявления
    const rent = o.deal === "rent";
    $("placePriceLabel").textContent = rent ? "Цена, тыс. ₽/мес (если разобралась неверно)" : "Цена, млн ₽ (если разобралась неверно)";
    f.price.value = o.price ? String(+(o.price / (rent ? 1000 : 1e6)).toFixed(rent ? 0 : 3)).replace(".", ",") : "";
    f.price.dataset.orig = f.price.value;
    $("placeInfo").textContent = [o.title, address(o)].filter(Boolean).join(" · ");
    $("placeYa").href = `https://yandex.ru/maps/35/krasnodar/?text=${encodeURIComponent(yaQuery(o))}`;
    $("placeSkip").hidden = !placeQueue;
    $("placeLeft").textContent = "";
    renderPlaceLearn();
    openDlg($("placeDlg"));
  }
  // Чему научить сервис — галочки зависят от того, что есть в объявлении и что поменяли
  function renderPlaceLearn() {
    const o = known.get(placeId) || {};
    const f = $("placeForm");
    const cx = f.complex.value.trim(), d = f.district.value;
    const box = [];
    if (o.street && d) box.push(["street", `Улица <b>${esc(o.street)}</b> → район <b>${esc(d)}</b> (для её объявлений без своего района)`]);
    if (o.street && o.house) box.push(["addr", `Запомнить для дома <b>ул. ${esc(o.street)}, ${esc(o.house)}</b>: ${cx ? "ЖК " + esc(cx) : "без ЖК"}${d ? ", " + esc(d) : ""}`]);
    if (o.complex && o.complex !== cx) box.push(["alias", cx
      ? `Везде, где агенты пишут <b>«${esc(o.complex)}»</b>, — это ЖК <b>${esc(cx)}</b>`
      : `<b>«${esc(o.complex)}»</b> — это не ЖК (больше не считать ЖК)`]);
    if (cx && d) box.push(["cx_district", `ЖК <b>${esc(cx)}</b> всегда в районе <b>${esc(d)}</b>`]);
    $("placeLearn").innerHTML = box.length
      ? `<p class="note">Научить сервис (подействует на все такие объявления — уже собранные и новые):</p>` +
        box.map(([k, t]) => `<label class="check"><input type="checkbox" name="learn_${k}" checked> <span>${t}</span></label>`).join("")
      : `<p class="note">Поправим только этот объект.</p>`;
  }
  async function savePlace() {
    const f = $("placeForm");
    const learn = {};
    ["addr", "alias", "cx_district", "street"].forEach((k) => { if (f[`learn_${k}`] && f[`learn_${k}`].checked) learn[k] = true; });
    const body = { complex: f.complex.value, district: f.district.value, learn };
    if (f.price.value.trim() && f.price.value !== f.price.dataset.orig) body.price = f.price.value;
    const r = await call(`/api/admin/listings/${placeId}/place`, body);
    if (!r.ok) { toast(r.data.detail || "Не получилось сохранить"); return; }
    const o = known.get(placeId);
    if (o) Object.assign(o, { complex: r.data.complex, district: r.data.district, admin_fixed: 1, price: r.data.price });
    closeDlg($("placeDlg"));
    toast(`✓ Сохранено${r.data.learned.length ? `. Выучено: ${r.data.learned.join("; ")}` : ""}`
      + (r.data.applied ? `. Поправлено ещё объектов: ${r.data.applied}` : ""), 6000);
    if (placeQueue) { nextPlace(); return; }
    if ($("detail").open && openedId === placeId) openDetail(placeId);
    reloadList();
  }
  async function nextPlace() {
    const r = await call(`/api/admin/place-queue?kind=${encodeURIComponent(placeQueue)}`);
    placeSkipped.add(placeId);
    const items = r.ok ? r.data.items || [] : [];
    const next = items.find((o) => !placeSkipped.has(o.id)) || items.find((o) => o.id !== placeId);
    if (!next) { toast("Очередь разобрана 🎉"); placeQueue = ""; return; }
    await openDetail(next.id);
    setTimeout(() => { openPlaceEdit(next.id); $("placeLeft").textContent = `Осталось в очереди: ${r.data.counts[placeQueue]}`; }, 400);
  }

  // ─── сохранённые поиски у фильтров ──────────────────────────────────────
  // Совпадает ли текущий адрес с открытым сохранённым поиском (если человек поменял фильтры — уже нет)
  function applyingSaved() {
    const s = savedList.find((x) => x.id === activeSaved);
    if (!s) return false;
    const norm = (q) => { const p = new URLSearchParams(q); ["saved", "since", "view", "open", "sort"].forEach((k) => p.delete(k));
      return [...p.entries()].map((e) => e.join("=")).sort().join("&"); };
    writeUrl();
    return norm(location.search.slice(1)) === norm(s.url.split("?")[1] || "");
  }
  async function loadSavedSearches() {
    if (!me()) { savedList = []; renderSaved(); return; }
    const r = await call("/api/saved");
    savedList = r.ok ? r.data.items || [] : [];
    renderSaved();
  }
  let savedHidden = false;
  try { savedHidden = localStorage.getItem("savedHidden") === "1"; } catch { /* */ }
  function renderSaved() {
    $("savedRow").hidden = !savedList.length;
    $("savedRow").classList.toggle("collapsed", savedHidden);
    $("savedToggle").textContent = savedHidden ? `Показать · ${savedList.length}` : "Скрыть";
    $("savedChips").innerHTML = savedList.map((s) =>
      `<span class="saved-chip${s.id === activeSaved ? " on" : ""}">
        <button type="button" class="saved-open" data-saved="${s.id}" title="${esc(s.title)}${s.new_24h ? ` — новых за сутки: ${s.new_24h}` : ""}">${esc(s.title)}${s.new_24h ? ` <b class="saved-new">+${s.new_24h}</b>` : ""}</button>
        <button type="button" class="saved-x" data-unsave-chip="${s.id}" aria-label="Удалить поиск «${esc(s.title)}»" title="Удалить поиск">×</button>
      </span>`).join("");
  }
  // Применить фильтры сохранённого поиска (из адреса вида «/?rooms=2&district=ФМР»)
  function applySaved(id, since) {
    const s = savedList.find((x) => x.id === id);
    if (!s) return;
    const sp = new URLSearchParams(s.url.split("?")[1] || "");
    sp.delete("saved"); sp.delete("since");
    Object.assign(state, JSON.parse(JSON.stringify(DEFAULTS)), { view: state.view });
    history.replaceState(null, "", sp.toString() ? `?${sp}` : location.pathname);
    readUrl();
    $("q").value = state.q;
    activeSaved = id;
    setHits(since || 0, s.title);
    if (favMode) setFavMode(false);
    syncControls();
    renderSaved();
    refresh();
  }
  function setHits(since, title) {
    hitSince = since;
    $("hitNote").hidden = !since;
    if (since) $("hitNote").innerHTML = `Новые объекты по поиску «${esc(title || "")}» выделены
      <button type="button" class="chip" id="hitOff">Понятно</button>`;
  }

  async function watchSearch() {
    if (!me()) { openLogin("Чтобы получать уведомления о новых объектах по этому поиску, войдите."); return; }
    const r = await call("/api/saved", { query: apiParams().toString(), page: location.search });
    toast(r.data.text || r.data.detail || "Не получилось.", 3500);
    if (r.ok) { reloadMeta(); loadSavedSearches(); activeSaved = r.data.saved.id; }
  }

  // Телефон агента: открыт по подписке; иначе — скрыт с понятным следующим шагом
  function contactHTML(o) {
    if (o.loading) return `<div class="d-locked loading"><div class="skel"><i></i></div><p>Загружаем контакты…</p></div>`;
    if (o.phones && o.phones.length) {
      const pretty = (p) => { const d = p.replace(/\D/g, ""); return d.length === 11 ? `+7 ${d.slice(1, 4)} ${d.slice(4, 7)}-${d.slice(7, 9)}-${d.slice(9)}` : p; };
      return `<div class="dv-contacts"><span class="dv-contacts-h">${o.phones.length > 1 ? "Контакты агентов" : "Контакт агента"}</span>${o.phones.map((p) => {
        const digits = p.replace(/\D/g, "");
        return `<div class="dv-phone"><a class="dv-phone-num" href="tel:${esc(p)}">${esc(pretty(p))}</a>
          <a class="dv-ic-btn" href="tel:${esc(p)}" aria-label="Позвонить" title="Позвонить">${ic("phone")}</a>
          <button type="button" class="dv-ic-btn dv-max" data-max="${esc(p)}" aria-label="Написать в MAX" title="Написать в MAX">MAX</button></div>`;
      }).join("")}${o.sender_phone ? `<p class="dv-sender">Прислал в чат: <a href="tel:${esc(o.sender_phone)}">${esc(pretty(o.sender_phone))}</a></p>` : ""}${o.url ? `<a class="dv-site${o.source === "feed" ? " strely" : ""}" href="${esc(o.url)}" target="_blank" rel="noopener">${o.source === "feed"
        ? `${STRELY}<span>Объект партнёра «СТРЕЛЫ» — смотреть на arrowsrealty.ru</span>${ic("external")}` : `${ic("link")}Объявление на сайте агентства`}</a>` : ""}</div>`;
    }
    if (o.phones_limit) {
      return `<div class="d-locked"><p>На сегодня открыто максимум номеров (${esc(me() && me().views_limit)}). Завтра лимит обновится.</p></div>`;
    }
    if (!o.phones_masked || !o.phones_masked.length) return "";
    const u = me();
    let text, btn;
    if (!u) {
      text = `Телефон агента открывается после входа. <b>Новым — ${meta.trial_days || 7} дней бесплатно.</b>`;
      btn = `<button type="button" class="pill" data-login>Войти и открыть номер</button>`;
    } else if (!u.phone_confirmed && !u.trial_used && meta.tg_login) {
      text = `Подтвердите номер в Telegram — и <b>${meta.trial_days || 7} дней бесплатно</b>.`;
      btn = `<button type="button" class="pill" data-tglink>Подтвердить номер</button>`;
    } else {
      text = `Доступ к номерам закончился. Подписка: <b>${meta.prices || `${meta.price} ₽ за ${meta.period_days} дней`}</b>.`;
      btn = `<button type="button" class="pill" data-pay>Оформить подписку</button>`;
    }
    return `<div class="d-locked">
      <div class="d-locked-num">${esc(o.phones_masked[0])}</div>
      <p>${text}</p>${btn}</div>`;
  }

  function reportHTML(o) {
    return `<details class="dv-acc d-report"><summary><span class="dv-acc-ic">⚠</span><span class="dv-acc-t">Сообщить об ошибке</span></summary>
      <form class="report-form" data-report="${o.id}">
        <select name="reason">
          <option value="sold">Объект продан или неактуален</option>
          <option value="wrong">Неверные данные (цена, этаж, адрес)</option>
          <option value="my_phone">Это мой номер — уберите его</option>
          ${(o.photos || []).length ? `<option value="photo">Фото не от этого объекта (чужие, с номером, реклама)</option>` : ""}
          <option value="other">Другое</option>
        </select>
        ${(o.photos || []).length ? `<div class="rp-photos" data-photo-pick hidden><p class="note">Отметьте фото, которые не подходят:</p>
          <div class="rp-ph">${o.photos.map((u) => `<label><input type="checkbox" name="ph" value="${esc(u)}"><img src="${esc(u)}" alt="" loading="lazy"><i>${ic("check")}</i></label>`).join("")}</div></div>` : ""}
        <textarea name="text" rows="2" placeholder="Что не так? (необязательно)"></textarea>
        <p class="note" data-optout-hint hidden>Отправьте заявку — мы проверим и уберём номер. Если хотите сами управлять своими
          объявлениями (цена, фото, «продано»), подтвердите номер в разделе <a href="/my" target="_top">«Мои объекты»</a> — код через MAX.</p>
        <button type="submit" class="pill light">Отправить</button>
      </form></details>`;
  }

  function openDlg(d) {
    if (!d.open) d.showModal();
    d.classList.remove("closing");
    d.style.transform = "";
  }

  function closeDlg(d) {
    if (!d || !d.open || d.classList.contains("closing")) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) { d.close(); return; }
    d.classList.add("closing");
    let finished = false;
    const done = () => {
      if (finished) return;
      finished = true;
      d.removeEventListener("animationend", done);
      d.classList.remove("closing");
      d.style.transform = "";
      d.close();
    };
    d.addEventListener("animationend", done);
    setTimeout(done, 350);  // страховка: анимация могла не проиграться (свёрнутая вкладка, экономия энергии)
  }

  // Свайп вниз закрывает окно (телефон): тянем за верх, отпустили ниже 110px — закрыли
  function swipeToClose(d) {
    let y0 = null, dy = 0;
    d.addEventListener("touchstart", (e) => {
      if (window.innerWidth > 640 || d.scrollTop > 0) { y0 = null; return; }
      y0 = e.touches[0].clientY; dy = 0;
    }, { passive: true });
    d.addEventListener("touchmove", (e) => {
      if (y0 === null) return;
      dy = Math.max(0, e.touches[0].clientY - y0);
      if (dy > 4) { d.classList.add("dragging"); d.style.transform = `translateY(${dy}px)`; }
    }, { passive: true });
    d.addEventListener("touchend", () => {
      if (y0 === null) return;
      d.classList.remove("dragging");
      if (dy > 110) closeDlg(d);
      else { d.style.transition = "transform .3s cubic-bezier(.2,.9,.25,1)"; d.style.transform = "";
        setTimeout(() => { d.style.transition = ""; }, 320); }
      y0 = null;
    });
    d.addEventListener("cancel", (e) => { e.preventDefault(); closeDlg(d); });   // Esc — тоже плавно
    d.addEventListener("click", (e) => { if (e.target === d) closeDlg(d); });     // нажатие мимо окна
  }

  // Всплывающая подсказка внизу
  let toastTimer = 0;
  // Уведомление всегда поверх всего, в том числе поверх открытого окна объекта
  function toast(text, ms = 2400) {
    const t = $("toast");
    t.textContent = text;
    if (t.showPopover) {
      try { t.hidePopover(); } catch { /* ещё не показан */ }
      try { t.showPopover(); } catch { /* старый браузер */ }
    } else {
      const dlg = document.querySelector("dialog[open]");   // запасной вариант: внутрь открытого окна
      (dlg || document.body).appendChild(t);
    }
    requestAnimationFrame(() => t.classList.add("show"));
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      t.classList.remove("show");
      setTimeout(() => { if (!t.classList.contains("show") && t.hidePopover) { try { t.hidePopover(); } catch { /* */ } } }, 450);
    }, ms);
  }

  // ─── новые объекты, пока человек на сайте ───────────────────────────────
  function hideNewPill() { $("newPill").classList.remove("show"); }

  let lastNewCheck = 0;
  async function checkNew() {
    if (document.hidden || favMode || !loadedAt || Date.now() - lastNewCheck < 20000) return;
    lastNewCheck = Date.now();
    const r = await call(`/api/listings?${apiParams({ size: 1, since: loadedAt })}`);
    if (r.ok && r.data.total > 0) {
      $("newCount").textContent = num(r.data.total);
      $("newPill").hidden = false;
      $("newPill").classList.add("show");
    }
  }

  function reloadList() {
    hideNewPill();
    const b = $("refreshBtn");
    b.classList.remove("spin"); void b.offsetWidth; b.classList.add("spin");
    window.scrollTo({ top: 0, behavior: "smooth" });
    loadList();
    loadFacets();
    if (state.view === "map") loadMap();
  }

  // ─── шторка фильтров (телефон) ──────────────────────────────────────────
  function openSheet() { document.body.classList.add("sheet-open"); }
  function closeSheet() { document.body.classList.remove("sheet-open"); }
  function filtersCount() {
    return state.types.length + state.rooms.length + state.districts.length + state.complexes.length
      + ["priceMin", "priceMax", "areaMin", "areaMax", "landMin", "landMax", "fresh"].filter((k) => state[k]).length
      + (state.notFirst ? 1 : 0) + (state.notLast ? 1 : 0) + (state.hot ? 1 : 0) + (state.deal === "rent" ? 1 : 0);
  }

  // ─── нижняя панель (телефон) ────────────────────────────────────────────
  function setTab(tab) {
    document.querySelectorAll(".tabbar [data-tab], .nav-links [data-tab]").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
    const vc = $("viewChip");   // кнопка «Карта / Список» в строке над выдачей (на телефоне — единственный переключатель)
    if (vc) { vc.dataset.tab = tab === "map" ? "list" : "map"; vc.textContent = tab === "map" ? "Список" : "Карта"; }
  }

  const known = new Map();  // объекты, которые уже пришли списком/картой — для мгновенного открытия
  let openedId = 0;
  // Переходы между объектами внутри окна («продают ещё N агентов») — со стрелкой «Назад»
  let detailStack = [];
  function setOpenParam(id) {
    const sp = new URLSearchParams(location.search);
    if (id) sp.set("open", id); else sp.delete("open");
    const s = sp.toString();
    history.replaceState(null, "", s ? `?${s}` : location.pathname);
  }

  async function openDetail(id, opts = {}) {
    const dlg = $("detail");
    if (opts.push && openedId && dlg.open && openedId !== id) detailStack.push(openedId);
    if (!dlg.open) detailStack = [];
    openedId = id;
    $("detailBack").hidden = !detailStack.length;
    setOpenParam(id);
    $("detailBody").innerHTML = `<div class="d-loading">${LOGO}<p>Открываем объект…</p></div>`;
    // Сразу показываем то, что уже знаем (фото, цена, описание), номер и история догрузятся
    const pre = known.get(id);
    if (pre) $("detailBody").innerHTML = detailHTML({ ...pre, phones_masked: null, history: [], loading: true });
    openDlg(dlg);
    dlg.scrollTop = 0;
    const r = await call(`/api/listings/${id}`);  // тихие повторы — внутри call()
    if (openedId !== id) return;  // пока грузилось, открыли другой объект
    if (r.ok) {
      // Подменяем содержимое без прыжка: та же прокрутка, мягкое проявление догруженного
      const top = dlg.scrollTop;
      known.set(id, { ...(known.get(id) || {}), ...r.data });
      $("detailBody").innerHTML = detailHTML(r.data);
      drawDetailMap(r.data);
      dlg.scrollTop = top;
      const fb = $("detailFav");
      if (!fb.innerHTML) fb.innerHTML = HEART;
      fb.dataset.fav = id;
      fb.classList.toggle("on", !!r.data.favorite);
      fb.setAttribute("aria-label", r.data.favorite ? "Убрать из избранного" : "В избранное");
    } else if (r.status === 404) {
      $("detailBody").innerHTML = EMBED
        ? `<p class="d-place">Этот объект уже сняли с продажи или удалили.</p>
          <button type="button" class="pill" data-close-dlg>Закрыть</button>`
        : `<p class="d-place">Этот объект уже сняли с продажи или обновили. Обновите список — он покажет актуальное.</p>
          <button type="button" class="pill" data-refresh-list>Обновить список</button>`;
      document.querySelectorAll(`.item[data-id="${id}"]`).forEach((el) => el.remove());
    } else {
      $("detailBody").innerHTML = `<p class="d-place">Не получилось загрузить объект — похоже, пропала связь.</p>
        <button type="button" class="pill" data-retry="${id}">Повторить</button>`;
    }
  }

  // ─── вход и кабинет ─────────────────────────────────────────────────────
  function renderAccount() {
    const u = me();
    $("accountBtn").textContent = u ? "Кабинет" : "Войти";
    $("accountBtn").classList.toggle("on", !!u);

    const n = u ? u.favorites.length : 0;
    if ($("favBtn")) $("favBtn").innerHTML = `Избранное${n ? `<i class="nav-badge">${n}</i>` : ""}`;
    $("toClientBtn").hidden = !u;
    if (u && $("hero")) $("hero").hidden = true;   // акт 14.10: вошедшему агенту приветствие не занимает первый экран
    if ($("heroLogin")) { $("heroLogin").hidden = !!u; $("heroCabinet").hidden = !u; }
    if (window.yaSelect) window.yaSelect(!!(u && u.is_admin));   // выделил адрес → «В Яндекс Картах»
    const unread = u ? u.unread || 0 : 0;
    $("bellMobile").hidden = !u;
    if (u && window.bellSet) window.bellSet(unread);   // колокольчик с числом (web/bell.js)

    $("optoutLink").hidden = !meta.bot;
    if (meta.bot) $("optoutLink").href = `https://t.me/${meta.bot}?start=optout`;
  }

  async function reloadMeta() {
    try { meta = await getJSON("/api/meta"); } catch { /* оставим как было */ }
    renderAccount();
  }

  function openLogin(lead) {
    $("loginLead").innerHTML = lead || `Номера агентов открываются после входа. Новым — <b>${meta.trial_days || 7} дней бесплатно</b>.`;
    $("tgLogin").hidden = !meta.tg_login;
    $("emailForm").hidden = !meta.email_login;
    $("codeForm").hidden = true;
    document.querySelector("#loginDlg .or").hidden = !(meta.tg_login && meta.email_login);
    $("promoBox").hidden = !meta.promo_login;
    $("tgWait").hidden = true;
    $("loginErr").hidden = true;
    if (!meta.tg_login && !meta.email_login && !meta.promo_login) {
      $("loginLead").textContent = "Вход скоро откроется.";
    }
    openDlg($("loginDlg"));
  }

  function loginError(text) {
    $("loginErr").textContent = text;
    $("loginErr").hidden = false;
  }

  // Вход/подтверждение номера через бота: открываем Telegram и ждём подтверждения
  async function startTelegram(link) {
    const w = window.open("", "_blank");  // открываем сразу, иначе браузер заблокирует окно
    const r = await call("/api/auth/tg/start", link ? { link: true } : {});
    if (!r.ok) {
      if (w) w.close();
      if (!$("loginDlg").open) openLogin();
      loginError(r.data.detail || "Не получилось. Попробуйте ещё раз.");
      return;
    }
    if (w) w.location = r.data.url; else window.location.href = r.data.url;
    if (!$("loginDlg").open) openLogin(link ? "Подтверждаем номер через Telegram." : undefined);
    $("tgWait").hidden = false;
    $("tgWait").innerHTML = `В Telegram нажмите <b>«Старт»</b>, затем <b>«📱 Поделиться номером»</b>. Это окно обновится само.<br>
      Telegram не открылся? <a href="${esc(r.data.url)}" target="_blank" rel="noopener">Открыть бота</a>`;
    clearInterval(tgPoll);
    const started = Date.now();
    tgPoll = setInterval(async () => {
      if (Date.now() - started > 30 * 60 * 1000) { clearInterval(tgPoll); return; }
      const st = await call(`/api/auth/tg/status?t=${encodeURIComponent(r.data.token)}`);
      if (st.ok && st.data.status === "ok") {
        clearInterval(tgPoll);
        await afterLogin();
      } else if (st.data.status === "expired") {
        clearInterval(tgPoll);
        loginError("Время входа вышло — нажмите «Войти через Telegram» ещё раз.");
      } else if (st.data.status === "error") {
        clearInterval(tgPoll);
        loginError("Этот Telegram уже привязан к другому аккаунту.");
      }
    }, 2000);
  }

  async function afterLogin() {
    if ($("loginDlg").open) closeDlg($("loginDlg"));
    await reloadMeta();
    loadSavedSearches();
    toast("Вы вошли");
    refresh();
    if ($("detail").open && openedId) openDetail(openedId);
    if ($("cabinetDlg").open) closeDlg($("cabinetDlg"));
  }

  // «Приведи коллегу — неделя бесплатно»
  function refHTML(r) {
    const link = `${location.origin}/?ref=${r.code}`;
    const text = `Пользуюсь 1+1 — все объекты Краснодара из риелторских чатов в одном поиске, с картой и номерами агентов. Попробуй: ${link}`;
    return `<div class="cab-ref">
      <p class="note">Отправьте ссылку коллеге-риелтору. Когда он войдёт и подтвердит свой номер агента, вам добавится ${r.days} дней доступа.
        ${r.invited ? `Пришли по ссылке: ${r.invited}, подтвердили номер: ${r.rewarded}.` : ""}</p>
      <div class="cab-ref-row"><input readonly value="${esc(link)}" aria-label="Ваша ссылка">
        <button type="button" class="pill light" data-copy-ref="${esc(link)}">Копировать</button>
        <button type="button" class="pill light" data-share-text="${esc(text)}">Поделиться</button></div></div>`;
  }

  function renderCabinet() {
    const u = me();
    if (!u) { closeDlg($("cabinetDlg")); return; }
    const name = u.name || u.email || (u.tg_username && "@" + u.tg_username) || "Коллега";
    const sub = [u.name && u.email, u.tg_username && "@" + u.tg_username].filter(Boolean).join(" · ");
    const now = Date.now() / 1000;
    const left = u.access_until ? Math.max(0, Math.ceil((u.access_until - now) / 86400)) : 0;
    let badge, bar = "";
    if (u.is_admin) badge = `<span class="cab-badge admin">Администратор</span>`;
    else if (u.access) {
      badge = `<span class="cab-badge ok">Доступ до ${esc(fmtDay(u.access_until))}</span>`;
      bar = `<div class="cab-bar" title="Осталось ${left} ${plural(left, "день", "дня", "дней")}"><i style="width:${Math.min(100, left / 7 * 100)}%"></i></div>
        <p class="cab-left">Осталось ${left} ${plural(left, "день", "дня", "дней")}</p>`;
    } else badge = `<span class="cab-badge off">Доступа к номерам нет</span>`;
    const promoUser = !u.tg_username && !u.email;
    const confirm = !u.phone_confirmed && meta.tg_login
      ? `<button type="button" class="pill wide" data-tglink>${promoUser ? "Привязать Telegram" : "Подтвердить номер через Telegram"}${u.trial_used || promoUser ? "" : ` — ${meta.trial_days} дней бесплатно`}</button>` : "";
    const tile = (icon, label, attrs, count) => `<${attrs.startsWith("href") ? "a" : "button type=\"button\""} class="cab-tile2" ${attrs}>
        <i>${icon}</i><span>${label}</span>${count ? `<b>${count}</b>` : ""}</${attrs.startsWith("href") ? "a" : "button"}>`;
    const views = u.views_limit ? `<p class="note">Открыто номеров за сутки: ${u.views_today} из ${u.views_limit}.</p>` : "";
    $("cabinetBody").innerHTML = `
      <div class="cab-head">
        <div class="cab-ava">${esc((name.trim()[0] || "1").toUpperCase())}</div>
        <div class="cab-who"><h2 id="cabTitle">${esc(name)}</h2>${sub ? `<p class="note">${esc(sub)}</p>` : ""}${badge}</div>
      </div>
      ${bar}${views}
      <div class="cab-grid">
        ${tile("♡", "Избранное", 'id="cabFav"', u.favorites.length || "")}
        ${tile("🔔", "Уведомления", 'href="/notifications"', u.unread || "")}
        ${tile("📅", "Планер", 'href="/planner"')}
        ${tile("📁", "Подборки", 'href="/picks"')}
        ${u.is_admin ? tile("🙋", "Запросы", 'href="/requests"') : ""}
        ${tile("📊", "Аналитика", 'href="/market"')}
        ${tile("🏷", "Мои объекты", 'href="/my"')}
        ${u.is_admin ? tile("⚙", "Админка", 'href="/admin"') : ""}
      </div>
      ${u.is_admin ? `<div class="cab-admin" id="cabAdmin"><div class="cab-stats">${"<div class=\"skel\"><i></i></div>".repeat(8)}</div></div>` : ""}
      ${confirm}
      ${u.is_admin ? "" : `<div class="cab-pay"><div><b>Подписка</b><span>${meta.prices || `${meta.price} ₽ за ${meta.period_days} дней`} — номера агентов и все инструменты</span></div>
        ${meta.payments ? `<button type="button" class="pill" data-pay>Оплатить</button>` : `<span class="note">скоро</span>`}</div>`}
      <div class="cab-accs">
        <details class="dv-acc"><summary><span class="dv-acc-ic">🔔</span><span class="dv-acc-t">Слежу за поисками</span><span class="dv-acc-h" id="cabSavedN"></span></summary>
          <div class="dv-acc-b"><div class="cab-saved" id="cabSaved"></div></div></details>
        ${u.ref ? `<details class="dv-acc"><summary><span class="dv-acc-ic">🎁</span><span class="dv-acc-t">Приведи коллегу — +${u.ref.days} дней</span>
          <span class="dv-acc-h">${u.ref.rewarded ? `+${u.ref.rewarded * u.ref.days} дн.` : ""}</span></summary><div class="dv-acc-b">${refHTML(u.ref)}</div></details>` : ""}
        <details class="dv-acc"><summary><span class="dv-acc-ic">🎟</span><span class="dv-acc-t">Промокод</span></summary><div class="dv-acc-b">
          <form class="inline-form" id="promoForm"><input id="promoInput" autocomplete="off" placeholder="Введите промокод" aria-label="Промокод">
            <button type="submit" class="pill light">Применить</button></form><p class="note" id="promoMsg" hidden></p></div></details>
      </div>
      <div class="cab-foot"><button type="button" class="ghost" id="logoutBtn">Выйти</button>
        <button type="button" class="pill light" data-close>Закрыть</button></div>`;
    openDlg($("cabinetDlg"));
    if (u.is_admin) loadAdminStats();
    loadSaved();
  }

  // Подписки на поиск: письмо, когда появились новые объекты
  async function loadSaved() {
    const r = await call("/api/saved");
    const box = $("cabSaved");
    if (!box || !r.ok) return;
    const items = r.data.items || [];
    if ($("cabSavedN")) $("cabSavedN").textContent = items.length || "";
    box.innerHTML = (items.length
      ? `<ul class="saved-list">${items.map((x) => `<li><a href="${esc(x.url)}">${esc(x.title)}</a>
          <button type="button" class="ghost" data-unsave="${x.id}" aria-label="Не следить">✕</button></li>`).join("")}</ul>`
      : `<p class="note">Настройте фильтры и нажмите «🔔 Следить» — пришлём письмо, когда появятся новые объекты.</p>`);
  }

  // Сводка для администратора прямо в кабинете
  async function loadAdminStats() {
    const r = await call("/api/admin/overview");
    const box = $("cabAdmin");
    if (!box || !r.ok) return;
    const o = r.data;
    const tile = (n, label, warn) => `<div class="cab-tile${warn ? " warn" : ""}"><b>${esc(num(n))}</b><span>${esc(label)}</span></div>`;
    box.querySelector(".cab-stats").innerHTML = [
      tile(o.users, "пользователей"), tile(o.users_access, "с доступом"), tile(o.users_paid, "оплатили"),
      tile(o.complaints_new, "новых жалоб", o.complaints_new > 0), tile(o.listings, "объектов на сайте"),
      tile(o.queue, "ждут разбора"), tile(o.views_24h, "открытий номеров за сутки"), tile(o.optouts, "скрытых номеров"),
    ].join("");
  }

  // Статус по объекту: нажали тот же — сняли
  async function setCrm(id, status, btn) {
    const cur = myStatus(id);
    const next = cur === status ? "" : status;
    const r = await call(`/api/notes/${id}`, { status: next });
    if (!r.ok) { toast("Не получилось сохранить статус"); return; }
    if (me()) { me().statuses = me().statuses || {}; if (next) me().statuses[id] = next; else delete me().statuses[id]; }
    const o = known.get(id);
    if (o) o.status = next || null;
    btn.parentElement.querySelectorAll("[data-crm]").forEach((b) => b.classList.toggle("on", b.dataset.crm === next));
    const card = document.querySelector(`.item[data-id="${id}"]`);
    if (card && o) card.outerHTML = itemHTML(o);
    favItems = [];
    toast(next ? `Статус: ${STATUSES[next]}` : "Статус снят", 1800);
  }

  async function toggleFavorite(id) {
    if (!me()) { openLogin("Чтобы сохранять объекты в избранное, войдите."); return; }
    const r = await call(`/api/favorites/${id}`, {});
    if (!r.ok) { toast(r.data.detail || "Не получилось — попробуйте ещё раз."); return; }
    toast(r.data.favorite ? "♥ Добавлено в избранное" : "Убрано из избранного");
    const list = me().favorites;
    const i = list.indexOf(id);
    if (r.data.favorite && i < 0) list.push(id);
    if (!r.data.favorite && i >= 0) list.splice(i, 1);
    renderAccount();
    document.querySelectorAll(`[data-fav="${id}"]`).forEach((b) => {
      b.classList.toggle("on", r.data.favorite);
      b.classList.remove("pop"); void b.offsetWidth; b.classList.add("pop");
      b.setAttribute("aria-label", r.data.favorite ? "Убрать из избранного" : "В избранное");
      if (r.data.favorite && b.offsetParent) burst(b);
    });
    if (favMode && !r.data.favorite) loadList();
  }

  // Искры вокруг сердца, как в приложениях Apple
  function burst(el) {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches || !el.animate) return;
    const r = el.getBoundingClientRect();
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    const colors = ["#e2a596", "#f3e8bc", "#e3cb8f", "#e2a596"];
    for (let i = 0; i < 10; i++) {
      const p = document.createElement("i");
      p.className = "spark";
      p.style.left = `${cx}px`; p.style.top = `${cy}px`;
      p.style.background = colors[i % colors.length];
      document.body.appendChild(p);
      const a = (Math.PI * 2 * i) / 10 + Math.random() * 0.4;
      const d = 22 + Math.random() * 16;
      p.animate([
        { transform: "translate(-50%,-50%) scale(1)", opacity: 1 },
        { transform: `translate(calc(-50% + ${Math.cos(a) * d}px), calc(-50% + ${Math.sin(a) * d}px)) scale(.2)`, opacity: 0 },
      ], { duration: 560 + Math.random() * 200, easing: "cubic-bezier(.2,.9,.25,1)" }).onfinish = () => p.remove();
    }
  }

  function setFavMode(on) {
    if (on && !me()) { openLogin("Чтобы сохранять объекты в избранное, войдите."); return; }
    favMode = on;
    document.body.classList.toggle("fav-mode", on);   // в избранном кнопки поиска (сортировка, «Горячее», «Следить») не нужны
    setTab(on ? "fav" : (state.view === "map" ? "map" : "list"));
    if (on && state.view === "map") { state.view = "list"; syncControls(); applyView(); }
    document.querySelector(".filters").hidden = on;
    $("searchForm").hidden = on;
    loadList();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function pay() {
    const r = await call("/api/pay", {});
    if (r.ok && r.data.url) { window.location.href = r.data.url; return; }
    alert(r.data.detail || "Оплата скоро появится.");
  }

  // ─── события ────────────────────────────────────────────────────────────
  function toggle(list, value) {
    const i = list.indexOf(value);
    if (i >= 0) list.splice(i, 1); else list.push(value);
  }


  function bind() {
    $("searchForm").addEventListener("submit", (e) => { e.preventDefault(); state.q = $("q").value; refresh(); });
    $("q").addEventListener("input", () => { state.q = $("q").value; refresh(350); });

    document.addEventListener("click", (e) => {
      const more = document.querySelector(".nav-more[open]");
      if (more && !more.contains(e.target)) more.open = false;   // «Ещё ▾» — закрыть при клике мимо
      const t = e.target.closest("button");
      if (!t) return;
      if (t.dataset.fav) { e.stopPropagation(); toggleFavorite(Number(t.dataset.fav)); return; }
      if (t.dataset.crm) { setCrm(Number(t.dataset.id), t.dataset.crm, t); return; }
      if (t.dataset.cmp) { e.stopPropagation(); toggleCompare(Number(t.dataset.cmp)); return; }
      if (t.id === "savedToggle") {
        savedHidden = !savedHidden;
        try { localStorage.setItem("savedHidden", savedHidden ? "1" : "0"); } catch { /* */ }
        renderSaved(); return;
      }
      if (t.dataset.clientOpen) { openClientMenu(Number(t.dataset.clientOpen)); return; }
      if (t.dataset.clientAdd) { cmAdd(Number(t.dataset.clientAdd)); return; }
      if (t.dataset.fitAdd) {   // «Подходит вашим клиентам» → объект в подборку клиента
        call(`/api/clients/${t.dataset.fitAdd}/add`, { listing_id: Number(t.dataset.fitFor) }).then((r) => {
          if (!r.ok) { toast(r.data.detail || "Не получилось", 3500); return; }
          t.replaceWith(Object.assign(document.createElement("small"), { textContent: "добавлено в подборку" }));
        });
        return;
      }
      if (t.dataset.copyUrl !== undefined) {
        navigator.clipboard.writeText(t.dataset.copyUrl).then(() => toast("Ссылка на подборку скопирована"), () => prompt("Скопируйте ссылку:", t.dataset.copyUrl));
        return;
      }

      if (t.hasAttribute("data-close-dlg")) { t.closest("dialog").close(); return; }
      if (t.dataset.galStep) {
        const g = t.parentElement.querySelector(".dv-gal");
        g.scrollBy({ left: Number(t.dataset.galStep) * g.clientWidth * .8, behavior: "smooth" });
        return;
      }
      if (t.hasAttribute("data-desc-more")) { const box = t.closest(".dv-desc"); box.classList.toggle("clamp");
        t.textContent = box.classList.contains("clamp") ? "Читать полностью" : "Свернуть"; return; }
      if (t.dataset.adminAct) { adminAction(Number(t.dataset.id), t.dataset.adminAct, t.dataset.deal); return; }
      if (t.dataset.banPhone) { banPhone(t.dataset.banPhone); return; }
      if (t.dataset.pickOpen) { openPicks(Number(t.dataset.pickOpen)); return; }
      if (t.dataset.copyRef) { share(t.dataset.copyRef, "1+1 — поиск объектов Краснодара"); return; }
      if (t.dataset.cmpDel) { toggleCompare(Number(t.dataset.cmpDel)); if (cmp.length >= 2) openCompare(); else closeDlg($("cmpDlg")); return; }
      if (t.id === "cmpOpen") { openCompare(); return; }
      if (t.id === "cmpClear") { const ids = cmp; cmp = []; cmpSave(); ids.forEach((id) => document.querySelectorAll(`[data-cmp="${id}"]`).forEach((b) => b.classList.remove("on"))); renderCmpBar(); return; }
      if (t.dataset.crmFilter !== undefined) { favStatus = t.dataset.crmFilter; loadList(false, true); return; }
      if (t.hasAttribute("data-login")) { openLogin(); return; }
      if (t.dataset.hideOpen) { openHide(Number(t.dataset.hideOpen)); return; }
      if (t.dataset.hideReason) { hideListing(Number(t.dataset.id), t.dataset.hideReason); return; }
      if (t.dataset.share) {
        const o = known.get(Number(t.dataset.share));
        share(`${location.origin}/?open=${t.dataset.share}`, o ? `${o.title} — ${fmtPrice(o.price, o.deal)}` : "Объект на 1+1");
        return;
      }
      if (t.dataset.openId) { openDetail(Number(t.dataset.openId), { push: true }); return; }
      if (t.dataset.geoEdit) { openGeoEdit(Number(t.dataset.geoEdit)); return; }
      if (t.dataset.placeEdit) { openPlaceEdit(Number(t.dataset.placeEdit)); return; }
      if (t.dataset.saved) {
        const id = Number(t.dataset.saved);
        if (activeSaved === id) { activeSaved = 0; setHits(0); renderSaved(); $("resetBtn").click(); return; }
        applySaved(id);
        return;
      }
      if (t.id === "hitOff") { setHits(0); reloadList(); return; }
      if (t.dataset.unsaveChip) {
        const id = Number(t.dataset.unsaveChip);
        const s = savedList.find((x) => x.id === id);
        if (!confirm(`Больше не следить за поиском «${s ? s.title : ""}»?`)) return;
        call("/api/saved", { id }, "DELETE").then((r) => {
          if (!r.ok) { toast("Не получилось удалить"); return; }
          savedList = savedList.filter((x) => x.id !== id);
          if (activeSaved === id) { activeSaved = 0; setHits(0); }
          renderSaved();
          toast("Поиск удалён — уведомления по нему больше не придут");
        });
        return;
      }
      if (t.dataset.unsave) { call("/api/saved", { id: Number(t.dataset.unsave) }, "DELETE").then(loadSaved); return; }
      if (t.dataset.retry) { openDetail(Number(t.dataset.retry)); return; }
      if (t.hasAttribute("data-refresh-list")) { closeDlg($("detail")); reloadList(); return; }
      if (t.hasAttribute("data-sheet-close")) { closeSheet(); return; }
      if (t.dataset.tab) {
        const tab = t.dataset.tab;
        if (tab === "account") { if (me()) location.href = "/cabinet"; else openLogin(); return; }
        if (tab === "fav") { location.href = "/work"; return; }   // одно «Избранное» на весь сайт
        const go = () => {
          if (favMode) setFavMode(false);
          if (state.view !== tab) { state.view = tab; syncControls(); writeUrl(); applyView(); if (tab === "map") loadMap(); else loadList(); }
          else window.scrollTo({ top: 0, behavior: "smooth" });
          setTab(tab);
        };
        if (document.startViewTransition && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) document.startViewTransition(go);
        else go();
        return;
      }
      if (t.hasAttribute("data-tglink")) { startTelegram(!!me()); return; }
      if (t.hasAttribute("data-pay")) { pay(); return; }
      if (t.hasAttribute("data-close")) { closeDlg(t.closest("dialog")); return; }
      if (t.id === "favExit") { setFavMode(false); return; }
      if (t.id === "cabFav") { closeDlg($("cabinetDlg")); setFavMode(true); return; }
      if (t.id === "logoutBtn") {
        call("/api/auth/logout", {}).then(async () => {
          closeDlg($("cabinetDlg")); favMode = false; await reloadMeta(); setFavMode(false); toast("Вы вышли");
        });
        return;
      }
      if (t.dataset.deal) {
        if (state.deal === t.dataset.deal) return;
        state.deal = t.dataset.deal;
        state.priceMin = state.priceMax = "";  // млн ↔ тыс./мес — старые значения не подходят
      } else if (t.dataset.type) toggle(state.types, t.dataset.type);
      else if (t.dataset.room) toggle(state.rooms, Number(t.dataset.room));
      else if (t.dataset.district) toggle(state.districts, t.dataset.district);
      else if (t.dataset.complex) toggle(state.complexes, t.dataset.complex);
      else if (t.dataset.view) {
        if (state.view === t.dataset.view) return;
        state.view = t.dataset.view;
        syncControls();
        writeUrl();
        applyView();
        if (state.view === "map") loadMap(); else loadList();
        return;
      } else return;
      syncControls();
      refresh();
    });

    // админ: история правок объекта и откат одной правки
    const ED_F = { district: "район", extra_districts: "второй район", complex: "ЖК", street: "улица", house: "дом", price: "цена",
      description: "описание", deal: "сделка", rooms: "комнаты", area: "площадь", floor: "этаж", point: "точка на карте" };
    const PT_SRC = { approx: "примерно", learned: "выучена", ok: "точно", pending: "ищется", manual: "ваша", feed: "из фида" };
    const edVal = (v) => (v == null || v === "" ? "—"
      : Array.isArray(v) && v.length === 4 && typeof v[0] !== "string" ? (v[0] == null ? "без точки" : `${(+v[0]).toFixed(5)}, ${(+v[1]).toFixed(5)} (${PT_SRC[v[2]] || v[2]})`)
      : Array.isArray(v) ? (v.join(", ") || "—") : String(v).slice(0, 80));
    async function loadEdits(id) {
      const box = document.getElementById(`dvEd${id}`);
      if (!box) return;
      const r = await call(`/api/admin/edits?listing=${id}`);
      const items = (r.ok && r.data.items) || [];
      box.innerHTML = items.length ? items.map((e) => `<div class="dv-ed${e.reverted ? " off" : ""}">
          <span><b>${esc(ED_F[e.field] || e.field)}</b>: ${esc(edVal(e.old))} → ${esc(edVal(e.new))}</span>
          <small>${esc(e.who)} · ${esc(new Date(e.ts * 1000).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }))}</small>
          ${e.reverted ? `<em>откачено</em>` : `<button type="button" class="chip" data-edit-revert="${e.id}" data-id="${id}">Откатить</button>`}</div>`).join("")
        : `<p class="note">Правок пока не было.</p>`;
    }
    document.addEventListener("click", async (e) => {
      const b = e.target.closest("[data-hist-load], [data-edit-revert]");
      if (!b) return;
      if (b.dataset.histLoad) { loadEdits(Number(b.dataset.histLoad)); return; }
      if (!confirm("Откатить эту правку? Вернётся прежнее значение.")) return;
      const r = await call("/api/admin/edits", { id: Number(b.dataset.editRevert) });
      toast(r.ok && r.data.ok ? "Откатили" : "Эту правку откатить нельзя");
      if (r.ok && r.data.ok) { await openDetail(Number(b.dataset.id)); loadEdits(Number(b.dataset.id)); }
    });

    // админ: удалить фото у объекта
    document.addEventListener("click", async (e) => {
      const b = e.target.closest("[data-photo-del]");
      if (!b) return;
      if (!confirm("Удалить это фото у объекта? Из фида и чатов оно больше не вернётся.")) return;
      const id = Number(b.dataset.id);
      const r = await call(`/api/admin/listings/${id}/action`, { action: "photo", url: b.dataset.photoDel });
      if (!r.ok) { toast(r.data.detail || "Не получилось"); return; }
      b.parentElement.remove();
      const o = known.get(id);
      if (o) o.photos = r.data.photos;
      toast("Фото удалено");
    });

    // журнал работы: вид записи, записать, удалить
    document.addEventListener("click", async (e) => {
      const op = e.target.closest("[data-wk-open]");
      if (op) {
        const f = op.parentElement.querySelector("[data-wk-add]");
        f.hidden = false; op.hidden = true;
        if (window.Pick) { const sel = f.querySelector("select[name=to]"); if (sel) Pick.select(sel, { searchPlaceholder: "Клиент или подборка" }); }
        f.text.focus();
        return;
      }
      const cn = e.target.closest("[data-wk-cancel]");
      if (cn) {
        const f = cn.closest("[data-wk-add]");
        f.hidden = true; f.reset(); f.parentElement.querySelector("[data-wk-open]").hidden = false;
        return;
      }
      const k = e.target.closest("[data-wk-kind]");
      if (k) {
        k.parentElement.querySelectorAll("[data-wk-kind]").forEach((b) => { b.classList.toggle("on", b === k); b.setAttribute("aria-checked", String(b === k)); });
        return;
      }
      const del = e.target.closest("[data-wk-del]");
      if (!del) return;
      if (!confirm("Удалить запись из журнала?")) return;
      const id = Number(del.dataset.id);
      await call("/api/worklog", { id: Number(del.dataset.wkDel) }, "DELETE");
      const r = await call(`/api/worklog?listing=${id}`);
      if (r.ok) refreshJournal(id, r.data);
    });
    document.addEventListener("submit", async (e) => {
      const f = e.target.closest("[data-wk-add]");
      if (!f) return;
      e.preventDefault();
      const id = Number(f.dataset.wkAdd);
      const kind = (f.querySelector("[data-wk-kind].on") || {}).dataset?.wkKind || "note";
      const to = f.to.value, text = f.text.value.trim();
      if (!text && kind === "note") { f.text.focus(); return; }
      const btn = f.querySelector("[type=submit]");
      btn.disabled = true;
      const r = await call("/api/worklog", { listing_id: id, text, kind,
        client_id: to.startsWith("c:") ? Number(to.slice(2)) : null, pick_id: to.startsWith("p:") ? Number(to.slice(2)) : null });
      btn.disabled = false;
      if (!r.ok) { toast(r.data.detail || "Не сохранилось"); return; }
      refreshJournal(id, r.data);
      toast("Записано в журнал", 1600);
    });
    function refreshJournal(id, data) {
      const o = known.get(id);
      if (o) { o.work = data.items; o.work_ctx = data.ctx; }
      document.querySelectorAll(`[data-wk="${id}"]`).forEach((box) => {
        const keep = box.querySelector("select[name=to]");
        const to = keep ? keep.value : "";
        box.innerHTML = journalHTML(o || { id, work: data.items, work_ctx: data.ctx });
        const sel = box.querySelector("select[name=to]");
        if (sel) { sel.value = to; if (window.Pick) Pick.select(sel, { searchPlaceholder: "Клиент или подборка" }); }
      });
    }

    // статус пары клиент+объект из окна объекта («Клиенты по объекту»)
    document.addEventListener("change", (e) => {
      const t = e.target;
      if (!t.dataset || !t.dataset.pairSet) return;
      const [cid, lid] = t.dataset.pairSet.split(":").map(Number);
      call(`/api/clients/${cid}/items`, { listing_id: lid, status: t.value }).then((r) => toast(r.ok ? "Статус сохранён" : (r.data.detail || "Не получилось"), 1500));
    });

    let noteTimer = 0;
    document.addEventListener("input", (e) => {
      const t = e.target;
      if (!t.dataset || !t.dataset.note) return;
      clearTimeout(noteTimer);
      noteTimer = setTimeout(async () => {
        const r = await call(`/api/notes/${t.dataset.note}`, { text: t.value });
        const s = t.parentElement.querySelector(".d-note-saved");
        if (s && r.ok) { s.hidden = false; setTimeout(() => { s.hidden = true; }, 1500); }
      }, 700);
    });
    $("newTodayBtn").addEventListener("click", () => {
      state.fresh = state.fresh === "new1" ? "" : "new1";
      syncControls();
      refresh();
    });
    $("hotBtn").addEventListener("click", () => {
      state.hot = !state.hot;
      syncControls();
      refresh();
      if (state.hot) toast("🔥 Цена снизилась за неделю, ниже рынка или «срочно»", 3200);
    });
    $("watchBtn").addEventListener("click", watchSearch);
    // «Клиент из поиска»: фильтры этой страницы → пожелания нового клиента (/clients?new=1&from=…)
    $("toClientBtn").addEventListener("click", () => {
      const pm = toNum(state.priceMin), px = toNum(state.priceMax);
      const wishes = { types: state.types.filter((t) => ["flat", "house", "land", "commercial"].includes(t)),
        area_min: toNum(state.areaMin) || "", area_max: toNum(state.areaMax) || "", not_first: state.notFirst || undefined,
        complexes: state.complexes };
      const from = { rooms: state.rooms, districts: state.districts, wishes,
        price_min: pm != null ? Math.round(pm * priceMul()) : null, price_max: px != null ? Math.round(px * priceMul()) : null };
      location.href = `/clients?new=1&from=${encodeURIComponent(JSON.stringify(from))}`;
    });
    $("shareSearchBtn").addEventListener("click", () => share(location.href, "Подборка объектов на 1+1"));

    $("psMin").addEventListener("input", () => onSlider("min"));
    $("psMax").addEventListener("input", () => onSlider("max"));
    for (const k of ["areaMin", "areaMax", "landMin", "landMax"]) {
      $(k).addEventListener("input", () => { state[k] = $(k).value; refresh(500); });
    }
    $("notFirst").addEventListener("change", () => { state.notFirst = $("notFirst").checked; refresh(); });
    $("fresh").addEventListener("change", () => { state.fresh = $("fresh").value; refresh(); });
    $("sort").addEventListener("change", () => { state.sort = $("sort").value; refresh(); });

    // районы и ЖК в фильтре — широкий список с поиском, галочками и числом объектов
    if (window.Pick) {
      Pick.multi($("fDistricts"), { placeholder: "Все районы", width: 460, searchPlaceholder: "Найти район: ФМР, Юбилейный…" });
      Pick.multi($("fComplexes"), { placeholder: "Все ЖК", width: 460, searchPlaceholder: "Найти ЖК по названию" });
    }
    $("fDistricts").addEventListener("change", () => {
      state.districts = [...$("fDistricts").selectedOptions].map((o) => o.value);
      renderDistrictChosen(); refresh(400);
    });
    $("fComplexes").addEventListener("change", () => {
      state.complexes = [...$("fComplexes").selectedOptions].map((o) => o.value);
      renderComplexChosen(); refresh(400);
    });
    // Окно «ЖК и район» (админ)
    Combo($("placeDistrict"), { options: () => meta.districts || [], onPick: () => renderPlaceLearn() });
    Combo($("placeComplex"), { options: () => facetComplexes.map((c) => c.name), onPick: () => renderPlaceLearn() });

    $("moreBtn").addEventListener("click", () => {
      const open = $("more").hidden;
      $("more").hidden = !open;
      $("moreBtn").setAttribute("aria-expanded", open);
      $("moreBtn").querySelector("span").textContent = open ? "Скрыть фильтры" : "Ещё фильтры";
    });
    $("resetBtn").addEventListener("click", () => {
      Object.assign(state, {
        types: [], rooms: [], priceMin: "", priceMax: "", areaMin: "", areaMax: "", landMin: "", landMax: "",
        notFirst: false, notLast: false, fresh: "", districts: [], complexes: [], q: "",
      });
      $("q").value = "";
      syncControls();
      refresh();
    });

    $("loadMore").addEventListener("click", () => { page += 1; loadList(true); });
    // Подгрузка при прокрутке: дошли почти до конца — подгружаем следующую страницу
    if ("IntersectionObserver" in window) {
      let busy = false;
      new IntersectionObserver(async (entries) => {
        if (!entries[0].isIntersecting || busy || $("loadMore").hidden) return;
        busy = true; page += 1; await loadList(true); busy = false;
      }, { rootMargin: "900px 0px" }).observe(document.querySelector(".more-results"));  // контейнер виден всегда, кнопка — нет
    }
    $("filterBtn").addEventListener("click", openSheet);
    // Уведомление о cookie — один раз, запоминаем в браузере
    try { if (!EMBED && !localStorage.getItem("cookieOk")) $("cookieBar").hidden = false; } catch { /* приватный режим */ }
    $("cookieOk").addEventListener("click", () => {
      try { localStorage.setItem("cookieOk", "1"); } catch { /* приватный режим */ }
      $("cookieBar").classList.add("bye");
      setTimeout(() => { $("cookieBar").hidden = true; }, 400);
    });
    const onScroll = () => $("nav").classList.toggle("scrolled", window.scrollY > 8);
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
    $("brand").addEventListener("mouseenter", () => {
      const l = $("brand").querySelector(".logo");
      l.classList.remove("redraw"); void l.offsetWidth; l.classList.add("redraw");
    });
    $("sheetOverlay").addEventListener("click", closeSheet);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });
    $("refreshBtn").addEventListener("click", reloadList);
    $("newPill").addEventListener("click", reloadList);
    if (!EMBED) setInterval(checkNew, 60000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) checkNew(); });
    // Фото проявляются плавно, когда загрузились
    document.addEventListener("load", (e) => {
      if (e.target.tagName === "IMG") e.target.classList.add("loaded");
    }, true);
    ["detail", "loginDlg", "cabinetDlg", "cmpDlg", "pickDlg"].forEach((id) => swipeToClose($(id)));
    renderCmpBar();
    $("pickList").addEventListener("click", async (e) => {
      const b = e.target.closest("[data-pick-tog]");
      if (!b) return;
      const add = !b.classList.contains("on");
      b.classList.toggle("on", add);   // сразу видно, запрос — следом
      const r = await call(`/api/picks/${b.dataset.pickTog}/items`, { listing_id: pickFor, add });
      if (!r.ok) { b.classList.toggle("on", !add); toast(r.data.detail || "Не получилось"); return; }
      const sm = b.querySelector("small");
      const n = parseInt(sm.textContent, 10) + (add ? 1 : -1);
      sm.textContent = `${n} ${plural(n, "объект", "объекта", "объектов")}`;
      toast(add ? "Добавлено в подборку" : "Убрано из подборки", 1600);
    });
    $("pickNewForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const r = await call("/api/picks", { title: $("pickNewTitle").value, listing_id: pickFor });
      if (!r.ok) { toast(r.data.detail || "Не получилось"); return; }
      toast("✓ Подборка создана, объект добавлен", 2200);
      openPicks(pickFor);
    });

    $("results").addEventListener("click", (e) => {
      const step = e.target.closest("[data-ph-step]");
      if (step) {   // листаем фото прямо в карточке
        const box = step.closest(".item-photo"), o = known.get(Number(step.closest(".item").dataset.id));
        const list = (o && o.photos) || [];
        const i = (Number(box.dataset.ph) + Number(step.dataset.phStep) + list.length) % list.length;
        box.dataset.ph = i;
        const img = box.querySelector("img");
        img.classList.remove("loaded");
        img.dataset.full = list[i];
        img.src = thumb(list[i]);
        box.querySelector(".ph-count").textContent = `${i + 1} / ${list.length}`;
        return;
      }
      if (e.target.closest("[data-fav], [data-cmp], .plan-btn")) return;
      const li = e.target.closest(".item");
      if (li) openDetail(Number(li.dataset.id));
    });
    $("results").addEventListener("keydown", (e) => {
      const li = e.target.closest(".item");
      if (li && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openDetail(Number(li.dataset.id)); }
    });

    const detail = $("detail");
    $("closeDetail").addEventListener("click", () => closeDlg(detail));
    $("geoSave").addEventListener("click", () => {
      const ll = geoMarker.getLatLng();
      saveGeo({ lat: ll.lat, lon: ll.lng });
    });
    $("geoHide").addEventListener("click", () => saveGeo({ hide: true }));
    $("placeForm").addEventListener("submit", (e) => { e.preventDefault(); savePlace(); });
    $("placeForm").addEventListener("input", (e) => { if (e.target.name === "complex" || e.target.name === "district") renderPlaceLearn(); });
    $("placeForm").addEventListener("change", (e) => { if (e.target.name === "district" || e.target.name === "complex") renderPlaceLearn(); });
    $("placeSkip").addEventListener("click", () => { closeDlg($("placeDlg")); nextPlace(); });
    $("geoSkip").addEventListener("click", () => { closeDlg($("geoDlg")); nextInQueue(); });
    $("geoFind").addEventListener("submit", (e) => {
      e.preventDefault();
      const ll = parseCoords($("geoCoords").value);
      if (!ll) { toast("Не понял координаты. Пример: 45.0355, 38.9753"); return; }
      geoMarker.setLatLng(ll);
      geoMap.setView(ll, 17);
      showGeoNow();
      toast("Метка перенесена — проверьте и нажмите «Сохранить точку»");
    });
    $("geoAuto").addEventListener("click", () => saveGeo({ auto: true }));
    $("detailBack").addEventListener("click", () => {
      const prev = detailStack.pop();
      if (prev) openDetail(prev);
    });
    detail.addEventListener("close", () => { detailStack = []; openedId = 0; setOpenParam(null); toParent("close"); });

    $("accountBtn").addEventListener("click", () => (me() ? (location.href = "/cabinet") : openLogin()));

    $("tgLogin").addEventListener("click", () => startTelegram(false));
    $("loginDlg").addEventListener("close", () => clearInterval(tgPoll));
    $("emailForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const email = $("emailInput").value.trim();
      if (!email) { loginError("Впишите почту."); return; }
      const btn = $("emailForm").querySelector("button");
      const label = btn.textContent;
      btn.disabled = true;
      btn.textContent = "Отправляем код…";
      $("loginErr").hidden = true;
      const r = await call("/api/auth/email/start", { email });
      btn.disabled = false;
      btn.textContent = label;
      if (!r.ok) { loginError(r.data.detail || `Не получилось отправить код (ошибка ${r.status}). Попробуйте ещё раз чуть позже.`); return; }
      $("codeForm").hidden = false;
      $("loginLead").innerHTML = `Код отправлен на <b>${esc(email)}</b>. Проверьте и папку «Спам».`;
      $("codeInput").focus();
    });
    $("codeForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const r = await call("/api/auth/email/verify", { email: $("emailInput").value.trim(), code: $("codeInput").value.trim() });
      if (!r.ok) { loginError(r.data.detail || "Код не подошёл."); return; }
      await afterLogin();
    });
    $("accessForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const code = $("accessCode").value.trim();
      if (!code) return;
      const r = await call("/api/login", { code });
      if (!r.ok) { loginError(r.data.detail || "Код не подошёл. Проверьте и введите ещё раз."); return; }
      await afterLogin();
    });
    document.addEventListener("submit", async (e) => {
      const f = e.target;
      if (f.id === "promoForm") {
        e.preventDefault();
        const r = await call("/api/promo", { code: $("promoInput").value.trim() });
        $("promoMsg").hidden = false;
        $("promoMsg").textContent = r.data.text || r.data.detail || "";
        if (r.ok) { meta.me = r.data.me; meta.access = r.data.me.access; renderAccount(); setTimeout(renderCabinet, 1200); }
      } else if (f.dataset.report) {
        e.preventDefault();
        const photos = [...f.querySelectorAll("input[name=ph]:checked")].map((x) => x.value);
        if (f.reason.value === "photo" && !photos.length) { toast("Отметьте фото, которые не подходят"); return; }
        const r = await call("/api/complaints", { listing_id: Number(f.dataset.report), reason: f.reason.value, text: f.text.value, photos });
        f.innerHTML = `<p class="note">${r.ok ? "Спасибо! Проверим и поправим." : esc(r.data.detail || "Не отправилось.")}</p>`;
      }
    });
    document.addEventListener("change", (e) => {
      if (e.target.name === "reason") {
        const hint = e.target.form.querySelector("[data-optout-hint]");
        if (hint) hint.hidden = e.target.value !== "my_phone";
        const pp = e.target.form.querySelector("[data-photo-pick]");
        if (pp) pp.hidden = e.target.value !== "photo";
      }
    });
  }

  // ─── первый экран: живые цифры базы (счётчик «докручивается»), «скрыть» запоминается ───
  function countUp(el, to) {
    const t0 = performance.now(), dur = 900;
    const step = (t) => {
      const k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 3);
      el.textContent = num(Math.round(to * e));
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }
  async function initHero() {
    const hero = $("hero");
    if (!hero) return;
    let hidden = false;
    try { hidden = localStorage.getItem("heroHidden") === "1"; } catch { /* */ }
    if (hidden) { hero.hidden = true; return; }
    $("heroX").addEventListener("click", () => {
      hero.style.transition = "opacity .35s, transform .35s"; hero.style.opacity = "0"; hero.style.transform = "translateY(-8px)";
      setTimeout(() => { hero.hidden = true; }, 340);
      try { localStorage.setItem("heroHidden", "1"); } catch { /* */ }
    });
    $("heroLogin").addEventListener("click", () => openLogin());
    try {
      const s = await getJSON("/api/stats");
      hero.querySelectorAll("[data-k]").forEach((b) => {
        if (s[b.dataset.k]) countUp(b, s[b.dataset.k]); else b.parentElement.hidden = true;   // нули не показываем
      });
    } catch { /* без цифр — тоже красиво */ }
  }

  async function init() {
    readUrl();
    state.notLast = false;   // фильтр «не последний этаж» убран (этажность домов в чатах пишут по-разному)
    if (EMBED) {   // только окно объекта: без списка, карты и первого экрана
      try { meta = await getJSON("/api/meta"); } catch { /* */ }
      renderTypes();
      renderAccount();
      syncControls();
      bind();
      const id = Number(new URLSearchParams(location.search).get("open"));
      if (id) await openDetail(id);
      toParent($("detail").open ? "ready" : "gone");   // объект сняли/удалили — странице-хозяину нечего показывать
      return;
    }
    initHero();
    try { meta = await getJSON("/api/meta"); } catch { /* страница всё равно покажет список */ }
    renderTypes();
    renderAccount();
    if (state.areaMin || state.areaMax || state.landMin || state.landMax || state.notFirst || state.notLast
        || state.fresh || state.districts.length || state.complexes.length) {
      $("more").hidden = false;
      $("moreBtn").setAttribute("aria-expanded", "true");
      $("moreBtn").querySelector("span").textContent = "Скрыть фильтры";
    }
    syncControls();
    bind();
    setTab(state.view === "map" ? "map" : "list");
    refresh();
    const sp = new URLSearchParams(location.search);
    await loadSavedSearches();
    const savedId = Number(sp.get("saved"));
    if (savedId) {
      const since = Number(sp.get("since")) || 0;
      if (savedList.some((s) => s.id === savedId)) applySaved(savedId, since);
      else {
        sp.delete("saved"); sp.delete("since");
        history.replaceState(null, "", sp.toString() ? `?${sp}` : location.pathname);
      }
    }
    const openId = Number(sp.get("open"));
    if (openId) openDetail(openId);  // ссылка на объект: /?open=123
    // Ссылки со страницы уведомлений: открыть нужный раздел и убрать служебный параметр из адреса
    // Из админки «Карта»: /?open=ID&geo=none — сразу окно правки точки, после сохранения — следующий объект
    if (openId && sp.get("place") && me() && me().is_admin) {
      placeQueue = sp.get("place");
      setTimeout(() => openPlaceEdit(openId), 700);
    }
    if (openId && sp.get("geo") && me() && me().is_admin) {
      geoQueue = sp.get("geo");
      setTimeout(() => openGeoEdit(openId), 700);
    }
    const act = ["login", "fav", "cabinet", "agent"].find((k) => sp.has(k));
    if (act) {
      sp.delete(act);
      history.replaceState(null, "", sp.toString() ? `?${sp}` : location.pathname);
      if (act === "login") { if (!me()) openLogin(); }
      else if (!me()) openLogin();
      else if (act === "fav") setFavMode(true);
      else if (act === "cabinet") { location.replace(me() ? "/cabinet" : "/?login=1"); return; }
      else if (act === "agent") { location.replace("/my"); return; }   // «Мои объекты» — отдельная страница
    }
  }

  // Кабинет агента (web/agent.js) пользуется общими помощниками страницы
  window.OnePlus = {
    call, esc, num, toast, openDlg, closeDlg, fmtPrice, fmtDay, me, openDetail, openLogin, reloadList, LOGO,
    meta: () => meta,
  };

  init();
})();
