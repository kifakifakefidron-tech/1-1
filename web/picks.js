/* Мои подборки для клиентов (/picks), v3: список слева (обложка-коллаж, «клиент открывал»), подборка справа
   (ссылка, оформление, объекты плиткой с перетаскиванием, добавление из избранного).
   API: /api/picks, /api/picks/<id>/items, /api/favorites, /api/clients. ?open=<id> — открыть подборку. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const ic = (n) => (window.ic ? window.ic(n) : "");
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const money = (p, deal) => (!p ? "цена не указана" : deal === "rent" ? `${Math.round(p / 1000).toLocaleString("ru-RU")} тыс. ₽/мес`
    : `${(p / 1e6).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} млн ₽`);
  // цена в поле: продажа — в млн («6,5»), аренда — в тыс. («35»)
  const priceIn = (p, deal) => (!p ? "" : deal === "rent" ? String(Math.round(p / 1000)) : (p / 1e6).toLocaleString("ru-RU", { maximumFractionDigits: 3 }));
  const priceOut = (v, deal) => {
    const n = Number(String(v).replace(/\s/g, "").replace(",", "."));
    if (!n || n < 0) return 0;
    if (deal === "rent") return Math.round(n < 1000 ? n * 1000 : n);
    return Math.round(n < 1000 ? n * 1e6 : n < 100000 ? n * 1000 : n);   // 6,5 → млн; 6500 → тыс.; 6500000 → рубли
  };
  const plural = (n, a, b, c) => (n % 10 === 1 && n % 100 !== 11 ? a : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? b : c);
  const ago = (ts) => {
    if (!ts) return "";
    const m = Math.round((Date.now() / 1000 - ts) / 60);
    if (m < 60) return `${Math.max(1, m)} мин назад`;
    if (m < 1440) return `${Math.round(m / 60)} ч назад`;
    if (m < 2880) return "вчера";
    return new Date(ts * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
  };
  const link = (p) => p.url || `${location.origin}/c/${p.token}`;   // нейтральный адрес подборок, если задан PICK_DOMAIN
  // площадь и этаж уже в заголовке — в подписи только где: ЖК, район, улица
  const sub = (it) => [it.complex ? `ЖК ${it.complex}` : "", it.district, it.street ? `${it.street}${it.house ? `, ${it.house}` : ""}` : ""]
    .filter(Boolean).join(" · ");

  let list = [], favs = null, clientsList = null, q = "", filter = "all", tab = "items", favQ = "";
  const COLORS = ["gold", "teal", "sky", "violet", "coral", "rose", "lime", "slate"];
  const NK = { note: "Заметка", call: "Звонок", show: "Показ", meet: "Встреча" };
  // статус объекта для клиента подборки (одна пара клиент+объект) и ответ клиента со страницы подборки
  const PAIR = { added: "Добавлен", sent: "Отправлен", opened: "Открыл", liked: "Понравился", rejected: "Не подходит", show: "Показ",
    talks: "Переговоры", deal: "Сделка", lost: "Отказ", matched: "Подобран" };
  const REACT = { like: ["Нравится", "ok"], no: ["Не подходит", "bad"], want_show: ["Хочет посмотреть", "ok"], question: ["Вопрос", "gold"] };
  let clientFilter = 0, baseQ = "", baseRes = null, baseTimer = 0;
  const openRows = new Set();   // 24.10: раскрытые строки «Настроить»
  let addOpen = false;          // 24.10: «+ Добавить объект» раскрыт
  let gripDown = false;         // строку тянем только за ⠿ (иначе мешает выделять текст в полях)
  const when = (ts) => new Date(ts * 1000).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  let open = Number(new URLSearchParams(location.search).get("open")) || 0;
  const isPhone = () => matchMedia("(max-width: 900px)").matches;

  async function call(url, body, method) {
    const opts = { credentials: "same-origin", method: method || (body !== undefined ? "POST" : "GET") };
    if (body !== undefined) { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(body); }
    let r = await fetch(url, opts);
    for (let i = 0; i < 3 && r.status === 503; i++) { await new Promise((res) => setTimeout(res, 1200 * (i + 1))); r = await fetch(url, opts); }
    let d = {};
    try { d = await r.json(); } catch { /* пусто */ }
    return { ok: r.ok, status: r.status, data: d };
  }
  function toast(text, ms = 2400) {
    const t = $("toast");
    t.textContent = text;
    try { t.showPopover(); } catch { t.hidden = false; }
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { try { t.hidePopover(); } catch { t.hidden = true; } }, ms);
  }

  async function load() {
    const r = await call("/api/picks");
    if (r.status === 401) { $("pksMsg").hidden = false; $("pksMsg").innerHTML = `Подборки доступны после входа. <a href="/?login=1">Войти</a>`; $("pksNew").hidden = true; return; }
    if (r.status === 403) { $("pksMsg").hidden = false; $("pksMsg").innerHTML = `${esc(r.data.detail)} <a href="/pay">Оформить доступ</a>`; $("pksNew").hidden = true; return; }
    list = r.data.items;
    $("pksApp").hidden = false;
    if (!open && list.length && !isPhone()) open = list[0].id;
    await loadClients();
    render();
    if (open) loadSide();
  }
  async function reload() {
    const r = await call("/api/picks");
    if (r.ok) list = r.data.items;
    render();
  }
  async function loadClients() {
    const r = await call("/api/clients");
    clientsList = r.ok ? (r.data.items || []).filter((c) => !c.archived) : [];
    fillNewClient();
  }
  function clientOptions(sel) {
    return `<option value="">Без клиента</option>${(clientsList || []).map((c) =>
      `<option value="${c.id}"${c.id === sel ? " selected" : ""}>${esc(c.name)}</option>`).join("")}<option value="new">+ Новый клиент…</option>`;
  }
  function fillNewClient(sel) {
    const s = $("pkDupClient");   // выбор клиента — только для копии подборки (17.10)
    if (!s) return;
    s.innerHTML = clientOptions(sel != null ? sel : Number(s.value) || null);
    if (window.Pick) Pick.select(s, { searchPlaceholder: "Найти клиента" });
  }
  async function loadSide() {   // избранное (добавлять) — один раз
    if (favs === null) { const r = await call("/api/favorites"); favs = r.ok ? r.data.items.filter((f) => f.favorite !== false) : []; }
    renderDetail();
  }

  // ─── сводка ───────────────────────────────────────────────────────
  function stats() {
    const week = Date.now() / 1000 - 7 * 86400;
    const objs = list.reduce((s, p) => s + p.n, 0), views = list.reduce((s, p) => s + (p.views || 0), 0);
    const fresh = list.filter((p) => p.viewed_at && p.viewed_at > week).length, wait = list.filter((p) => !p.views && p.n).length;
    const tile = (icon, n, label, cls = "") => `<div class="pk2-stat ${cls}">${ic(icon)}<b>${n}</b><span>${label}</span></div>`;
    $("pkStats").innerHTML = list.length ? tile("folder", list.length, plural(list.length, "подборка", "подборки", "подборок"))
      + tile("home", objs, plural(objs, "объект", "объекта", "объектов"))
      + tile("eye", views, `${plural(views, "просмотр", "просмотра", "просмотров")} клиентами`)
      + tile("check-circle", fresh, "открыли за неделю", fresh ? "ok" : "")
      + tile("hourglass", wait, "ещё не открывали", wait ? "wait" : "") : "";
  }

  // ─── список подборок ──────────────────────────────────────────────
  // в списке подборка — цветная плитка (цвет выбирается в «Оформлении»), буква клиента или названия и число объектов
  function cover(p) {
    const letter = (p.client_name || p.title || "П").trim().charAt(0).toUpperCase();
    return `<span class="pk2-cov c-${esc(p.color)}"><b>${esc(letter)}</b><i>${p.n}</i></span>`;
  }
  function seenBadge(p) {
    p = { ...p, views: p.views_unique || p.views };   // уникальные открытия клиентом (до 13.10 считались все)
    if (p.views) return `<span class="pk2-st seen"><i></i>Открывал ${p.views} ${plural(p.views, "раз", "раза", "раз")} · ${esc(ago(p.viewed_at))}</span>`;
    return `<span class="pk2-st"><i></i>${p.n ? "Ещё не открывал" : "Пустая"}</span>`;
  }
  function visible() {
    const s = q.trim().toLowerCase();
    return list.filter((p) => (!clientFilter || p.client_id === clientFilter) && (filter === "all" || (filter === "seen" ? p.views : !p.views))
      && (!s || `${p.title} ${p.client_name || ""} ${p.items.map((x) => `${x.title} ${x.complex || ""}`).join(" ")}`.toLowerCase().includes(s)));
  }
  function renderList() {
    const items = visible();
    $("pksList").innerHTML = items.map((p) => `<button type="button" class="pk2-li${p.id === open ? " on" : ""}" data-open="${p.id}">
        ${cover(p)}
        <span class="pk2-li-b"><b>${esc(p.title)}</b>
          <small>${p.n} ${plural(p.n, "объект", "объекта", "объектов")}${p.client_name ? ` · ${ic("user")}${esc(p.client_name)}` : ""}</small>
          ${seenBadge(p)}</span></button>`).join("")
      || (list.length ? `<p class="pk2-none">Ничего не нашлось.</p>` : "");
  }

  // ─── подборка ─────────────────────────────────────────────────────
  function emptyState() {
    return `<div class="pk2-empty">
      <svg class="pk2-ill" viewBox="0 0 240 150" aria-hidden="true">
        <rect x="58" y="22" width="124" height="96" rx="16" class="a"/><rect x="40" y="34" width="124" height="96" rx="16" class="b"/>
        <rect x="22" y="46" width="124" height="96" rx="16" class="c"/><rect x="36" y="60" width="96" height="44" rx="9" class="d"/>
        <path d="M36 96l22-18 16 12 14-10 44 22v2a9 9 0 0 1-9 9H45a9 9 0 0 1-9-9z" class="e"/><circle cx="112" cy="72" r="6" class="f"/>
        <rect x="36" y="112" width="58" height="8" rx="4" class="f"/><rect x="36" y="126" width="38" height="6" rx="3" class="d"/>
        <circle cx="196" cy="110" r="22" class="g"/><path d="M186 110l7 7 13-14" class="h"/></svg>
      <h2>${list.length ? "Выберите подборку слева" : "Первая подборка — за минуту"}</h2>
      <ol class="pk2-steps"><li><b>1</b><span>Впишите имя клиента наверху — заведём клиента и его подборку разом</span></li>
        <li><b>2</b><span>Добавьте объекты из избранного или кнопкой «В подборку» в окне объекта</span></li>
        <li><b>3</b><span>Отправьте ссылку — увидите, когда клиент её откроет</span></li></ol>
      ${list.length ? "" : `<button type="button" class="pill" data-focus-new>${ic("plus")}Создать подборку</button>`}</div>`;
  }
  // 18.10 (Артём: «фото в подборке можно перетаскивать»): все фото объекта в том порядке, в каком их увидит клиент.
  // Перетащить — поменять местами (мышь — сразу, палец — подержать 0,3 с); × у своего фото — удалить, у фото объявления — скрыть.
  function photosHTML(p, it) {
    const mine = new Set(it.my_photos || []);
    const vis = it.photos_all || [], hid = it.photos_hidden || [];
    return `<div class="pk2-own" data-phs="${it.listing_id}" data-pid="${p.id}">
      <span class="pk2-own-h">Фото для клиента <small>— перетащите, чтобы поменять порядок; первое — обложка</small></span>
      <div class="pk2-own-list">${vis.map((u, k) => `<span class="pk2-own-ph pk2-pt${k === 0 ? " first" : ""}" data-u="${esc(u)}" draggable="false">
          <img src="${esc(u)}" alt="" loading="lazy" draggable="false">${k === 0 ? `<em>Обложка</em>` : ""}
          ${mine.has(u) ? `<button type="button" data-ph-del="${esc(u)}" data-lid="${it.listing_id}" data-pid="${p.id}" aria-label="Удалить своё фото" title="Удалить своё фото">${ic("x")}</button>`
            : vis.length > 1 ? `<button type="button" data-ph-hide="${esc(u)}" data-lid="${it.listing_id}" data-pid="${p.id}" aria-label="Скрыть от клиента" title="Скрыть от клиента">${ic("x")}</button>` : ""}</span>`).join("")}
        ${(it.my_photos || []).length < 10 ? `<label class="pk2-own-add" title="Добавить свои фото">${ic("plus")}<span>Фото</span>
          <input type="file" accept="image/*" multiple data-ph-add="${it.listing_id}" data-pid="${p.id}" hidden></label>` : ""}</div>
      ${hid.length ? `<div class="pk2-hid"><span>Скрыто от клиента:</span>${hid.map((u) => `<button type="button" class="pk2-hid-ph" data-ph-unhide="${esc(u)}" data-lid="${it.listing_id}" data-pid="${p.id}" title="Вернуть клиенту"><img src="${esc(u)}" alt="" loading="lazy"><i>${ic("plus")}</i></button>`).join("")}</div>` : ""}
    </div>`;
  }
  // 24.10 (образец Артёма «Подборки — максимально просто»): объект — строка (⠿, номер, фото, название, цена, статус,
  // «Настроить»); всё для клиента — в раскрытой строке: цена, фото, статус, текст, комментарий, порядок, запись, убрать
  const ST_CLS = { added: "added", sent: "sent", opened: "open", liked: "ok", rejected: "bad", show: "ok", talks: "ok", deal: "ok", lost: "bad", matched: "sent" };
  function objCard(p, it, i) {
    const on = openRows.has(it.listing_id);
    const st = it.status || "added";
    return `<article class="pk2-obj pk5-row${on ? " on" : ""}${it.is_active ? "" : " gone"}" draggable="true" data-lid="${it.listing_id}" data-pid="${p.id}" style="--i:${i}">
      <div class="pk5-line">
        <span class="pk5-grip" title="Перетащите, чтобы поменять порядок" aria-hidden="true">⠿</span>
        <span class="pk5-n">${i + 1}</span>
        <a class="pk5-ph" href="/?open=${it.listing_id}">${it.photo ? `<img src="${esc(it.photo)}" alt="" loading="lazy">` : `<span>${ic("home")}</span>`}</a>
        <span class="pk5-t"><a href="/?open=${it.listing_id}"><b>${esc(it.title)}</b></a><small>${esc(sub(it))}${it.is_active ? "" : " · снят с сайта"}</small></span>
        <b class="pk5-price${it.hide_price ? " off" : ""}">${it.hide_price ? "скрыта" : esc(money(it.my_price || it.price, it.deal))}</b>
        ${p.client_id ? `<span class="pk5-st ${ST_CLS[st] || ""}"><i></i>${esc(PAIR[st] || "Отправлен")}</span>` : ""}
        ${it.reaction ? `<span class="pk2-re r-${REACT[it.reaction][1]}">${REACT[it.reaction][0]}</span>` : ""}
        <button type="button" class="pk5-tog${on ? " on" : ""}" data-row="${it.listing_id}">${on ? "Свернуть" : "Настроить"}</button>
      </div>
      ${on ? `<div class="pk5-body">
        ${it.is_active ? "" : `<button type="button" class="pk2-sim" data-sim="${it.listing_id}" data-pid="${p.id}">${ic("repeat")}Снят — показать похожие</button>`}
        <div class="pk5-cols">
          <div class="pk5-col">
            <label class="pk2-pin money-wrap"><span>Цена для клиента <small>· в объявлении ${esc(money(it.price, it.deal))}</small></span>
              <span class="pk5-pin"><input data-pprice="${it.listing_id}" data-pid="${p.id}" data-deal="${it.deal}" data-money="${it.deal === "rent" ? "rent" : "sale"}" inputmode="decimal" autocomplete="off"
                value="${it.my_price ? esc(priceIn(it.my_price, it.deal)) : ""}" placeholder="${esc(priceIn(it.price, it.deal) || "впишите")}"
                aria-label="Цена для клиента"${it.hide_price ? " disabled" : ""}><em>${it.deal === "rent" ? "тыс. ₽/мес" : "млн ₽"}</em></span></label>
            <label class="pk2-phide"><input type="checkbox" data-phide="${it.listing_id}" data-pid="${p.id}"${it.hide_price ? " checked" : ""}>Скрыть цену от клиента</label>
            ${photosHTML(p, it)}
            ${p.client_id ? `<label class="pk2-pst"><span>Статус для ${esc(p.client_name || "клиента")}</span><select data-pst="${it.listing_id}" data-pid="${p.id}" aria-label="Статус">
              ${Object.entries(PAIR).map(([k, v]) => `<option value="${k}"${st === k ? " selected" : ""}>${v}</option>`).join("")}</select></label>` : ""}
          </div>
          <div class="pk5-col">
            <label class="pk2-ptext"><span>Текст для клиента <small>· сохраняется сам</small></span>
              <textarea data-ptext="${it.listing_id}" data-pid="${p.id}" data-grow rows="6" maxlength="5000">${esc(it.text || it.orig || "")}</textarea>
              ${it.text ? `<button type="button" class="pk2-ptext-reset" data-ptext-reset="${it.listing_id}" data-pid="${p.id}">Вернуть исходный текст</button>` : ""}</label>
            <label class="pk5-com"><span>Ваш комментарий — клиент увидит</span>
              <input class="pk2-com" data-com="${it.listing_id}" data-pid="${p.id}" maxlength="1000" value="${esc(it.note || "")}"
                placeholder="Например: «Хозяин торгуется»" aria-label="Комментарий для клиента"></label>
            ${it.reaction_text ? `<p class="pk2-hint">Клиент написал: «${esc(it.reaction_text)}»</p>` : ""}
            ${notesFor(p, it)}
          </div>
        </div>
        <div class="pk5-acts">
          <button type="button" class="chip" data-move="-1" data-i="${i}" data-pid="${p.id}" ${i ? "" : "disabled"}>${ic("arrow-up")}Выше</button>
          <button type="button" class="chip" data-move="1" data-i="${i}" data-pid="${p.id}" ${i < p.items.length - 1 ? "" : "disabled"}>${ic("arrow-down")}Ниже</button>
          <a class="chip" href="/planner?add=show&listing=${it.listing_id}${p.client_id ? `&client=${p.client_id}` : ""}">${ic("calendar")}Запись на показ</a>
          <button type="button" class="chip" data-wnote="${it.listing_id}" data-pid="${p.id}">${ic("note")}Заметка</button>
          <span class="pk5-sp"></span>
          <button type="button" class="chip danger" data-rm="${it.listing_id}" data-pid="${p.id}">Убрать из подборки</button>
        </div>
      </div>` : ""}</article>`;
  }
  // журнал по объекту в этой подборке (и про её клиента) — видит только агент
  function notesFor(p, it) {
    const ns = (p.notes || {})[it.listing_id] || [];
    if (!ns.length) return "";
    const n = ns[0];
    return `<div class="pk2-wn" title="Журнал — видите только вы"><span class="pk2-wn-k k-${esc(n.kind)}">${NK[n.kind] || "Заметка"} · ${esc(when(n.ts))}</span>
      ${n.text ? `<p>${esc(n.text)}</p>` : ""}${ns.length > 1 ? `<small>ещё ${ns.length - 1} в журнале объекта</small>` : ""}</div>`;
  }
  function favCard(f, p) {
    return `<div class="pk2-fav">
        <span class="pk2-fph">${f.photos && f.photos[0] ? `<img src="${esc(f.photos[0])}" alt="" loading="lazy">`
          : `<span class="ph-none"><img src="/static/favicon.svg" alt=""><small>Фото у агента</small></span>`}</span>
        <b>${esc(money(f.price, f.deal))}</b><small>${esc(f.complex ? `ЖК ${f.complex} · ${f.title}` : f.title)}</small>
        ${f.description ? `<p class="pk2-fdesc">${esc(f.description.slice(0, 220))}</p>` : ""}
        <a class="pk2-fopen" href="/?open=${f.id}">Открыть объект</a>
        <button type="button" class="pk2-addb" data-add="${f.id}" data-pid="${p.id}" aria-label="Добавить в подборку">${ic("plus")}</button></div>`;
  }
  function baseStrip(p) {
    if (!baseQ.trim()) return `<p class="pk2-hint">Пишите как в поиске на главной: «2к фмр до 7 млн», «студия самолёт», улица — покажем 20 объектов.</p>`;
    if (baseRes === null) return `<p class="pk2-hint">Ищем…</p>`;
    const inPick = new Set(p.items.map((x) => x.listing_id));
    const items = baseRes.filter((f) => !inPick.has(f.id));
    return items.length ? `<div class="pk2-strip">${items.map((f) => favCard(f, p)).join("")}</div>` : `<p class="pk2-hint">Ничего не нашлось.</p>`;
  }
  function addStrip(p) {
    if (favs === null) return `<p class="pk2-hint">Загружаем избранное…</p>`;
    const inPick = new Set(p.items.map((x) => x.listing_id));
    const s = favQ.trim().toLowerCase();
    const add = favs.filter((f) => !inPick.has(f.id) && (!s || `${f.title} ${f.complex || ""} ${f.district || ""}`.toLowerCase().includes(s)));
    if (!favs.length) return `<p class="pk2-hint">Избранное пусто. Отмечайте объекты сердечком на <a href="/">поиске</a> — они появятся здесь.</p>`;
    if (!add.length) return `<p class="pk2-hint">${s ? "Ничего не нашлось." : "Всё избранное уже в подборке."}</p>`;
    return `<div class="pk2-strip">${add.slice(0, 40).map((f) => favCard(f, p)).join("")}</div>`;
  }
  function renderDetail() {
    const box = $("pkDetail");
    const p = list.find((x) => x.id === open);
    document.body.classList.toggle("pk-open", !!p);
    if (!p) { box.innerHTML = emptyState(); return; }
    const ph = p.items.map((x) => x.photo).filter(Boolean).slice(0, 3);
    box.innerHTML = `
      <button type="button" class="pk2-back" data-back>${ic("chevron-left")}Все подборки</button>
      <header class="pk2-hero pk5-hero">
        <div class="pk2-col n${ph.length} c-${esc(p.color)}">${ph.map((u) => `<img src="${esc(u)}" alt="">`).join("") || `<span>${ic("folder")}</span>`}</div>
        <div class="pk2-hero-b">
          <h2>${esc(p.title)}</h2>
          <p class="pk2-hero-m">${p.client_id ? `<a class="pk5-cl" href="/clients?id=${p.client_id}" title="Карточка клиента: пожелания, история">Клиент ${esc(p.client_name || "")} ›</a>`
            : `<button type="button" class="pk5-cl warn" data-tab="setup">Без клиента — привязать ›</button>`}
            <span>${p.n} ${plural(p.n, "объект", "объекта", "объектов")}</span>${seenBadge(p)}<span class="pk5-upd">обновлена ${esc(ago(p.updated))}</span></p>
        </div>
      </header>
      <div class="pk2-share pk5-share">
        <div class="pk2-link"><span title="${esc(link(p))}">${esc(link(p).replace(/^https?:\/\//, ""))}</span>
          <button type="button" class="pill" data-copy="${p.id}">${ic("link")}Копировать</button></div>
        <button type="button" class="pk2-act" data-need-me="${p.id}" data-share-text="${esc(`${p.title}
${link(p)}`)}">${ic("share")}<span>Поделиться</span></button>
        <a class="pk2-act" href="${esc(link(p))}?as=agent&back=${encodeURIComponent(`${location.origin}/picks?open=${p.id}`)}" title="Ваше открытие не засчитается как просмотр клиента">${ic("eye")}<span>Как видит клиент</span></a>
        <details class="pk5-more"><summary class="pk2-act">${ic("more")}<span>Ещё</span></summary>
          <div class="pk5-menu">
            <button type="button" data-dup="${p.id}">${ic("layers")}Копия для другого клиента</button>
            <button type="button" data-tab="setup">${ic("sliders")}Оформление и контакт</button>
            <button type="button" class="danger" data-del="${p.id}">${ic("trash")}Удалить подборку</button>
          </div></details>
      </div>
      ${p.contact_phone && p.contact_name && p.contact_name !== "Коллега (по коду)" ? "" : `<form class="pk2-me" data-me="${p.id}" autocomplete="off">
        <p>${ic("user")}<b>Как вас показать клиенту?</b> Клиент видит только ваш контакт — без него ему некуда позвонить.</p>
        <div class="pk2-me-f"><input name="name" maxlength="80" placeholder="Ваше имя" value="${esc(p.contact_name && p.contact_name !== "Коллега (по коду)" ? p.contact_name : "")}" required>
          <input name="phone" maxlength="30" inputmode="tel" placeholder="Ваш телефон" value="${esc(p.contact_phone || "")}" required>
          <button type="submit" class="pill">Сохранить</button></div>
        <label class="pk2-me-all"><input type="checkbox" name="all" checked>Для всех подборок</label></form>`}
      <nav class="pk2-tabs"><button type="button" data-tab="items" class="${tab === "items" ? "on" : ""}">Объекты <b>${p.items.length}</b></button>
        <button type="button" data-tab="setup" class="${tab === "setup" ? "on" : ""}">Оформление и контакт</button></nav>
      ${tab === "items" ? `
        ${p.items.length ? `<p class="pk2-hint">Клиент увидит объекты в этом порядке — ${isPhone() ? "стрелки «Выше / Ниже» в «Настроить»" : "перетащите строку за ⠿, чтобы поменять"}.</p>
          <div class="pk2-grid pk5-list" id="pkGrid">${p.items.map((it, i) => objCard(p, it, i)).join("")}</div>`
          : `<div class="pk2-noitems">${ic("home")}<b>В подборке пока нет объектов</b><span>Добавьте из избранного ниже или кнопкой «Клиенту» в окне объекта на поиске.</span></div>`}
        <button type="button" class="pk5-addbtn${addOpen ? " on" : ""}" data-addopen>${ic("plus")}${addOpen ? "Скрыть" : "Добавить объект"}</button>
        ${addOpen || !p.items.length ? `<section class="pk2-add">
          <div class="pk2-add-h"><h3>${ic("heart")}Из избранного</h3>
            <label class="pk2-search sm"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
              <input id="pkFavQ" type="search" value="${esc(favQ)}" placeholder="ЖК, улица" aria-label="Найти в избранном"></label></div>
          <div id="pkFavs">${addStrip(p)}</div>
          <p class="pk2-hint">Нужного нет в избранном? Найдите объект на <a href="/">поиске</a> и нажмите «Клиенту» в окне объекта — он сразу попадёт сюда.</p>
        </section>` : ""}` : `
        <form class="pk2-form" data-edit="${p.id}">
          <label class="agent-field"><span>Название — видите только вы</span><input name="title" maxlength="120" value="${esc(p.title)}"></label>
          <label class="agent-field"><span>Заголовок для клиента</span><input name="client_title" maxlength="120" value="${esc(p.client_title || "")}"
            placeholder="Подобрали для вас"></label>
          <label class="agent-field"><span>Сообщение клиенту</span><textarea name="note" rows="4" maxlength="2000"
            placeholder="Например: подобрал варианты по вашему запросу, первые два — лучшие по цене">${esc(p.note || "")}</textarea></label>
          <div class="pk2-two">
            <label class="agent-field"><span>Ваше имя</span><input name="contact_name" maxlength="100" value="${esc(p.contact_name || "")}" placeholder="Как клиенту вас называть"></label>
            <label class="agent-field"><span>Ваш телефон</span><input name="contact_phone" maxlength="30" inputmode="tel" value="${esc(p.contact_phone || "")}" placeholder="+7…"></label>
          </div>
          <label class="pk2-hide"><input type="checkbox" data-hide-addr="${p.id}"${p.hide_address ? " checked" : ""}>
            <span><b>Скрыть точный адрес</b><small>Клиент увидит улицу без номера дома, точка на карте — примерно в пределах квартала</small></span></label>
          <div class="agent-field"><span>Цвет подборки в списке</span><div class="pk2-colors">${COLORS.map((c) =>
            `<button type="button" class="pk2-color c-${c}${c === p.color ? " on" : ""}" data-color="${c}" aria-label="Цвет ${c}"></button>`).join("")}</div></div>
          <p class="pk2-hint">Цвет, обложку и подпись подборок можно задать в <a href="/settings">Настройках</a> — для всех подборок сразу.</p>
          <div class="pk2-form-acts"><button type="submit" class="pill">Сохранить</button></div>
        </form>`}`;
    if (window.Pick) box.querySelectorAll("select").forEach((s) => Pick.select(s, { searchPlaceholder: "Найти клиента" }));
  }
  function render() { stats(); fillClientFilter(); renderList(); renderDetail(); }

  // ─── действия ─────────────────────────────────────────────────────
  async function setOrder(pid, ids) {
    const p = list.find((x) => x.id === pid);
    p.items.sort((a, b) => ids.indexOf(a.listing_id) - ids.indexOf(b.listing_id));
    renderDetail();
    await call(`/api/picks/${pid}/items`, { order: ids });
  }
  let textFor = null;
  function openText(pid, lid) {
    const it = list.find((x) => x.id === pid).items.find((x) => x.listing_id === lid);
    textFor = { pid, lid };
    $("pkTextArea").value = it.text || it.orig || "";
    $("pkTextReset").hidden = !it.text;
    $("pkText").showModal();
  }
  async function saveText(reset) {
    const { pid, lid } = textFor;
    const r = await call(`/api/picks/${pid}/items`, { listing_id: lid, add: true, text: reset ? "" : $("pkTextArea").value });
    if (!r.ok) { toast(r.data.detail || "Не сохранилось"); return; }
    $("pkText").close();
    toast(reset ? "Вернули исходный текст" : "Текст для клиента сохранён");
    await reload();
  }
  $("pkTextSave").onclick = () => saveText(false);
  $("pkTextReset").onclick = () => saveText(true);

  // 17.10: без имени и телефона клиент видит подборку «ни от кого» — сначала заполнить «Как вас показать клиенту»
  const noMe = (p) => !p || !p.contact_name || !p.contact_phone || p.contact_name === "Коллега (по коду)";
  // 17.10 (Артём): «Копировать» без имени и телефона — понятное окно с объяснением и полями; после «Сохранить» ссылка копируется сама
  function needMe(p, then = "copy") {
    if (!noMe(p)) return false;
    let d = $("pkNeedMe");
    if (!d) { d = document.createElement("dialog"); d.id = "pkNeedMe"; d.className = "pk2-dlg pk2-need"; document.body.appendChild(d); }
    const nm = p && p.contact_name && p.contact_name !== "Коллега (по коду)" ? p.contact_name : "";
    d.innerHTML = `<div class="pk2-need-in">
      <div class="pk2-need-ic">${ic("alert")}</div>
      <h2>Ссылку пока нельзя отправить</h2>
      <p>Клиент увидит в подборке только ваш контакт — без него непонятно, от кого объекты и кому звонить.
        Впишите <b>${!nm && !(p && p.contact_phone) ? "имя и телефон" : !nm ? "имя" : "телефон"}</b> — один раз, дальше подставится само.</p>
      <form class="pk2-me" data-me="${p ? p.id : 0}" data-then="${then}" autocomplete="off">
        <label><span>Ваше имя</span><input name="name" maxlength="80" placeholder="Например: Артём" value="${esc(nm)}" required></label>
        <label><span>Телефон для клиента</span><input name="phone" maxlength="30" inputmode="tel" placeholder="+7 900 000-00-00" value="${esc((p && p.contact_phone) || "")}" required></label>
        <label class="pk2-me-all"><input type="checkbox" name="all" checked>Для всех подборок</label>
        <div class="pk2-need-acts"><button type="button" class="ghost" data-need-cancel>Позже</button>
          <button type="submit" class="pill">Сохранить и скопировать ссылку</button></div>
      </form></div>`;
    d.querySelector("[data-need-cancel]").onclick = () => (window.softClose ? softClose(d) : d.close());
    d.showModal();
    setTimeout(() => { const f = d.querySelector("form"); (f.name.value ? f.phone : f.name).focus(); }, 60);
    return true;
  }
  window.addEventListener("click", (e) => {   // «Поделиться» ловит max.js (document, capture) — window срабатывает раньше
    const b = e.target.closest("[data-need-me]");
    if (!b) return;
    const pk = list.find((x) => x.id === Number(b.dataset.needMe));
    if (needMe(pk)) { e.preventDefault(); e.stopImmediatePropagation(); return; }
    if (pk) { window.pickSent(link(pk)); setTimeout(reload, 800); }   // 24.10b: поделились — «Отправлен»
  }, true);
  document.addEventListener("click", async (e) => {
    const t = e.target.closest("button, [data-open]");
    if (!t || t.closest("#pkText") || t.closest(".pk-panel")) return;
    if (t.dataset.row) {   // 24.10: «Настроить» / «Свернуть» строку объекта
      const lid = Number(t.dataset.row);
      if (openRows.has(lid)) openRows.delete(lid); else openRows.add(lid);
      renderDetail(); return;
    }
    if (t.hasAttribute("data-addopen")) { addOpen = !addOpen; renderDetail(); if (addOpen) loadSide(); return; }
    if (t.dataset.tab && t.closest(".pk5-more")) t.closest(".pk5-more").open = false;
    if (t.dataset.open) {
      open = Number(t.dataset.open); tab = "items"; favQ = "";
      renderList(); renderDetail(); loadSide();
      if (isPhone()) scrollTo({ top: 0 });
      return;
    }
    if (t.hasAttribute("data-back")) { open = 0; renderList(); renderDetail(); return; }
    if (t.hasAttribute("data-focus-new")) { $("pksNew").title.focus(); return; }
    if (t.dataset.tab) { tab = t.dataset.tab; renderDetail(); return; }
    const pid = Number(t.dataset.pid);
    if (t.dataset.copy) {
      const p = list.find((x) => x.id === Number(t.dataset.copy));
      if (needMe(p)) return;
      try { await navigator.clipboard.writeText(link(p)); toast("Ссылка скопирована — отправьте её клиенту"); window.pickSent(link(p)); setTimeout(reload, 600); }
      catch { prompt("Скопируйте ссылку:", link(p)); }
      return;
    }
    if (t.dataset.add) {
      t.disabled = true;
      const r = await call(`/api/picks/${pid}/items`, { listing_id: Number(t.dataset.add), add: true });
      if (!r.ok) { toast(r.data.detail || "Не получилось"); t.disabled = false; return; }
      toast("Добавлено в подборку"); await reload(); return;
    }
    if (t.dataset.text) { openText(pid, Number(t.dataset.text)); return; }
    if (t.dataset.rm) {
      const card = t.closest(".pk2-obj");
      if (card) card.classList.add("out");
      await call(`/api/picks/${pid}/items`, { listing_id: Number(t.dataset.rm), add: false });
      await reload(); return;
    }
    if (t.dataset.move) {
      const p = list.find((x) => x.id === pid);
      const ids = p.items.map((x) => x.listing_id);
      const i = Number(t.dataset.i), j = i + Number(t.dataset.move);
      [ids[i], ids[j]] = [ids[j], ids[i]];
      await setOrder(pid, ids); return;
    }
    if (t.dataset.dup) { dupFor = Number(t.dataset.dup); fillNewClient(null); $("pkDupDlg").showModal(); return; }
    if (t.dataset.ptextReset) {
      const r = await call(`/api/picks/${t.dataset.pid}/items`, { listing_id: Number(t.dataset.ptextReset), add: true, text: "" });
      toast(r.ok ? "Вернули исходный текст" : (r.data.detail || "Не получилось")); if (r.ok) await reload(); return;
    }
    if (t.dataset.del) {
      if (!confirm("Удалить подборку? Ссылка у клиента перестанет открываться.")) return;
      await call("/api/picks", { id: Number(t.dataset.del) }, "DELETE");
      open = 0; toast("Подборка удалена"); await reload(); return;
    }
  });
  // копия подборки для другого клиента (17.10: внутри подборки клиента не выбирают — она для своего клиента)
  let dupFor = 0;
  $("pkDupSave").onclick = async () => {
    const p = list.find((x) => x.id === dupFor);
    if (!p) return;
    const cl = Number($("pkDupClient").value) || null;
    const cName = cl && (clientsList.find((c) => c.id === cl) || {}).name;
    const r = await call("/api/picks", { title: (cName ? `Для ${cName}` : `${p.title} (копия)`).slice(0, 120), note: p.note || "",
      contact_name: p.contact_name || "", contact_phone: p.contact_phone || "", copy_of: p.id, ...(cl ? { client_id: cl } : {}) });
    if (!r.ok) { toast(r.data.detail || "Не получилось"); return; }
    $("pkDupDlg").close();
    open = r.data.pick.id; tab = "items"; toast("Копия готова — у неё своя ссылка"); await reload();
  };
  // текст для клиента — правится прямо в карточке объекта, сохраняется сам
  const textTimers = {};
  document.addEventListener("input", (e) => {
    const ta = e.target;
    if (!ta.dataset || !ta.dataset.ptext) return;
    clearTimeout(textTimers[ta.dataset.ptext]);
    textTimers[ta.dataset.ptext] = setTimeout(async () => {
      const r = await call(`/api/picks/${ta.dataset.pid}/items`, { listing_id: Number(ta.dataset.ptext), add: true, text: ta.value });
      toast(r.ok ? "Текст для клиента сохранён" : (r.data.detail || "Не сохранилось"), 1600);
      const it = (list.find((x) => x.id === Number(ta.dataset.pid)) || { items: [] }).items.find((x) => x.listing_id === Number(ta.dataset.ptext));
      if (r.ok && it) it.text = ta.value;
    }, 900);
  });
  let clientFor = null;   // для формы нового клиента (окно «+ Новый клиент…» в выборе клиента копии)
  document.addEventListener("change", async (e) => {
    const s = e.target;
    if (s.id === "pkDupClient" && s.value === "new") { clientFor = null; openClient(); return; }
    if (!s.dataset || !s.dataset.pkClient) return;
    const pid = Number(s.dataset.pkClient);
    if (s.value === "new") { clientFor = { pid }; openClient(); return; }
    await setClient(pid, Number(s.value) || null);
  });
  async function setClient(pid, cid) {
    const r = await call("/api/picks", { id: pid, client_id: cid });
    if (!r.ok) { toast(r.data.detail || "Не получилось"); return; }
    toast(cid ? "Подборка привязана — объекты уже в карточке клиента" : "Клиент отвязан");
    await reload();
  }
  function openClient() {
    const f = $("pkClientForm");
    f.reset();
    $("pkClientDlg").showModal();
    setTimeout(() => f.name.focus(), 50);
  }
  $("pkClientDlg").addEventListener("close", () => {   // отменили — вернуть выбор как был
    const p = clientFor && list.find((x) => x.id === clientFor.pid);
    if (clientFor && p) renderDetail(); else if (!clientFor && $("pkDupClient").value === "new") fillNewClient(null);
  });
  $("pkClientSave").onclick = async () => {
    const f = $("pkClientForm");
    if (!f.name.value.trim()) { f.name.focus(); return; }
    const r = await call("/api/clients", { name: f.name.value.trim(), phone: f.phone.value });
    if (!r.ok) { toast(r.data.detail || "Не получилось"); return; }
    const cid = r.data.client.id, target = clientFor;
    await loadClients();
    $("pkClientDlg").close();
    if (target) await setClient(target.pid, cid); else fillNewClient(cid);
    toast(`Клиент «${r.data.client.name}» создан`);
  };
  // запись в журнал объекта из подборки
  let noteFor = null;
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-wnote], [data-nk], [data-color]");
    if (!b) return;
    if (b.dataset.nk) { $("pkNoteKinds").querySelectorAll("[data-nk]").forEach((x) => x.classList.toggle("on", x === b)); return; }
    if (b.dataset.color) {
      const p = list.find((x) => x.id === open);
      b.parentElement.querySelectorAll("[data-color]").forEach((x) => x.classList.toggle("on", x === b));
      call("/api/picks", { id: p.id, color: b.dataset.color }).then((r) => { if (r.ok) { p.color = b.dataset.color; renderList(); } });
      return;
    }
    const p = list.find((x) => x.id === Number(b.dataset.pid));
    const it = p.items.find((x) => x.listing_id === Number(b.dataset.wnote));
    noteFor = { pid: p.id, lid: it.listing_id };
    $("pkNoteFor").textContent = `${it.title}${p.client_name ? ` · клиент ${p.client_name}` : ""} — запись появится в журнале объекта${p.client_name ? " и в карточке клиента" : ""}.`;
    $("pkNoteText").value = "";
    $("pkNoteKinds").querySelectorAll("[data-nk]").forEach((x, i) => x.classList.toggle("on", !i));
    $("pkNoteDlg").showModal();
    setTimeout(() => $("pkNoteText").focus(), 50);
  });
  $("pkNoteSave").onclick = async () => {
    const kind = ($("pkNoteKinds").querySelector(".on") || {}).dataset?.nk || "note";
    const text = $("pkNoteText").value.trim();
    if (!text && kind === "note") { $("pkNoteText").focus(); return; }
    const r = await call("/api/worklog", { listing_id: noteFor.lid, pick_id: noteFor.pid, kind, text });
    if (!r.ok) { toast(r.data.detail || "Не сохранилось"); return; }
    $("pkNoteDlg").close();
    toast("Записано");
    await reload();
  };

  // контакт агента для клиента: сразу в этой подборке, «для всех» — в настройках и в подборках без контакта
  document.addEventListener("submit", async (e) => {
    const f = e.target.closest("[data-me]");
    if (!f) return;
    e.preventDefault();
    const name = f.name.value.trim(), phone = f.phone.value.trim();
    const ids = f.all.checked ? list.filter((x) => x.id === Number(f.dataset.me) || !x.contact_phone || !x.contact_name || x.contact_name === "Коллега (по коду)").map((x) => x.id)
      : [Number(f.dataset.me)];
    for (const id of ids) await call("/api/picks", { id, contact_name: name, contact_phone: phone });
    if (f.all.checked) await call("/api/settings", { pick_name: name, pick_phone: phone });
    toast(f.all.checked ? "Сохранено — так вы будете в новых подборках" : "Сохранено");
    await reload();
    if (f.dataset.then) {   // из окна «Ссылку пока нельзя отправить» — сразу копируем
      const d = $("pkNeedMe"); if (d && d.open) d.close();
      const p = list.find((x) => x.id === Number(f.dataset.me));
      if (p) { try { await navigator.clipboard.writeText(link(p)); toast("Сохранено. Ссылка скопирована — отправьте её клиенту", 3000); window.pickSent(link(p)); }
        catch { prompt("Скопируйте ссылку:", link(p)); } }
    }
  });
  document.addEventListener("change", async (e) => {
    const c = e.target;
    if (!c.dataset || !c.dataset.hideAddr) return;
    const r = await call("/api/picks", { id: Number(c.dataset.hideAddr), hide_address: c.checked });
    toast(r.ok ? (c.checked ? "Клиент не увидит номер дома" : "Клиент видит точный адрес") : "Не получилось");
    if (r.ok) { const p = list.find((x) => x.id === Number(c.dataset.hideAddr)); if (p) p.hide_address = c.checked ? 1 : 0; }
  });
  // снятый объект → похожие, замена на то же место
  document.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-sim], [data-swap]");
    if (!b) return;
    if (b.dataset.swap) {
      const [pid, oldId, newId] = b.dataset.swap.split(":").map(Number);
      const r = await call(`/api/picks/${pid}/items`, { replace: oldId, listing_id: newId });
      toast(r.ok ? "Заменили — новый объект на том же месте" : (r.data.detail || "Не получилось"));
      if (r.ok) { $("pkSimDlg").close(); await reload(); }
      return;
    }
    const pid = Number(b.dataset.pid), lid = Number(b.dataset.sim);
    const r = await call(`/api/picks/${pid}/similar?listing=${lid}`);
    const items = r.ok ? r.data.items : [];
    let d = $("pkSimDlg");
    if (!d) { d = document.createElement("dialog"); d.id = "pkSimDlg"; d.className = "pk2-dlg"; document.body.appendChild(d); }
    d.innerHTML = `<div class="pk2-dlg-in"><h2>Похожие на снятый объект</h2>
      <p class="pk2-hint">Те же комнаты, тот же ЖК или район, цена ±15 %. «Заменить» поставит объект на то же место в подборке.</p>
      ${items.length ? `<div class="pk2-sims">${items.map((f) => `<div class="pk2-simit">
          <span class="pk2-fph">${f.photos && f.photos[0] ? `<img src="${esc(f.photos[0])}" alt="">` : ic("home")}</span>
          <span><b>${esc(money(f.price, f.deal))}</b><small>${esc(f.complex ? `ЖК ${f.complex} · ${f.title}` : f.title)}</small>
            <a href="/?open=${f.id}">Открыть</a></span>
          <button type="button" class="pill" data-swap="${pid}:${lid}:${f.id}">Заменить</button></div>`).join("")}</div>`
        : `<p class="pk2-hint">Похожих сейчас нет на сайте.</p>`}
      <div class="pk2-dlg-acts"><span></span><button type="button" class="chip" data-pk-close>Закрыть</button></div></div>`;
    d.querySelector("[data-pk-close]").addEventListener("click", () => d.close());   // без onclick — так требует CSP
    d.showModal();
  });

  $("pkSeg").addEventListener("click", (e) => {
    const b = e.target.closest("[data-f]");
    if (!b) return;
    filter = b.dataset.f;
    $("pkSeg").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    renderList();
  });
  $("pkQ").addEventListener("input", (e) => { q = e.target.value; renderList(); });
  // фильтр по клиенту
  function fillClientFilter() {
    const sel = $("pkCl");
    if (!sel) return;
    const used = [...new Map(list.filter((p) => p.client_id).map((p) => [p.client_id, p.client_name])).entries()];
    sel.hidden = !used.length;
    sel.innerHTML = `<option value="">Все клиенты</option>` + used.map(([id, n]) => `<option value="${id}"${id === clientFilter ? " selected" : ""}>${esc(n || "Клиент")}</option>`).join("");
    if (window.Pick && used.length) Pick.select(sel, { searchPlaceholder: "Найти клиента" });
  }

  // статус объекта в подборке
  document.addEventListener("change", async (e) => {
    const sel = e.target;
    if (!sel.dataset || !sel.dataset.pst) return;
    const r = await call(`/api/picks/${sel.dataset.pid}/items`, { listing_id: Number(sel.dataset.pst), status: sel.value });
    toast(r.ok ? "Статус сохранён — запись в журнале объекта" : (r.data.detail || "Не получилось"), 2000);
    if (r.ok) await reload();
  });
  document.addEventListener("input", (e) => {
    if (e.target.id !== "pkBaseQ") return;
    baseQ = e.target.value;
    clearTimeout(baseTimer);
    baseTimer = setTimeout(async () => {
      const p = list.find((x) => x.id === open);
      if (!baseQ.trim()) { baseRes = []; if (p) $("pkBase").innerHTML = baseStrip(p); return; }
      baseRes = null;
      if (p) $("pkBase").innerHTML = baseStrip(p);
      const r = await call(`/api/listings?q=${encodeURIComponent(baseQ.trim())}&size=20`);
      baseRes = r.ok ? r.data.items : [];
      const p2 = list.find((x) => x.id === open);
      if (p2 && $("pkBase")) $("pkBase").innerHTML = baseStrip(p2);
    }, 350);
  });
  document.addEventListener("input", (e) => {
    if (e.target.id !== "pkFavQ") return;
    favQ = e.target.value;
    const p = list.find((x) => x.id === open);
    if (p) $("pkFavs").innerHTML = addStrip(p);
  });
  document.addEventListener("submit", async (e) => {
    const f = e.target;
    if (f.id === "pksNew") {
      e.preventDefault();
      // 24.10: подборка = клиент. Есть такой клиент — открываем его подборку, нет — заводим клиента и подборку разом
      const r = await call("/api/picks", { client_name: f.title.value.trim() });
      if (!r.ok) { toast(r.data.detail || "Не получилось"); return; }
      toast(r.data.pick.made_client ? `Завели клиента «${f.title.value.trim()}» и его подборку — пожелания можно дописать в «Клиентах»`
        : "У этого клиента уже есть подборка — открыли её", 4000);
      f.title.value = ""; open = r.data.pick.id; tab = "items"; addOpen = true; await reload(); loadSide(); return;
    }
    if (f.dataset.edit) {
      e.preventDefault();
      const r = await call("/api/picks", { id: Number(f.dataset.edit), title: f.title.value, note: f.note.value, client_title: f.client_title.value,
        contact_name: f.contact_name.value, contact_phone: f.contact_phone.value });
      if (!r.ok) { toast(r.data.detail || "Не получилось"); return; }
      toast("Сохранено"); await reload();
    }
  });
  // 17.10 (Артём): свои фото к объекту — браузер уменьшает до 1600 px, сервер кладёт в /photos/pk/
  function shrink(file) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => {
        const k = Math.min(1, 1600 / Math.max(img.width, img.height));
        const c = document.createElement("canvas");
        c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
        URL.revokeObjectURL(img.src);
        resolve(c.toDataURL("image/jpeg", 0.85));
      };
      img.onerror = () => reject(new Error("bad image"));
      img.src = URL.createObjectURL(file);
    });
  }
  document.addEventListener("change", async (e) => {
    const inp = e.target;
    if (!inp.dataset || !inp.dataset.phAdd) return;
    const files = [...inp.files].slice(0, 10);
    if (!files.length) return;
    const box = inp.closest(".pk2-own");
    box.classList.add("busy");
    toast(`Загружаем ${files.length} фото…`, 4000);
    let ok = 0;
    for (const f of files) {
      try {
        const r = await call(`/api/picks/${inp.dataset.pid}/items`, { listing_id: Number(inp.dataset.phAdd), photo: await shrink(f) });
        if (r.ok) ok += 1; else { toast(r.data.detail || "Фото не загрузилось", 3000); break; }
      } catch { toast("Не получилось прочитать фото — нужен JPEG или PNG", 3000); }
    }
    box.classList.remove("busy");
    if (ok) toast(`Добавлено фото: ${ok} — клиент увидит их первыми`, 2600);
    await reload();
  });
  document.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-ph-del]");
    if (!b) return;
    b.closest(".pk2-own-ph").classList.add("bye");
    const r = await call(`/api/picks/${b.dataset.pid}/items`, { listing_id: Number(b.dataset.lid), photo_del: b.dataset.phDel });
    toast(r.ok ? "Фото убрано" : r.data.detail || "Не получилось");
    await reload();
  });
  // 17.10 (Артём): своя цена для клиента / «Скрыть цену» — сохраняем сразу
  document.addEventListener("change", async (e) => {
    const inp = e.target;
    if (!inp.dataset || !(inp.dataset.pprice || inp.dataset.phide)) return;
    const pid = Number(inp.dataset.pid), lid = Number(inp.dataset.pprice || inp.dataset.phide);
    const body = inp.dataset.pprice ? { price: priceOut(inp.value, inp.dataset.deal) } : { hide_price: inp.checked };
    const r = await call(`/api/picks/${pid}/items`, { listing_id: lid, add: true, ...body });
    if (!r.ok) { toast(r.data.detail || "Не сохранилось"); return; }
    toast(inp.dataset.phide ? (inp.checked ? "Цена скрыта — клиент увидит «Цена по запросу»" : "Цена снова видна клиенту")
      : body.price ? "Цена для клиента сохранена" : "Цена — как в объявлении", 2000);
    await reload();
  });
  // комментарий к объекту — сохраняем, когда поле теряет фокус
  document.addEventListener("change", async (e) => {
    const inp = e.target;
    if (!inp.dataset || !inp.dataset.com) return;
    const r = await call(`/api/picks/${inp.dataset.pid}/items`, { listing_id: Number(inp.dataset.com), add: true, note: inp.value });
    if (r.ok) { const it = list.find((x) => x.id === Number(inp.dataset.pid)).items.find((x) => x.listing_id === Number(inp.dataset.com)); it.note = inp.value; }
    toast(r.ok ? "Комментарий сохранён" : r.data.detail || "Не сохранилось", 1500);
  });

  // скрыть фото объявления от клиента / вернуть
  document.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-ph-hide], [data-ph-unhide]");
    if (!b) return;
    const hide = b.hasAttribute("data-ph-hide");
    const r = await call(`/api/picks/${b.dataset.pid}/items`, { listing_id: Number(b.dataset.lid),
      [hide ? "photo_hide" : "photo_unhide"]: hide ? b.dataset.phHide : b.dataset.phUnhide });
    toast(r.ok ? (hide ? "Фото скрыто от клиента" : "Фото снова видно клиенту") : r.data.detail || "Не получилось", 1800);
    if (r.ok) await reload();
  });
  // ─── перетаскивание фото внутри объекта: мышь — после сдвига 6 px, палец — после удержания 0,3 с ───
  // плитка едет за пальцем, соседние плавно расступаются (FLIP); отпустили — порядок сохраняется
  let ph = null;
  const phEnd = async (save) => {
    const d = ph; ph = null;
    if (!d) return;
    clearTimeout(d.timer);
    if (!d.on) return;
    d.t.classList.remove("pk2-pt-drag"); d.strip.classList.remove("sorting");
    d.t.style.transform = ""; d.t.style.zIndex = "";
    if (!save) return;
    const order = [...d.strip.querySelectorAll(".pk2-pt")].map((x) => x.dataset.u);
    if (order.join("|") === d.before) return;
    const box = d.strip.closest("[data-phs]");
    const r = await call(`/api/picks/${box.dataset.pid}/items`, { listing_id: Number(box.dataset.phs), photo_order: order });
    toast(r.ok ? "Порядок фото сохранён" : r.data.detail || "Не сохранилось", 1500);
    await reload();
  };
  const phBegin = () => {
    if (!ph) return;
    ph.on = true;
    try { ph.t.setPointerCapture(ph.id); } catch { /* */ }
    ph.t.classList.add("pk2-pt-drag"); ph.strip.classList.add("sorting");
    ph.t.style.zIndex = "5";
    if (navigator.vibrate && ph.touch) navigator.vibrate(8);
  };
  document.addEventListener("pointerdown", (e) => {
    const t = e.target.closest(".pk2-pt");
    if (!t || e.target.closest("button") || e.button > 0) return;
    const strip = t.parentElement;
    ph = { t, strip, id: e.pointerId, x: e.clientX, y: e.clientY, on: false, touch: e.pointerType !== "mouse",
      before: [...strip.querySelectorAll(".pk2-pt")].map((x) => x.dataset.u).join("|"), timer: 0 };
    if (ph.touch) ph.timer = setTimeout(phBegin, 300);
  });
  document.addEventListener("pointermove", (e) => {
    if (!ph || e.pointerId !== ph.id) return;
    if (!ph.on) {
      const dist = Math.hypot(e.clientX - ph.x, e.clientY - ph.y);
      if (ph.touch) { if (dist > 10) phEnd(false); return; }   // палец поехал раньше — это прокрутка страницы
      if (dist < 6) return;
      phBegin();
    }
    e.preventDefault();
    const t = ph.t;
    t.style.transform = "";
    t.style.pointerEvents = "none";
    const over = document.elementFromPoint(e.clientX, e.clientY);
    t.style.pointerEvents = "";
    const o = over && over.closest(".pk2-pt");
    if (o && o !== t && o.parentElement === ph.strip) {
      const tiles = [...ph.strip.querySelectorAll(".pk2-pt")];
      const was = new Map(tiles.map((x) => [x, x.getBoundingClientRect()]));
      const r = o.getBoundingClientRect();
      ph.strip.insertBefore(t, e.clientX > r.left + r.width / 2 ? o.nextSibling : o);
      tiles.forEach((x) => {
        if (x === t) return;
        const a = was.get(x), b = x.getBoundingClientRect();
        if (a.left !== b.left || a.top !== b.top) {
          x.animate([{ transform: `translate(${a.left - b.left}px, ${a.top - b.top}px)` }, { transform: "none" }],
            { duration: 220, easing: "cubic-bezier(.22,1,.36,1)" });
        }
      });
    }
    // плитка — под пальцем: центр плитки там, где палец (от её нового места в ряду)
    const home = t.getBoundingClientRect();
    t.style.transform = `translate(${e.clientX - (home.left + home.width / 2)}px, ${e.clientY - (home.top + home.height / 2)}px) scale(1.08)`;
  }, { passive: false });
  document.addEventListener("pointerup", (e) => { if (ph && e.pointerId === ph.id) phEnd(true); });
  document.addEventListener("pointercancel", (e) => { if (ph && e.pointerId === ph.id) phEnd(false); });
  document.addEventListener("touchmove", (e) => { if (ph && ph.on) e.preventDefault(); }, { passive: false });   // не листать страницу, пока тянем
  document.addEventListener("contextmenu", (e) => { if (e.target.closest(".pk2-pt")) e.preventDefault(); });

  // перетаскивание карточек (мышь; на телефоне — стрелки)
  let drag = null;
  document.addEventListener("mousedown", (e) => { gripDown = !!(e.target.closest && e.target.closest(".pk5-grip, .pk5-line")) && !(e.target.closest && e.target.closest("a, button, input")); });
  document.addEventListener("dragstart", (e) => {
    if (e.target.closest && e.target.closest(".pk2-own")) { e.preventDefault(); return; }   // фото тянем своим способом
    if (e.target.closest && e.target.closest(".pk5-row") && !gripDown) { e.preventDefault(); return; }   // 24.10: строку — только за саму строку, не за поля
    const card = e.target.closest && e.target.closest(".pk2-obj");
    if (!card || e.target.closest("input")) return;
    drag = card; card.classList.add("drag");
    e.dataTransfer.effectAllowed = "move";
    try { e.dataTransfer.setData("text/plain", card.dataset.lid); } catch { /* */ }
  });
  document.addEventListener("dragover", (e) => {
    if (!drag) return;
    const over = e.target.closest && e.target.closest(".pk2-obj");
    if (!over || over === drag || over.parentNode !== drag.parentNode) return;
    e.preventDefault();
    const r = over.getBoundingClientRect();
    const after = (e.clientY - r.top) / r.height > .5 || ((e.clientX - r.left) / r.width > .5 && e.clientY < r.bottom);
    over.parentNode.insertBefore(drag, after ? over.nextSibling : over);
  });
  document.addEventListener("dragend", () => {
    if (!drag) return;
    const card = drag; drag = null; card.classList.remove("drag");
    const ids = [...card.parentNode.querySelectorAll(".pk2-obj")].map((x) => Number(x.dataset.lid));
    const pid = Number(card.dataset.pid);
    const p = list.find((x) => x.id === pid);
    if (ids.join() !== p.items.map((x) => x.listing_id).join()) setOrder(pid, ids);
  });

  load().then(() => { if (location.search) history.replaceState(null, "", location.pathname); });
})();
