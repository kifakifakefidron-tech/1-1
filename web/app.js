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
    const lbl = document.querySelector(".range .range-label");
    lbl.textContent = rent ? "Цена, тыс. ₽/мес" : "Цена, млн";
    $("priceMin").setAttribute("aria-label", rent ? "Цена от, тысяч рублей в месяц" : "Цена от, млн");
    $("priceMax").setAttribute("aria-label", rent ? "Цена до, тысяч рублей в месяц" : "Цена до, млн");
    renderComplexChosen();
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
    const where = place(o);
    const tag = o.type === "new" ? `<span class="tag">новостройка</span>` : "";
    const m2 = o.price_m2 && o.deal !== "rent" ? `<div class="price-m2">${num(o.price_m2)} ₽/м²</div>` : "";
    const seen = o.seen_count > 1 ? ` · присылали ${o.seen_count} ${plural(o.seen_count, "раз", "раза", "раз")}` : "";
    const photo = o.photos && o.photos[0]
      ? `<div class="item-photo"><img src="${esc(o.photos[0])}" alt="" loading="lazy"></div>` : "";
    const fav = me() && me().favorites.includes(o.id);
    const strely = o.source === "feed" ? `<span class="tag tag-strely">СТРЕЛЫ</span>` : "";
    return `<li class="item${photo ? " has-photo" : ""}" tabindex="0" data-id="${o.id}">
      ${photo}
      <button type="button" class="fav-btn${fav ? " on" : ""}" data-fav="${o.id}" aria-label="${fav ? "Убрать из избранного" : "В избранное"}">${fav ? "♥" : "♡"}</button>
      <div class="item-price"><div class="price">${esc(fmtPrice(o.price, o.deal))}</div>${m2}</div>
      <h3 class="item-title">${esc(o.title)}${tag}${strely}</h3>
      ${where ? `<div class="item-place">${esc(where)}</div>` : ""}
      ${o.description ? `<p class="item-desc">${esc(o.description)}</p>` : ""}
      <div class="item-meta">${esc(fmtAgo(o.last_seen))}${esc(seen)}</div>
    </li>`;
  }

  async function loadList(append = false) {
    const seq = append ? reqSeq : ++reqSeq;
    if (!append) page = 1;
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
    $("count").innerHTML = favMode
      ? `Избранное: ${num(data.total)} <button type="button" class="link-btn" id="favExit">← ко всем объектам</button>`
      : esc(data.total ? `${num(data.total)} ${plural(data.total, "объект", "объекта", "объектов")}` : "Ничего не нашлось");
    const html = data.items.map(itemHTML).join("");
    if (append) $("results").insertAdjacentHTML("beforeend", html);
    else $("results").innerHTML = html || (favMode
      ? `<li class="empty"><b>Пока пусто</b>Нажмите ♡ на объекте, чтобы сохранить его сюда.</li>`
      : `<li class="empty"><b>Ничего не нашлось</b>Попробуйте убрать часть фильтров или изменить запрос.</li>`);
    $("loadMore").hidden = state.view !== "list" || data.page >= data.pages;
  }

  // ─── карта ──────────────────────────────────────────────────────────────
  function ensureMap() {
    if (map || !window.L) return;
    map = L.map("mapBox", { scrollWheelZoom: true }).setView([45.035, 38.975], 11);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19, attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map);
    cluster = L.markerClusterGroup ? L.markerClusterGroup({ showCoverageOnHover: false, maxClusterRadius: 50 }) : L.layerGroup();
    map.addLayer(cluster);
    $("mapBox").addEventListener("click", (e) => {
      const b = e.target.closest("[data-open]");
      if (b) openDetail(Number(b.dataset.open));
    });
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
      const icon = L.divIcon({ className: "", html: `<div class="pin">${esc(fmtShortPrice(p.price, state.deal))}</div>`, iconSize: null });
      return L.marker([p.lat, p.lon], { icon }).bindPopup(
        `<div class="map-pop"><b>${esc(fmtPrice(p.price, state.deal))}</b>${esc(p.title)}<br><button type="button" data-open="${p.id}">Подробнее</button></div>`);
    });
    if (cluster.addLayers) cluster.addLayers(markers); else markers.forEach((m) => cluster.addLayer(m));
    if (markers.length) map.fitBounds(L.latLngBounds(pts.map((p) => [p.lat, p.lon])).pad(0.1), { maxZoom: 15 });
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

    const chats = o.chats > 1 ? `Объект присылали в ${o.chats} ${plural(o.chats, "чат", "чата", "чатов")}. ` : "";
    const fav = !!o.favorite;
    return `
      ${gallery}
      <button type="button" class="fav-btn d-fav${fav ? " on" : ""}" data-fav="${o.id}">${fav ? "♥ В избранном" : "♡ В избранное"}</button>
      <h2 class="d-title" id="dTitle">${esc(o.title)}</h2>
      <p class="d-place">${esc(place(o))}</p>
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
    } else if (!u.phone_confirmed && !u.trial_used) {
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

  let openedId = 0;
  async function openDetail(id) {
    openedId = id;
    const dlg = $("detail");
    $("detailBody").innerHTML = `<p class="d-place">Загрузка…</p>`;
    if (!dlg.open) dlg.showModal();
    try {
      const o = await getJSON(`/api/listings/${id}`);
      $("detailBody").innerHTML = detailHTML(o);
    } catch {
      $("detailBody").innerHTML = `<p class="d-place">Объект не найден — возможно, его уже убрали из базы.</p>`;
    }
  }

  // ─── вход и кабинет ─────────────────────────────────────────────────────
  function renderAccount() {
    const u = me();
    $("accountBtn").textContent = u ? "Кабинет" : (meta.access ? "Вы вошли ✓" : "Войти");
    $("accountBtn").classList.toggle("on", !!u || !!meta.access);
    $("favBtn").hidden = !u;
    if (u) $("favBtn").textContent = `♡ Избранное${u.favorites.length ? " · " + u.favorites.length : ""}`;
    $("optoutLink").hidden = !meta.bot;
    if (meta.bot) $("optoutLink").href = `https://t.me/${meta.bot}?start=optout`;
  }

  async function reloadMeta() {
    try { meta = await getJSON("/api/meta"); } catch { /* оставим как было */ }
    renderAccount();
  }

  function openLogin(lead) {
    $("loginLead").innerHTML = lead || `Номера агентов открываются после входа. Новым — <b>${meta.trial_days || 7} дней бесплатно</b> после подтверждения номера в Telegram.`;
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
    if (!$("loginDlg").open) $("loginDlg").showModal();
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
    if ($("loginDlg").open) $("loginDlg").close();
    await reloadMeta();
    refresh();
    if ($("detail").open && openedId) openDetail(openedId);
    if ($("cabinetDlg").open) renderCabinet();
  }

  function renderCabinet() {
    const u = me();
    if (!u) { $("cabinetDlg").close(); return; }
    const who = [u.name, u.tg_username && "@" + u.tg_username, u.email].filter(Boolean).join(" · ");
    let status;
    if (u.is_admin) status = "Администратор — доступ к номерам без ограничений.";
    else if (u.access) status = `Доступ к номерам открыт до <b>${esc(fmtDay(u.access_until))}</b>.`;
    else status = "Доступа к номерам сейчас нет.";
    const views = u.views_limit ? `<p class="note">Открыто номеров за сутки: ${u.views_today} из ${u.views_limit}.</p>` : "";
    const confirm = !u.phone_confirmed
      ? `<button type="button" class="pill" data-tglink>Подтвердить номер через Telegram${u.trial_used ? "" : ` — ${meta.trial_days} дней бесплатно`}</button>` : "";
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
    if (!$("cabinetDlg").open) $("cabinetDlg").showModal();
  }

  async function toggleFavorite(id) {
    if (!me()) { openLogin("Чтобы сохранять объекты в избранное, войдите."); return; }
    const r = await call(`/api/favorites/${id}`, {});
    if (!r.ok) return;
    const list = me().favorites;
    const i = list.indexOf(id);
    if (r.data.favorite && i < 0) list.push(id);
    if (!r.data.favorite && i >= 0) list.splice(i, 1);
    renderAccount();
    document.querySelectorAll(`[data-fav="${id}"]`).forEach((b) => {
      b.classList.toggle("on", r.data.favorite);
      b.textContent = b.classList.contains("d-fav") ? (r.data.favorite ? "♥ В избранном" : "♡ В избранное") : (r.data.favorite ? "♥" : "♡");
    });
    if (favMode && !r.data.favorite) loadList();
  }

  function setFavMode(on) {
    favMode = on;
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
      if (t.hasAttribute("data-tglink")) { startTelegram(!!me()); return; }
      if (t.hasAttribute("data-pay")) { pay(); return; }
      if (t.hasAttribute("data-close")) { t.closest("dialog").close(); return; }
      if (t.id === "favExit") { setFavMode(false); return; }
      if (t.id === "cabFav") { $("cabinetDlg").close(); setFavMode(true); return; }
      if (t.id === "logoutBtn") {
        call("/api/auth/logout", {}).then(async () => { $("cabinetDlg").close(); favMode = false; setFavMode(false); await reloadMeta(); });
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

    for (const k of ["priceMin", "priceMax", "areaMin", "areaMax", "landMin", "landMax"]) {
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
    $("closeDetail").addEventListener("click", () => detail.close());
    detail.addEventListener("click", (e) => { if (e.target === detail) detail.close(); });

    $("accountBtn").addEventListener("click", () => (me() ? renderCabinet() : openLogin()));
    $("favBtn").addEventListener("click", () => setFavMode(!favMode));
    $("tgLogin").addEventListener("click", () => startTelegram(false));
    $("loginDlg").addEventListener("close", () => clearInterval(tgPoll));
    $("emailForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const email = $("emailInput").value.trim();
      if (!email) return;
      const r = await call("/api/auth/email/start", { email });
      if (!r.ok) { loginError(r.data.detail || "Не получилось отправить код."); return; }
      $("loginErr").hidden = true;
      $("codeForm").hidden = false;
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
    refresh();
    const openId = Number(new URLSearchParams(location.search).get("open"));
    if (openId) openDetail(openId);  // ссылка из админки: /?open=123
  }

  init();
})();
