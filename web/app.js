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
    notFirst: false, notLast: false, fresh: "", districts: [], complexes: [],
    sort: "new", view: "list",
  };
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
    return [o.district, o.settlement, street].filter((x) => x && x !== head).join(" · ");
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
    notLast: "nl", fresh: "fresh", districts: "district", complexes: "complex", sort: "sort", view: "view",
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
    if (state.fresh) p.set("fresh_days", state.fresh);
    for (const d of state.districts) p.append("district", d);
    for (const c of state.complexes) p.append("complex", c);
    p.set("sort", state.sort);
    for (const [k, v] of Object.entries(extra)) p.set(k, v);
    return p;
  }

  async function getJSON(url, opts) {
    const r = await fetch(url, { credentials: "same-origin", ...opts });
    if (!r.ok) throw new Error(`${r.status}`);
    return r.json();
  }

  // POST/GET с понятной ошибкой: { ok, status, data }
  async function call(url, body, method) {
    const opts = { credentials: "same-origin", method: method || (body !== undefined ? "POST" : "GET") };
    if (body !== undefined) { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(body); }
    try {
      const r = await fetch(url, opts);
      let data = {};
      try { data = await r.json(); } catch { /* пустой ответ */ }
      return { ok: r.ok, status: r.status, data };
    } catch {
      return { ok: false, status: 0, data: { detail: "Нет связи с сайтом. Проверьте интернет." } };
    }
  }
  const me = () => meta.me || null;
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
    $("sort").value = state.sort;
    const rent = state.deal === "rent";
    setSliderFromState();
    renderComplexChosen();
    const fc = filtersCount();
    $("filterBadge").hidden = !fc;
    $("filterBadge").textContent = fc;
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
    $("complexList").innerHTML = f.complexes.map((c) => `<option value="${esc(c.name)}">${esc(c.n)}</option>`).join("");
  }

  // ─── результаты ─────────────────────────────────────────────────────────
  function itemHTML(o) {
    const tag = "";
    const m2 = o.price_m2 && o.deal !== "rent" ? `<div class="price-m2">${num(o.price_m2)} ₽/м²</div>` : "";
    const photo = o.photos && o.photos[0]
      ? `<div class="item-photo"><img src="${esc(o.photos[0])}" alt="" loading="lazy"></div>` : "";
    const fav = me() && me().favorites.includes(o.id);
    const strely = o.source === "feed" ? `<span class="tag tag-strely">Партнёр</span>` : "";
    const addr = address(o);
    return `<li class="item${photo ? " has-photo" : ""}" tabindex="0" data-id="${o.id}">
      ${photo}
      <button type="button" class="fav-btn${fav ? " on" : ""}" data-fav="${o.id}" aria-label="${fav ? "Убрать из избранного" : "В избранное"}">${fav ? "♥" : "♡"}</button>
      ${headline(o) || strely ? `<h3 class="item-head">${esc(headline(o))}${strely}</h3>` : ""}
      <div class="item-price"><div class="price">${esc(fmtPrice(o.price, o.deal))}</div>${m2}</div>
      <div class="item-title">${esc(o.title)}${tag}</div>
      ${addr ? `<div class="item-place">${esc(addr)}</div>` : ""}
      ${o.description ? `<p class="item-desc">${esc(o.description)}</p>` : ""}
      <div class="item-meta">${esc(fmtAgo(o.last_seen))}</div>
    </li>`;
  }

  function skeletons(n) {
    return Array.from({ length: n }, () => `<li class="item skel" aria-hidden="true"><i></i><i></i><i></i><i></i></li>`).join("");
  }

  async function loadList(append = false) {
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
        const fav = await getJSON("/api/favorites");
        data = { total: fav.items.length, page: 1, pages: 1, items: fav.items };
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
      ? `Избранное: ${num(data.total)} <button type="button" class="link-btn" id="favExit">← ко всем объектам</button>`
      : esc(data.total ? `${num(data.total)} ${plural(data.total, "объект", "объекта", "объектов")}` : "Ничего не нашлось");
    // Карточки появляются волной: у каждой своя небольшая задержка
    const html = data.items.map((o, i) => itemHTML(o).replace('<li class="item', `<li style="--d:${Math.min(i, 12) * 35}ms" class="item`)).join("");
    if (append) $("results").insertAdjacentHTML("beforeend", html);
    else $("results").innerHTML = html || (favMode
      ? `<li class="empty"><b>Пока пусто</b>Нажмите ♡ на объекте, чтобы сохранить его сюда.</li>`
      : `<li class="empty"><b>Ничего не нашлось</b>Попробуйте убрать часть фильтров или изменить запрос.</li>`);
    $("loadMore").hidden = state.view !== "list" || data.page >= data.pages;
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

  function popupHTML(p) {
    const head = p.complex ? `ЖК ${p.complex}` : (p.district || (p.street ? `ул. ${p.street}` : ""));
    return `<div class="map-card" data-open="${p.id}">
      ${p.photo ? `<img src="${esc(p.photo)}" alt="" loading="lazy">` : ""}
      <div class="mc-body">
        ${head ? `<div class="mc-head">${esc(head)}${p.source === "feed" ? ` <span class="tag tag-strely">Партнёр</span>` : ""}</div>` : ""}
        <div class="mc-price">${esc(fmtPrice(p.price, state.deal))}</div>
        <div class="mc-title">${esc(p.title)}</div>
        <span class="mc-more">Подробнее →</span>
      </div></div>`;
  }

  async function loadMap() {
    ensureMap();
    if (!map) return;
    const seq = reqSeq;
    let pts;
    try { pts = await getJSON(`/api/map?${apiParams()}`); } catch { return; }
    if (seq !== reqSeq) return;
    cluster.clearLayers();
    const markers = pts.map((p) => {
      const icon = L.divIcon({
        className: "pin-wrap", iconSize: null,
        html: `<div class="pin${p.source === "feed" ? " partner" : ""}">${esc(fmtShortPrice(p.price, state.deal))}</div>`,
      });
      return L.marker([p.lat, p.lon], { icon, riseOnHover: true })
        .bindPopup(popupHTML(p), { closeButton: false, className: "map-pop", offset: [0, -30], maxWidth: 260, minWidth: 220 });
    });
    if (cluster.addLayers) cluster.addLayers(markers); else markers.forEach((m) => cluster.addLayer(m));
    if (markers.length) map.flyToBounds(L.latLngBounds(pts.map((p) => [p.lat, p.lon])).pad(0.1), { maxZoom: 15, duration: 0.6 });
    setTimeout(() => map.invalidateSize(), 0);
  }

  function applyView() {
    const isMap = state.view === "map";
    $("mapBox").hidden = !isMap;
    $("results").hidden = isMap;
    if (isMap) $("loadMore").hidden = true;
  }

  // ─── всё вместе ─────────────────────────────────────────────────────────
  let timer = 0;
  function refresh(delay = 0) {
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
    const site = o.url ? `<a class="pill light" href="${esc(o.url)}" target="_blank" rel="noopener">Смотреть на сайте СТРЕЛ →</a>` : "";

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
      <button type="button" class="fav-btn d-fav${fav ? " on" : ""}" data-fav="${o.id}">${fav ? "♥ В избранном" : "♡ В избранное"}</button>
      <h2 class="d-title" id="dTitle">${esc(headline(o) || o.title)}</h2>
      <p class="d-place">${esc([headline(o) ? o.title : "", address(o)].filter(Boolean).join(" · "))}</p>
      <p class="d-price">${esc(fmtPrice(o.price, o.deal))}</p>
      <p class="d-price-m2">${o.price_m2 && o.deal !== "rent" ? esc(num(o.price_m2)) + " ₽/м²" : "&nbsp;"}</p>
      <dl class="d-facts">${facts}</dl>
      ${o.description ? `<h3 class="d-h">Описание</h3><p class="d-desc">${esc(o.description)}</p>` : ""}
      ${contact}
      ${site ? `<div class="d-contact">${site}</div>` : ""}
      ${meta.access && o.fragment ? `<h3 class="d-h">Исходное сообщение</h3><pre class="d-source">${esc(o.fragment)}</pre>` : ""}
      ${hist ? `<details class="d-hist-box"><summary>История · ${o.history.length} ${plural(o.history.length, "сообщение", "сообщения", "сообщений")}</summary><ul class="d-hist">${hist}</ul></details>` : ""}
      <p class="d-note">${esc(chats)}Впервые: ${esc(fmtAgo(o.first_seen))}, последний раз: ${esc(fmtAgo(o.last_seen))}.</p>
      ${reportHTML(o)}`;
  }

  // Телефон агента: открыт по подписке; иначе — скрыт с понятным следующим шагом
  function contactHTML(o) {
    if (o.source === "feed") {
      return meta.public_contact
        ? `<div class="d-contact"><a class="pill" href="${esc(meta.public_contact)}" target="_blank" rel="noopener">${esc(meta.public_contact_label || "Узнать подробности")} →</a></div>` : "";
    }
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
  function toast(text) {
    const t = $("toast");
    t.textContent = text;
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove("show"), 2200);
  }

  // ─── новые объекты, пока человек на сайте ───────────────────────────────
  function hideNewPill() { $("newPill").classList.remove("show"); }

  async function checkNew() {
    if (document.hidden || favMode || !loadedAt) return;
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
      + (state.notFirst ? 1 : 0) + (state.notLast ? 1 : 0) + (state.deal === "rent" ? 1 : 0);
  }

  // ─── нижняя панель (телефон) ────────────────────────────────────────────
  function setTab(tab) {
    document.querySelectorAll(".tabbar [data-tab]").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  }

  let openedId = 0;
  async function openDetail(id) {
    openedId = id;
    const dlg = $("detail");
    $("detailBody").innerHTML = `<p class="d-place">Загрузка…</p>`;
    openDlg(dlg);
    dlg.scrollTop = 0;
    let r = await call(`/api/listings/${id}`);
    if (!r.ok && r.status !== 404) r = await call(`/api/listings/${id}`);  // одна тихая повторная попытка
    if (openedId !== id) return;  // пока грузилось, открыли другой объект
    if (r.ok) {
      $("detailBody").innerHTML = detailHTML(r.data);
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
    $("favBtn").hidden = false;
    const n = u ? u.favorites.length : 0;
    $("favBtn").textContent = `♡ Избранное${n ? " · " + n : ""}`;
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
      <h2 id="cabTitle">Личный кабинет</h2>
      <p class="note">${esc(who)}</p>
      <p>${status}</p>${views}
      ${confirm}
      ${pay}
      <form class="inline-form" id="promoForm">
        <input id="promoInput" autocomplete="off" placeholder="Промокод" aria-label="Промокод">
        <button type="submit" class="pill light">Применить</button>
      </form>
      <p class="note" id="promoMsg" hidden></p>
      <div class="dlg-actions cab-actions">
        <button type="button" class="ghost" id="cabFav">♡ Избранное (${u.favorites.length})</button>
        ${u.is_admin ? `<a class="ghost" href="/admin">Админка</a>` : ""}
        <button type="button" class="ghost" id="logoutBtn">Выйти</button>
        <button type="button" class="pill light" data-close>Закрыть</button>
      </div>`;
    openDlg($("cabinetDlg"));
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
      b.textContent = b.classList.contains("d-fav") ? (r.data.favorite ? "♥ В избранном" : "♡ В избранное") : (r.data.favorite ? "♥" : "♡");
    });
    if (favMode && !r.data.favorite) loadList();
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
      if (t.hasAttribute("data-login")) { openLogin(); return; }
      if (t.dataset.retry) { openDetail(Number(t.dataset.retry)); return; }
      if (t.hasAttribute("data-refresh-list")) { closeDlg($("detail")); reloadList(); return; }
      if (t.hasAttribute("data-sheet-close")) { closeSheet(); return; }
      if (t.dataset.tab) {
        const tab = t.dataset.tab;
        if (tab === "account") { me() ? renderCabinet() : openLogin(); return; }
        if (tab === "fav") { setFavMode(true); return; }
        if (favMode) setFavMode(false);
        if (state.view !== tab) { state.view = tab; syncControls(); writeUrl(); applyView(); if (tab === "map") loadMap(); else loadList(); }
        else window.scrollTo({ top: 0, behavior: "smooth" });
        setTab(tab);
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

    $("psMin").addEventListener("input", () => onSlider("min"));
    $("psMax").addEventListener("input", () => onSlider("max"));
    for (const k of ["areaMin", "areaMax", "landMin", "landMax"]) {
      $(k).addEventListener("input", () => { state[k] = $(k).value; refresh(500); });
    }
    $("notFirst").addEventListener("change", () => { state.notFirst = $("notFirst").checked; refresh(); });
    $("notLast").addEventListener("change", () => { state.notLast = $("notLast").checked; refresh(); });
    $("fresh").addEventListener("change", () => { state.fresh = $("fresh").value; refresh(); });
    $("sort").addEventListener("change", () => { state.sort = $("sort").value; refresh(); });

    $("complexInput").addEventListener("change", addComplex);
    $("complexInput").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addComplex(); } });

    $("moreBtn").addEventListener("click", () => {
      const open = $("more").hidden;
      $("more").hidden = !open;
      $("moreBtn").setAttribute("aria-expanded", open);
      $("moreBtn").textContent = open ? "Скрыть фильтры" : "Ещё фильтры";
    });
    $("resetBtn").addEventListener("click", () => {
      Object.assign(state, {
        types: [], rooms: [], priceMin: "", priceMax: "", areaMin: "", areaMax: "", landMin: "", landMax: "",
        notFirst: false, notLast: false, fresh: "", districts: [], complexes: [],
      });
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

    $("accountBtn").addEventListener("click", () => (me() ? renderCabinet() : openLogin()));
    $("favBtn").addEventListener("click", () => setFavMode(!favMode));
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
    const openId = Number(new URLSearchParams(location.search).get("open"));
    if (openId) openDetail(openId);  // ссылка из админки: /?open=123
  }

  init();
})();
