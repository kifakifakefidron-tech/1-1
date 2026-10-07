/* 1+1 · поиск объектов — страница поиска.
   Фильтры → /api/listings, /api/facets, /api/map; карточка → /api/listings/{id};
   вход для коллег → /api/login. Состояние фильтров хранится в адресе страницы,
   поэтому ссылкой на поиск можно поделиться. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const PAGE_SIZE = 32;  // делится и на 4 (компьютер), и на 2 (телефон)

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
    return [ds, o.settlement, street].filter((x) => x && x !== head).join(" · ");
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
    if (state.notLast) p.set("not_last", "1");
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
    $("notLast").checked = state.notLast;
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
    const names = [...f.districts.map((d) => d.name), ...state.districts.filter((d) => !counts.has(d))];
    $("districts").innerHTML = names.length
      ? names.map((n) => {
          const on = state.districts.includes(n);
          return `<button type="button" class="chip${on ? " on" : ""}" data-district="${esc(n)}" aria-pressed="${on}">${esc(n)}<span class="n">${counts.get(n) ?? 0}</span></button>`;
        }).join("")
      : `<span class="range-label">Пока нет объектов с районом</span>`;
    facetComplexes = f.complexes;   // для выпадающего списка ЖК
  }

  // Статусы по объекту — простая CRM агента (видит только он)
  const STATUSES = { call: "📞 Звонил", show: "👀 Показ", think: "🤔 Думает", refuse: "❌ Отказ", deal: "✅ Сделка" };
  let favItems = [], favStatus = "";
  const myStatus = (id) => (me() && me().statuses ? me().statuses[id] : null);

  // ─── результаты ─────────────────────────────────────────────────────────
  // Отметки на карточке: новое за сутки, цена снизилась (за 2 недели), заметно ниже рынка
  function badgesHTML(o) {
    const now = loadedAt || Date.now() / 1000;
    const b = [];
    const st = myStatus(o.id);
    if (st) b.push(`<span class="mark crm crm-${st}">${STATUSES[st]}</span>`);
    if (o.source === "feed") b.push(`<span class="mark partner">Партнёр</span>`);
    if (o.first_seen > now - 86400) b.push(`<span class="mark new">Новое</span>`);
    if (o.prev_price && o.price && o.prev_price > o.price && o.price_changed_at > now - 14 * 86400) {
      b.push(`<span class="mark down">Цена ↓ ${esc(fmtShortPrice(o.prev_price - o.price, o.deal))}</span>`);
    }
    if (o.market_diff != null) b.push(`<span class="mark good">ниже рынка на ${Math.abs(o.market_diff)}%</span>`);
    if (/срочн/i.test(o.description || "")) b.push(`<span class="mark hot">🔥 Срочно</span>`);
    return b.length ? `<div class="marks">${b.join("")}</div>` : "";
  }

  // Сколько дней объект в продаже (с первого появления у нас)
  const daysOnSale = (o) => Math.max(0, Math.floor(((loadedAt || Date.now() / 1000) - o.first_seen) / 86400));
  const daysText = (n) => (n < 1 ? "меньше суток" : `${n} ${plural(n, "день", "дня", "дней")}`);

  function itemHTML(o) {
    const hit = hitSince && o.first_seen > hitSince;   // новое в этом поиске — выделяем один раз
    const tag = (hit ? `<div class="marks"><span class="mark hit">Новое в вашем поиске</span></div>` : "") + badgesHTML(o);
    const m2 = o.price_m2 && o.deal !== "rent" ? `<div class="price-m2">${num(o.price_m2)} ₽/м²</div>` : "";
    const photo = o.photos && o.photos[0]
      ? `<div class="item-photo"><img src="${esc(o.photos[0])}" alt="" loading="lazy"></div>` : "";
    const fav = me() && me().favorites.includes(o.id);
    const addr = address(o);
    return `<li class="item${photo ? " has-photo" : ""}${hit ? " hit" : ""}" tabindex="0" data-id="${o.id}">
      ${photo}
      <button type="button" class="fav-btn${fav ? " on" : ""}" data-fav="${o.id}" aria-label="${fav ? "Убрать из избранного" : "В избранное"}">${HEART}</button>
      ${headline(o) ? `<h3 class="item-head">${esc(headline(o))}</h3>` : ""}
      <div class="item-price"><div class="price">${esc(fmtPrice(o.price, o.deal))}</div>${m2}</div>
      <div class="item-title">${esc(o.title)}</div>
      ${tag}
      ${addr ? `<div class="item-place">${esc(addr)}</div>` : ""}
      ${o.description ? `<p class="item-desc">${esc(o.description)}</p>` : ""}
      <div class="item-meta"><span class="on-sale${daysOnSale(o) >= 30 ? " long" : ""}">в продаже ${esc(daysText(daysOnSale(o)))}</span> · ${esc(fmtAgo(o.last_seen))}</div>
    </li>`;
  }

  function skeletons(n) {
    return Array.from({ length: n }, () => `<li class="item skel" aria-hidden="true"><i></i><i></i><i></i><i></i></li>`).join("");
  }

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
      ? `Избранное и в работе: ${num(data.total)} <button type="button" class="link-btn" id="favExit">← ко всем объектам</button>${crmChips()}`
      : esc(data.total ? `${num(data.total)} ${plural(data.total, "объект", "объекта", "объектов")}` : "Ничего не нашлось");
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
        const n = c.getChildCount();
        const size = n < 10 ? 40 : n < 50 ? 48 : 58;
        return L.divIcon({ className: "cl-wrap", iconSize: [size, size], html: `<div class="cl"><span>${n}</span></div>` });
      },
    }) : L.layerGroup();
    map.addLayer(cluster);
    $("mapBox").addEventListener("click", (e) => {
      const b = e.target.closest("[data-open]");
      if (b) openDetail(Number(b.dataset.open));
    });
  }

  function popupHTML(p, extra) {
    const head = p.complex ? `ЖК ${p.complex}` : (p.district || (p.street ? `ул. ${p.street}` : ""));
    return `<div class="map-card" data-pid="${p.id}">
      ${p.photo ? `<img src="${esc(p.photo)}" alt="" loading="lazy">` : ""}
      <div class="mc-body">
        ${head ? `<div class="mc-head">${esc(head)}</div>` : ""}
        <div class="mc-price">${esc(fmtPrice(p.price, state.deal))}</div>
        <div class="mc-title">${esc(p.title)}</div>
        ${p.approx ? `<div class="mc-approx">Место примерное — в объявлении нет точного адреса</div>` : ""}
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
          <a class="mc-wa" href="https://wa.me/${ph.replace(/\D/g, "")}" target="_blank" rel="noopener">WhatsApp</a>`).join("")}</div>`
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
    // Сколько найденных объектов на карте — чтобы было видно, что ничего не потерялось
    const off = res.total - pts.length;
    $("mapNote").textContent = off > 0
      ? `На карте ${num(pts.length)} из ${num(res.total)}. ${res.pending ? `Ещё ${num(res.pending)} — ищем адрес на карте` : `${num(off)} — без адреса в объявлении`}, они есть в списке.`
      : `На карте все ${num(res.total)} ${plural(res.total, "объект", "объекта", "объектов")}.`;
    $("mapNote").hidden = !res.total;
    cluster.clearLayers();
    const markers = pts.map((p) => {
      const icon = L.divIcon({
        className: "pin-wrap", iconSize: null,
        html: `<div class="pin${p.approx ? " approx" : ""}">${esc(fmtShortPrice(p.price, state.deal))}</div>`,
      });
      const mk = L.marker([p.lat, p.lon], { icon, riseOnHover: true })
        .bindPopup(popupHTML(p), { closeButton: false, className: "map-pop", offset: [0, -30], maxWidth: 280, minWidth: 250 });
      mk.on("popupopen", (e) => fillPopup(e.popup, p));
      return mk;
    });
    if (cluster.addLayers) cluster.addLayers(markers); else markers.forEach((m) => cluster.addLayer(m));
    if (markers.length) map.flyToBounds(L.latLngBounds(pts.map((p) => [p.lat, p.lon])).pad(0.1), { maxZoom: 15, duration: 0.6 });
    setTimeout(() => map.invalidateSize(), 0);
  }

  function applyView() {
    const isMap = state.view === "map";
    $("mapBox").hidden = !isMap;
    if (!isMap) $("mapNote").hidden = true;
    $("results").hidden = isMap;
    if (isMap) $("loadMore").hidden = true;
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

  function detailHTML(o) {
    const rooms = o.rooms == null ? null : o.rooms === 0 ? "студия" : o.rooms >= 5 ? "5 и больше" : String(o.rooms);
    const floor = o.floor ? (o.floors ? `${o.floor} из ${o.floors}` : String(o.floor)) : (o.floors ? `этажей: ${o.floors}` : null);
    const facts = [
      fact("Тип", (meta.types || {})[o.type] || null),
      fact("Сделка", o.deal === "rent" ? "аренда" : "продажа"),
      fact("Комнат", rooms),
      fact("Площадь", o.area ? `${num(o.area, 1)} м²` : null),
      fact("Участок", o.land ? `${num(o.land, 1)} сот.` : null),
      fact("Этаж", floor),
      fact("Район", o.district),
      fact("ЖК", o.complex),
      fact("Населённый пункт", o.settlement),
      fact("Адрес", o.street ? `ул. ${o.street}${o.house ? ", " + o.house : ""}` : null),
    ].join("");

    const contact = contactHTML(o);
    const gallery = o.photos && o.photos.length
      ? `<div class="gallery">${o.photos.map((u) => `<a href="${esc(u)}" target="_blank" rel="noopener"><img src="${esc(u)}" alt="" loading="lazy"></a>`).join("")}</div>` : "";
    const site = o.url && o.phones && o.phones.length ? `<a class="pill light" href="${esc(o.url)}" target="_blank" rel="noopener">Подробнее у агента →</a>` : "";

    const hist = (o.history || []).map((h) => {
      const who = meta.access ? [h.chat, h.sender].filter(Boolean).join(" · ") : "";
      return `<li><span>${esc(fmtDate(h.ts))}${h.price ? " — " + esc(fmtPrice(h.price, o.deal)) : ""}</span>
        <span class="who">${esc(who || MATCH[h.match] || "")}</span></li>`;
    }).join("");

    const chats = (o.seen_count > 1 ? `Присылали ${o.seen_count} ${plural(o.seen_count, "раз", "раза", "раз")}` : "")
      + (o.chats > 1 ? ` в ${o.chats} ${plural(o.chats, "чат", "чата", "чатов")}` : "") + (o.seen_count > 1 ? ". " : "");
    const fav = !!o.favorite;
    return `
      ${gallery}
      <h2 class="d-title" id="dTitle">${esc(headline(o) || o.title)}</h2>
      <p class="d-place">${esc([headline(o) ? o.title : "", address(o)].filter(Boolean).join(" · "))}
        ${address(o) ? `<a class="ya-link" href="https://yandex.ru/maps/35/krasnodar/?text=${encodeURIComponent(yaQuery(o))}" target="_blank" rel="noopener">на Яндекс Картах ↗</a>` : ""}</p>
      <p class="d-price">${esc(fmtPrice(o.price, o.deal))}</p>
      <p class="d-price-m2">${o.price_m2 && o.deal !== "rent" ? esc(num(o.price_m2)) + " ₽/м²" : "&nbsp;"}</p>
      <dl class="d-facts">${facts}</dl>
      ${badgesHTML(o)}
      ${saleHTML(o)}
      ${marketHTML(o)}
      ${priceChartHTML(o)}
      ${o.description ? `<h3 class="d-h">Описание</h3><p class="d-desc">${esc(o.description)}</p>` : ""}
      ${contact}
      ${sameHTML(o)}
      ${noteHTML(o)}
      <div class="d-contact"><button type="button" class="pill light" data-share="${o.id}">Поделиться</button></div>
      ${site ? `<div class="d-contact">${site}</div>` : ""}
      ${meta.access && o.fragment ? `<h3 class="d-h">Исходное сообщение</h3><pre class="d-source">${esc(o.fragment)}</pre>` : ""}
      ${hist ? `<details class="d-hist-box"><summary>История · ${o.history.length} ${plural(o.history.length, "сообщение", "сообщения", "сообщений")}</summary><ul class="d-hist">${hist}</ul></details>` : ""}
      <p class="d-note">${esc(chats)}Впервые: ${esc(fmtAgo(o.first_seen))}, последний раз: ${esc(fmtAgo(o.last_seen))}.</p>
      ${o.can_edit ? `<div class="d-contact"><button type="button" class="pill light" data-agent-edit="${o.id}">✎ Изменить моё объявление</button></div>` : ""}
      ${me() && me().is_admin ? `<div class="d-contact"><button type="button" class="pill light" data-place-edit="${o.id}">✎ ЖК и район</button>
        <span class="note">${o.admin_fixed ? "поправлено вами" : ""}</span></div>` : ""}
      ${me() && me().is_admin ? `<div class="d-contact"><button type="button" class="pill light" data-geo-edit="${o.id}">📍 Поправить точку на карте</button>
        <span class="note">${o.geo_status === "manual" ? "точка поставлена вручную" : o.geo_status === "learned" ? "точка из ваших прошлых правок" : o.geo_status === "feed" ? "точка из фида СТРЕЛ" : o.lat ? "точка найдена по адресу" : "точки нет"}</span></div>` : ""}
      ${reportHTML(o)}
      ${brandLine("поиск объектов Краснодара из риелторских чатов")}`;
  }

  function saleHTML(o) {
    if (!o.first_seen) return "";
    const n = daysOnSale(o);
    return `<div class="d-sale${n >= 30 ? " long" : ""}">⏳ В продаже <b>${esc(daysText(n))}</b>${n >= 30
      ? " — долго продаётся, можно торговаться" : ""}<small>считаем с первого появления на 1+1</small></div>`;
  }

  // График цены: ступеньки по датам смены цены, до сегодня
  function priceChartHTML(o) {
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
    return `<details class="d-chart" open><summary>📈 Цена ${word} · ${h.length} ${plural(h.length, "изменение", "изменения", "изменений")}</summary>
      <svg viewBox="0 0 ${W} ${H}" class="d-chart-svg" role="img" aria-label="График цены"><path d="${d}"/>${dots}</svg>
      <ul class="d-chart-list">${rows}</ul></details>`;
  }

  function marketHTML(o) {
    const m = o.market;
    if (!m) return "";
    const word = m.diff <= -3 ? `дешевле рынка на <b>${-m.diff}%</b>` : m.diff >= 3 ? `дороже рынка на <b>${m.diff}%</b>` : "<b>по рынку</b>";
    const cls = m.diff <= -3 ? "good" : m.diff >= 3 ? "high" : "";
    return `<div class="d-market ${cls}"><span>${word}</span>
      <small>средняя цена м² — ${esc(num(m.median))} ₽, ${esc(m.base)}, по ${m.n} ${plural(m.n, "объекту", "объектам", "объектам")}</small></div>`;
  }

  function sameHTML(o) {
    if (!o.same || !o.same.length) return "";
    const n = o.same.length;
    return `<details class="d-same"><summary>Похожие объявления других агентов · ${n}</summary>
      <p class="note">Тот же или очень похожий объект: совпадают ЖК/улица, этаж, комнаты и площадь.
        Агенты описывают объекты по-разному — сверьте детали перед звонком.</p>
      <ul>${o.same.map((s) => `<li><button type="button" class="link" data-open-id="${s.id}">${esc(fmtPrice(s.price, o.deal))}</button>
        <span>${esc(s.title)} · ${esc(fmtAgo(s.last_seen))}</span></li>`).join("")}</ul></details>`;
  }

  function noteHTML(o) {
    if (!me() || o.loading) return "";
    const st = o.status || myStatus(o.id);
    const chips = Object.entries(STATUSES).map(([k, v]) =>
      `<button type="button" class="chip${st === k ? " on" : ""}" data-crm="${k}" data-id="${o.id}">${v}</button>`).join("");
    return `<div class="d-crm"><span class="d-crm-label">Мой статус <small>(видите только вы)</small></span><div class="chips">${chips}</div></div>
      <details class="d-note-box"${o.note ? " open" : ""}><summary>Моя заметка${o.note ? "" : " (видите только вы)"}</summary>
      <textarea class="d-note-text" data-note="${o.id}" rows="3" maxlength="2000"
        placeholder="Например: звонил 12.10, собственник готов торговаться">${esc(o.note || "")}</textarea>
      <span class="note d-note-saved" hidden>Сохранено</span></details>`;
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
    const r = await call(`/api/admin/listings/${placeId}/place`, { complex: f.complex.value, district: f.district.value, learn });
    if (!r.ok) { toast(r.data.detail || "Не получилось сохранить"); return; }
    const o = known.get(placeId);
    if (o) Object.assign(o, { complex: r.data.complex, district: r.data.district, admin_fixed: 1 });
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
  function renderSaved() {
    $("savedRow").hidden = !savedList.length;
    $("savedChips").innerHTML = savedList.map((s) =>
      `<span class="saved-chip${s.id === activeSaved ? " on" : ""}">
        <button type="button" class="saved-open" data-saved="${s.id}" title="${esc(s.title)}">${esc(s.title)}</button>
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
      return `<div class="d-contact">${o.phones.map((p) => {
        const digits = p.replace(/\D/g, "");
        return `<a class="pill" href="tel:${esc(p)}">${esc(p)}</a><a class="pill light" href="https://wa.me/${digits}" target="_blank" rel="noopener">WhatsApp</a>`;
      }).join("")}</div>`;
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
      text = `Доступ к номерам закончился. Подписка — <b>${meta.price} ₽ за ${meta.period_days} дней</b>.`;
      btn = `<button type="button" class="pill" data-pay>Оформить подписку</button>`;
    }
    return `<div class="d-locked">
      <div class="d-locked-num">${esc(o.phones_masked[0])}</div>
      <p>${text}</p>${btn}</div>`;
  }

  function reportHTML(o) {
    return `<details class="d-report"><summary>Сообщить об ошибке</summary>
      <form class="report-form" data-report="${o.id}">
        <select name="reason">
          <option value="sold">Объект продан или неактуален</option>
          <option value="wrong">Неверные данные (цена, этаж, адрес)</option>
          <option value="my_phone">Это мой номер — уберите его</option>
          <option value="other">Другое</option>
        </select>
        <textarea name="text" rows="2" placeholder="Что не так? (необязательно)"></textarea>
        <p class="note" data-optout-hint hidden>Быстрее всего — подтвердить номер в нашем Telegram-боте: он исчезнет с сайта сразу.
          ${meta.bot ? `<a href="https://t.me/${esc(meta.bot)}?start=optout" target="_blank" rel="noopener">Открыть бота</a>` : ""}</p>
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
      dlg.scrollTop = top;
      const fb = $("detailFav");
      if (!fb.innerHTML) fb.innerHTML = HEART;
      fb.dataset.fav = id;
      fb.classList.toggle("on", !!r.data.favorite);
      fb.setAttribute("aria-label", r.data.favorite ? "Убрать из избранного" : "В избранное");
    } else if (r.status === 404) {
      $("detailBody").innerHTML = `<p class="d-place">Этот объект уже сняли с продажи или обновили. Обновите список — он покажет актуальное.</p>
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
    $("tabAccount").textContent = u ? "Кабинет" : "Войти";
    const n = u ? u.favorites.length : 0;
    $("favBtn").innerHTML = `Избранное${n ? `<i class="nav-badge">${n}</i>` : ""}`;
    $("adminLink").hidden = !(u && u.is_admin);
    if (window.yaSelect) window.yaSelect(!!(u && u.is_admin));   // выделил адрес → «В Яндекс Картах»
    const unread = u ? u.unread || 0 : 0;
    $("bellLink").hidden = $("bellMobile").hidden = !u;
    $("bellBadge").hidden = $("bellDot").hidden = !unread;
    $("bellBadge").textContent = unread;
    $("favBadge").hidden = !n;
    $("favBadge").textContent = n;
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
    if ($("cabinetDlg").open) renderCabinet();
  }

  function renderCabinet() {
    const u = me();
    if (!u) { closeDlg($("cabinetDlg")); return; }
    const who = [u.name, u.tg_username && "@" + u.tg_username, u.email].filter(Boolean).join(" · ");
    let status;
    if (u.is_admin) status = "Администратор — доступ к номерам без ограничений.";
    else if (u.access) status = `Доступ к номерам открыт до <b>${esc(fmtDay(u.access_until))}</b>.`;
    else status = "Доступа к номерам сейчас нет.";
    const views = u.views_limit ? `<p class="note">Открыто номеров за сутки: ${u.views_today} из ${u.views_limit}.</p>` : "";
    const promoUser = !u.tg_username && !u.email;
    const confirm = !u.phone_confirmed && meta.tg_login
      ? `${promoUser ? `<p class="note">Вы вошли по коду коллег. Привяжите Telegram — так избранное и доступ сохранятся на любом устройстве.</p>` : ""}
         <button type="button" class="pill" data-tglink>${promoUser ? "Привязать Telegram" : "Подтвердить номер через Telegram"}${u.trial_used || promoUser ? "" : ` — ${meta.trial_days} дней бесплатно`}</button>`
      : (promoUser ? `<p class="note">Вы вошли по коду коллег.</p>` : "");
    const pay = u.is_admin ? "" : `<div class="cab-row"><div><b>Подписка</b><br><span class="note">${meta.price} ₽ за ${meta.period_days} дней</span></div>
      ${meta.payments ? `<button type="button" class="pill" data-pay>Оплатить</button>` : `<span class="note">Оплата появится скоро</span>`}</div>`;
    $("cabinetBody").innerHTML = `
      <div class="dlg-logo">${LOGO}</div>
      <h2 id="cabTitle">${u.is_admin ? "Кабинет администратора" : "Личный кабинет"}</h2>
      <p class="note">${esc(who)}</p>
      <p>${status}</p>${views}
      ${u.is_admin ? `<div class="cab-admin" id="cabAdmin"><div class="cab-stats">${"<div class=\"skel\"><i></i></div>".repeat(6)}</div>
        <a class="pill wide" href="/admin">Открыть админку →</a></div>` : ""}
      ${confirm}
      ${pay}
      <form class="inline-form" id="promoForm">
        <input id="promoInput" autocomplete="off" placeholder="Промокод" aria-label="Промокод">
        <button type="submit" class="pill light">Применить</button>
      </form>
      <p class="note" id="promoMsg" hidden></p>
      <div class="cab-saved" id="cabSaved"></div>
      <div class="dlg-actions cab-actions">
        <button type="button" class="ghost" id="cabFav">♡ Избранное (${u.favorites.length})</button>
        <a class="ghost" href="/notifications">🔔 Уведомления${u.unread ? ` (${u.unread})` : ""}</a>
        <button type="button" class="ghost" data-agent>Я агент: мои объявления</button>
        ${u.is_admin ? `<a class="ghost" href="/admin">Админка</a>` : ""}
        <button type="button" class="ghost" id="logoutBtn">Выйти</button>
        <button type="button" class="pill light" data-close>Закрыть</button>
      </div>`;
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
    box.innerHTML = `<h3 class="d-h">Слежу за поисками · ${items.length}</h3>` + (items.length
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
    const colors = ["#ff8a7a", "#f3e8bc", "#ffd27a", "#ffb3a7"];
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

  function addComplex() {
    const v = $("complexInput").value.trim();
    if (!v) return;
    if (!state.complexes.includes(v)) state.complexes.push(v);
    $("complexInput").value = "";
    renderComplexChosen();
    refresh();
  }

  function bind() {
    $("searchForm").addEventListener("submit", (e) => { e.preventDefault(); state.q = $("q").value; refresh(); });
    $("q").addEventListener("input", () => { state.q = $("q").value; refresh(350); });

    document.addEventListener("click", (e) => {
      const t = e.target.closest("button");
      if (!t) return;
      if (t.dataset.fav) { e.stopPropagation(); toggleFavorite(Number(t.dataset.fav)); return; }
      if (t.dataset.crm) { setCrm(Number(t.dataset.id), t.dataset.crm, t); return; }
      if (t.dataset.crmFilter !== undefined) { favStatus = t.dataset.crmFilter; loadList(false, true); return; }
      if (t.hasAttribute("data-login")) { openLogin(); return; }
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
        if (tab === "account") { me() ? renderCabinet() : openLogin(); return; }
        if (tab === "fav") { setFavMode(true); return; }
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
    $("shareSearchBtn").addEventListener("click", () => share(location.href, "Подборка объектов на 1+1"));

    $("psMin").addEventListener("input", () => onSlider("min"));
    $("psMax").addEventListener("input", () => onSlider("max"));
    for (const k of ["areaMin", "areaMax", "landMin", "landMax"]) {
      $(k).addEventListener("input", () => { state[k] = $(k).value; refresh(500); });
    }
    $("notFirst").addEventListener("change", () => { state.notFirst = $("notFirst").checked; refresh(); });
    $("notLast").addEventListener("change", () => { state.notLast = $("notLast").checked; refresh(); });
    $("fresh").addEventListener("change", () => { state.fresh = $("fresh").value; refresh(); });
    $("sort").addEventListener("change", () => { state.sort = $("sort").value; refresh(); });

    // ЖК в фильтре — выпадающий список с поиском и числом объектов
    Combo($("complexInput"), {
      options: () => facetComplexes.map((c) => ({ value: c.name, meta: `${c.n}` })),
      onPick: () => addComplex(),
    });
    $("complexInput").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addComplex(); } });
    // Окно «ЖК и район» (админ)
    Combo($("placeDistrict"), { options: () => meta.districts || [], onPick: () => renderPlaceLearn() });
    Combo($("placeComplex"), { options: () => facetComplexes.map((c) => c.name), onPick: () => renderPlaceLearn() });

    $("moreBtn").addEventListener("click", () => {
      const open = $("more").hidden;
      $("more").hidden = !open;
      $("moreBtn").setAttribute("aria-expanded", open);
      $("moreBtn").textContent = open ? "Скрыть фильтры" : "Ещё фильтры";
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
    try { if (!localStorage.getItem("cookieOk")) $("cookieBar").hidden = false; } catch { /* приватный режим */ }
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
    setInterval(checkNew, 60000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) checkNew(); });
    // Фото проявляются плавно, когда загрузились
    document.addEventListener("load", (e) => {
      if (e.target.tagName === "IMG") e.target.classList.add("loaded");
    }, true);
    ["detail", "loginDlg", "cabinetDlg"].forEach((id) => swipeToClose($(id)));

    $("results").addEventListener("click", (e) => {
      if (e.target.closest("[data-fav]")) return;
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
    detail.addEventListener("close", () => { detailStack = []; openedId = 0; setOpenParam(null); });

    $("accountBtn").addEventListener("click", () => (me() ? renderCabinet() : openLogin()));

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
        const r = await call("/api/complaints", { listing_id: Number(f.dataset.report), reason: f.reason.value, text: f.text.value });
        f.innerHTML = `<p class="note">${r.ok ? "Спасибо! Проверим и поправим." : esc(r.data.detail || "Не отправилось.")}</p>`;
      }
    });
    document.addEventListener("change", (e) => {
      if (e.target.name === "reason") {
        const hint = e.target.form.querySelector("[data-optout-hint]");
        if (hint) hint.hidden = e.target.value !== "my_phone";
      }
    });
  }

  async function init() {
    readUrl();
    try { meta = await getJSON("/api/meta"); } catch { /* страница всё равно покажет список */ }
    renderTypes();
    renderAccount();
    if (state.areaMin || state.areaMax || state.landMin || state.landMax || state.notFirst || state.notLast
        || state.fresh || state.districts.length || state.complexes.length) {
      $("more").hidden = false;
      $("moreBtn").setAttribute("aria-expanded", "true");
      $("moreBtn").textContent = "Скрыть фильтры";
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
      else if (act === "cabinet") renderCabinet();
      else if (act === "agent") document.dispatchEvent(new CustomEvent("oneplus:agent"));
    }
  }

  // Кабинет агента (web/agent.js) пользуется общими помощниками страницы
  window.OnePlus = {
    call, esc, num, toast, openDlg, closeDlg, fmtPrice, fmtDay, me, openDetail, openLogin, reloadList, LOGO,
    meta: () => meta,
  };

  init();
})();
