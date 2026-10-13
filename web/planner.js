/* Планер: большой календарь месяца (события плашками в днях, перетаскивание на другой день), справа — расписание
   выбранного дня; цвет у каждого события, фильтр по видам, поиск, счётчики месяца; свои заметки.
   API: /api/planner (обзор; ?from&to — события и счётчики периода; POST событие / {id, done} / {id, ts}; DELETE {id}),
   /api/planner/notes. Ссылки: ?add=show&listing=ID[&client=ID] — новое событие; ?event=ID — открыть событие; ?tab=notes. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const ic = (n) => (window.ic ? window.ic(n) : "");
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const KINDS = { show: "Показ", call: "Созвон", meet: "Встреча", task: "Задача" };
  const KIC = { show: "home", call: "phone", meet: "users", task: "check" };
  const KFORMS = { show: ["показ", "показа", "показов"], call: ["созвон", "созвона", "созвонов"],
    meet: ["встреча", "встречи", "встреч"], task: ["задача", "задачи", "задач"] };
  const KCOLOR = { show: "gold", call: "sky", meet: "violet", task: "lime" };   // цвет по умолчанию — по виду события
  const COLORS = { gold: "Золото", teal: "Бирюза", sky: "Небо", violet: "Сирень", coral: "Коралл", rose: "Роза", lime: "Лайм", slate: "Графит" };
  const STATUSES = { call: "Звонил", show: "Показ", think: "Думает", refuse: "Отказ" };
  const MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];
  const MONTHS_G = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
  let data = null, monthEvents = [], stats = null, editing = null, kind = "show", color = "", colorTouched = false;
  let month = new Date(); month.setDate(1); month.setHours(0, 0, 0, 0);
  const pad = (n) => String(n).padStart(2, "0");
  const dayKey = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  let selDay = dayKey(new Date());
  let hidden = new Set();
  try { hidden = new Set(JSON.parse(localStorage.getItem("plHidden") || "[]")); } catch { /* */ }
  let q = "";
  const objects = new Map();   // объекты, которые видели (избранное + найденные поиском)

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
  const hm = (ts) => new Date(ts * 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  const dur = (m) => (m < 60 ? `${m} мин` : m % 60 ? `${Math.floor(m / 60)} ч ${m % 60} мин` : `${m / 60} ч`);
  const money = (p, deal) => (!p ? "" : deal === "rent" ? `${Math.round(p / 1000).toLocaleString("ru-RU")} тыс. ₽/мес`
    : p >= 1e6 ? `${(p / 1e6).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} млн ₽` : `${p.toLocaleString("ru-RU")} ₽`);
  const place = (l) => [l.complex && `ЖК ${l.complex}`, l.street && `ул. ${l.street}${l.house ? ", " + l.house : ""}`, l.district]
    .filter(Boolean).join(" · ");
  const colorOf = (e) => e.color || KCOLOR[e.kind] || "gold";
  const fromKey = (k) => new Date(`${k}T00:00`);
  function dayName(k) {
    const d = fromKey(k), t = new Date(); t.setHours(0, 0, 0, 0);
    const diff = Math.round((d - t) / 86400000);
    const s = `${d.getDate()} ${MONTHS_G[d.getMonth()]}, ${d.toLocaleDateString("ru-RU", { weekday: "long" })}`;
    return diff === 0 ? `Сегодня · ${s}` : diff === 1 ? `Завтра · ${s}` : diff === -1 ? `Вчера · ${s}` : s;
  }
  const matches = (e) => {
    if (hidden.has(e.kind)) return false;
    if (!q) return true;
    const hay = [e.title, e.contact_name, e.contact_phone, e.place, e.note, e.listing && e.listing.title,
      e.listing && e.listing.complex].filter(Boolean).join(" ").toLowerCase();
    return q.toLowerCase().split(/\s+/).every((w) => hay.includes(w));
  };

  // ─── загрузка ───
  function gridRange() {
    const start = new Date(month); start.setDate(1 - ((month.getDay() + 6) % 7));
    const end = new Date(start); end.setDate(start.getDate() + 42);
    return [start, end];
  }
  async function loadMonth() {
    const [a, b] = gridRange();
    const r = await call(`/api/planner?from=${Math.floor(a / 1000)}&to=${Math.floor(b / 1000)}`);
    monthEvents = r.ok ? r.data.events : [];
    const m0 = Math.floor(month / 1000), m1 = Math.floor(new Date(month.getFullYear(), month.getMonth() + 1, 1) / 1000);
    stats = { total: 0, done: 0, by_kind: {} };
    for (const e of monthEvents) {
      if (e.ts < m0 || e.ts >= m1) continue;
      stats.total++; stats.done += e.done ? 1 : 0; stats.by_kind[e.kind] = (stats.by_kind[e.kind] || 0) + 1;
    }
    monthEvents.forEach((e) => { if (e.listing) objects.set(e.listing.id, e.listing); });
  }
  async function load() {
    const r = await call("/api/planner");
    if (r.status === 401) { $("plGuest").hidden = false; return false; }
    data = r.data;
    (data.favorites || []).forEach((l) => objects.set(l.id, l));
    $("plApp").hidden = false;
    await loadMonth();
    renderAll();
    greet();
    return true;
  }
  async function greet() {
    let name = "";
    try { const m = await call("/api/me"); name = (m.data.me && (m.data.me.name || "").split(" ")[0]) || ""; } catch { /* */ }
    const h = new Date().getHours();
    const hello = h < 5 ? "Доброй ночи" : h < 12 ? "Доброе утро" : h < 18 ? "Добрый день" : "Добрый вечер";
    $("pvHello").textContent = `${hello}${name ? ", " + name : ""}!`;
    const today = dayKey(new Date()), now = Date.now() / 1000;
    const list = monthEvents.filter((e) => dayKey(new Date(e.ts * 1000)) === today && !e.done);
    const next = list.find((e) => e.ts + (e.dur_min || 60) * 60 > now);
    $("pvSub").textContent = !list.length ? "Сегодня свободно — самое время запланировать показы."
      : `Сегодня ${list.length} ${plural(list.length, "дело", "дела", "дел")}${next ? ` · ближайшее в ${hm(next.ts)} — ${next.title}` : ""}`;
  }
  const plural = (n, one, few, many) => {
    const m10 = n % 10, m100 = n % 100;
    return m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many;
  };

  // ─── шапка календаря: месяц/год, счётчики, фильтр видов ───
  function renderHead() {
    const ms = $("pvMonth"), ys = $("pvYear");
    ms.innerHTML = MONTHS.map((m, i) => `<option value="${i}"${i === month.getMonth() ? " selected" : ""}>${m[0].toUpperCase() + m.slice(1)}</option>`).join("");
    const y0 = new Date().getFullYear();
    ys.innerHTML = [y0 - 1, y0, y0 + 1, y0 + 2].map((y) => `<option value="${y}"${y === month.getFullYear() ? " selected" : ""}>${y}</option>`).join("");
    const s = stats || { total: 0, done: 0, by_kind: {} };
    $("pvStats").innerHTML = s.total
      ? `<span><b>${s.total}</b> ${plural(s.total, "событие", "события", "событий")}</span>`
        + Object.keys(KINDS).filter((k) => s.by_kind[k]).map((k) => `<span class="c-${KCOLOR[k]}"><i></i>${s.by_kind[k]} ${plural(s.by_kind[k], ...KFORMS[k])}</span>`).join("")
        + (s.done ? `<span class="ok">${ic("check")}${s.done} сделано</span>` : "")
      : `<span class="muted">В этом месяце пока пусто</span>`;
    $("pvKinds").innerHTML = Object.entries(KINDS).map(([k, t]) => `<button type="button" class="pv-k c-${KCOLOR[k]}${hidden.has(k) ? " off" : ""}"
      data-kind-toggle="${k}" aria-pressed="${!hidden.has(k)}"><i></i>${t}</button>`).join("");
  }

  // ─── сетка месяца ───
  function pill(e) {
    return `<button type="button" class="pv-pill c-${colorOf(e)}${e.done ? " done" : ""}" data-ev="${e.id}" draggable="true"
      title="${esc(hm(e.ts) + " · " + e.title)}"><i></i><span><b>${hm(e.ts)}</b> ${esc(e.title)}</span></button>`;
  }
  function renderGrid() {
    const [start] = gridRange();
    const today = dayKey(new Date());
    const byDay = {};
    for (const e of monthEvents) if (matches(e)) (byDay[dayKey(new Date(e.ts * 1000))] ||= []).push(e);
    let html = "";
    for (let i = 0; i < 42; i++) {
      const d = new Date(start); d.setDate(start.getDate() + i);
      const k = dayKey(d), list = (byDay[k] || []).sort((a, b) => a.ts - b.ts);
      const cls = ["pv-cell", d.getMonth() !== month.getMonth() ? "out" : "", k === today ? "today" : "", k === selDay ? "on" : "",
        i % 7 >= 5 ? "we" : "", list.length ? "has" : "", d < fromKey(today) ? "past" : ""].filter(Boolean).join(" ");
      html += `<div class="${cls}" data-day="${k}" role="button" tabindex="0" aria-label="${esc(dayName(k))}${list.length ? `, событий: ${list.length}` : ""}">
        <span class="pv-num">${d.getDate()}</span>
        <div class="pv-pills">${list.slice(0, 3).map(pill).join("")}${list.length > 3 ? `<span class="pv-more">ещё ${list.length - 3}</span>` : ""}</div>
        <span class="pv-dots">${list.slice(0, 5).map((e) => `<i class="c-${colorOf(e)}"></i>`).join("")}</span>
        <button type="button" class="pv-plus" data-add-day="${k}" aria-label="Добавить в этот день" tabindex="-1">${ic("plus")}</button></div>`;
    }
    $("plGrid").innerHTML = html;
  }

  // ─── расписание дня (справа) ───
  function card(e, withDate) {
    const l = e.listing;
    const end = e.ts + (e.dur_min || 60) * 60;
    const phone = e.contact_phone || "";
    const sub = [KINDS[e.kind], l ? (l.complex ? `ЖК ${l.complex}` : l.title) : "", !l && e.place ? e.place : ""].filter(Boolean).join(" · ");
    return `<article class="pv-ev c-${colorOf(e)}${e.done ? " done" : ""}" data-ev-open="${e.id}" draggable="true" data-ev="${e.id}">
      <span class="pv-ev-bar"></span>
      <div class="pv-ev-top">
        <h3>${esc(e.title)}</h3>
        <button type="button" class="pv-check${e.done ? " on" : ""}" data-done="${e.id}" aria-label="${e.done ? "Вернуть в планы" : "Отметить сделанным"}"
          title="${e.done ? "Вернуть в планы" : "Сделано"}">${ic("check")}</button>
      </div>
      ${sub ? `<p class="pv-ev-sub">${esc(sub)}</p>` : ""}
      <div class="pv-ev-meta"><span>${ic("clock")}${withDate ? `${new Date(e.ts * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "short" })}, ` : ""}${hm(e.ts)} – ${hm(end)}</span>
        <span>${dur(e.dur_min || 60)}${e.remind_min && !e.done ? ` · ${ic("bell")}` : ""}</span></div>
      ${l ? `<a class="pv-obj" href="/?open=${l.id}">${l.photo ? `<img src="${esc(l.photo)}" alt="" loading="lazy">` : `<span class="pv-obj-ph">${ic("home")}</span>`}
        <span><b>${esc(money(l.price, l.deal) || l.title)}</b><small>${esc(place(l) || l.title)}${l.is_active ? "" : " · снят с сайта"}</small></span></a>` : ""}
      ${e.contact_name || phone ? `<div class="pv-contact"><span class="pv-ava">${esc((e.contact_name || "?").trim()[0] || "?").toUpperCase()}</span>
        <span class="pv-cname">${esc(e.contact_name || phone)}</span>
        ${phone ? `<a class="pv-mini" href="tel:${esc(phone)}" aria-label="Позвонить">${ic("phone")}</a><button type="button" class="pv-mini max" data-max="${esc(phone)}">MAX</button>` : ""}</div>` : ""}
      ${e.note ? `<p class="pv-ev-note">${esc(e.note)}</p>` : ""}
    </article>`;
  }
  function renderDay() {
    const list = $("plAgenda");
    $("pvDayAdd").dataset.addDay = selDay;
    if (q) {   // поиск — все найденные события месяца
      const found = monthEvents.filter(matches).sort((a, b) => a.ts - b.ts);
      $("pvDayTitle").textContent = `Найдено: ${found.length}`;
      $("pvDayName").textContent = `«${q}» · ${MONTHS[month.getMonth()]}`;
      list.innerHTML = found.map((e) => card(e, true)).join("") || `<div class="pv-empty">${ic("search")}<b>Ничего не нашлось</b>Попробуйте другое слово или другой месяц.</div>`;
      return;
    }
    $("pvDayTitle").textContent = "Запланировано";
    $("pvDayName").textContent = dayName(selDay);
    const today = dayKey(new Date());
    const items = monthEvents.filter((e) => dayKey(new Date(e.ts * 1000)) === selDay && matches(e)).sort((a, b) => a.ts - b.ts);
    let html = "";
    if (selDay === today && data.overdue.length) {
      html += `<details class="pv-overdue"><summary>${ic("alert")}Просрочено · ${data.overdue.length}</summary>${data.overdue.map((e) => card(e, true)).join("")}</details>`;
    }
    const now = Date.now() / 1000;
    let lastHour = "", nowShown = selDay !== today;
    for (const e of items) {
      if (!nowShown && e.ts > now) { html += `<div class="pv-now"><span>Сейчас ${hm(now)}</span></div>`; nowShown = true; }
      const h = `${pad(new Date(e.ts * 1000).getHours())}:00`;
      if (h !== lastHour) { html += `<div class="pv-hour">${h}</div>`; lastHour = h; }
      html += card(e);
    }
    if (items.length && !nowShown) html += `<div class="pv-now"><span>Сейчас ${hm(now)}</span></div>`;
    if (!items.length) {
      html += `<div class="pv-empty">${ic("calendar")}<b>${selDay < today ? "В этот день ничего не было" : "Свободный день"}</b>
        ${selDay >= today ? `Запланируйте показ или созвон:<div class="pv-empty-acts">
          <button type="button" class="chip" data-add="show" data-on-day="${selDay}">${ic("home")}Показ</button>
          <button type="button" class="chip" data-add="call" data-on-day="${selDay}">${ic("phone")}Созвон</button>
          <button type="button" class="chip" data-add="task" data-on-day="${selDay}">${ic("check")}Задача</button></div>` : ""}</div>`;
    }
    list.innerHTML = html;
  }
  function renderAll() { renderHead(); renderGrid(); renderDay(); }

  async function goMonth(d) {
    month = new Date(d.getFullYear(), d.getMonth(), 1);
    $("plGrid").classList.add("fade");
    await loadMonth();
    renderAll();
    requestAnimationFrame(() => $("plGrid").classList.remove("fade"));
  }
  async function pickDay(k, scroll) {
    selDay = k;
    const d = fromKey(k);
    if (d.getMonth() !== month.getMonth() || d.getFullYear() !== month.getFullYear()) await goMonth(d);
    else { renderGrid(); renderDay(); }
    if (scroll && innerWidth < 900) $("pvDay").scrollIntoView({ behavior: "smooth", block: "start" });
  }
  const allEvents = () => [...data.overdue, ...data.upcoming, ...monthEvents];
  const findEv = (id) => allEvents().find((x) => x.id === id);

  // ─── перетаскивание события на другой день ───
  let dragId = 0;
  document.addEventListener("dragstart", (e) => {
    const t = e.target.closest && e.target.closest("[data-ev]");
    if (!t) return;
    dragId = Number(t.dataset.ev);
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", String(dragId));
    document.body.classList.add("pv-dragging");
  });
  document.addEventListener("dragend", () => { dragId = 0; document.body.classList.remove("pv-dragging"); document.querySelectorAll(".pv-cell.drop").forEach((c) => c.classList.remove("drop")); });
  document.addEventListener("dragover", (e) => {
    const c = dragId && e.target.closest(".pv-cell");
    if (!c) return;
    e.preventDefault();
    document.querySelectorAll(".pv-cell.drop").forEach((x) => x !== c && x.classList.remove("drop"));
    c.classList.add("drop");
  });
  document.addEventListener("drop", async (e) => {
    const c = dragId && e.target.closest(".pv-cell");
    if (!c) return;
    e.preventDefault();
    const ev = findEv(dragId);
    if (!ev) return;
    const old = new Date(ev.ts * 1000), nd = fromKey(c.dataset.day);
    nd.setHours(old.getHours(), old.getMinutes(), 0, 0);
    const ts = Math.floor(nd / 1000);
    if (ts === ev.ts) return;
    const r = await call("/api/planner", { id: ev.id, ts });
    if (!r.ok) { toast(r.data.detail || "Не получилось перенести"); return; }
    toast(`Перенесено на ${nd.getDate()} ${MONTHS_G[nd.getMonth()]}, ${hm(ts)}`);
    await reload(c.dataset.day);
  });

  // ─── окно события ───
  function fillListings(selected) {
    const fav = data.favorites || [];
    const sel = $("evListing");
    const extra = selected && !fav.some((l) => l.id === selected) && objects.get(selected) ? [objects.get(selected)] : [];
    sel.innerHTML = `<option value="">Без объекта</option>` + [...extra, ...fav].map((l) =>
      `<option value="${l.id}"${l.id === selected ? " selected" : ""}>${esc(l.complex ? `ЖК ${l.complex}` : l.title)}${l.price ? " · " + esc(money(l.price, l.deal)) : ""}${l.status ? " · " + STATUSES[l.status] : ""}</option>`).join("");
    showObj();
  }
  function fillClients(selected) {
    const list = data.clients || [];
    $("evClient").innerHTML = `<option value="">Без клиента</option>` + list.map((c) =>
      `<option value="${c.id}"${c.id === selected ? " selected" : ""}>${esc(c.name)}${c.phone ? " · " + esc(c.phone) : ""}</option>`).join("");
  }
  let autoContact = { name: "", phone: "" };   // что подставили из клиента — меняем, только если человек сам не правил
  function pickClient(id) {
    const c = (data.clients || []).find((x) => x.id === id);
    const f = $("evForm");
    if (!c) return;
    if (!f.contact_name.value || f.contact_name.value === autoContact.name) f.contact_name.value = c.name || "";
    if (!f.contact_phone.value || f.contact_phone.value === autoContact.phone) f.contact_phone.value = c.phone || "";
    autoContact = { name: c.name || "", phone: c.phone || "" };
    if (!f.title.value) f.title.value = `${KINDS[kind]}: ${c.name}`;
  }
  function showObj() {
    const l = objects.get(Number($("evListing").value));
    $("evObj").innerHTML = l ? `<a class="pv-obj" href="/?open=${l.id}">${l.photo ? `<img src="${esc(l.photo)}" alt="">` : `<span class="pv-obj-ph">${ic("home")}</span>`}
      <span><b>${esc(l.title)}</b><small>${esc(money(l.price, l.deal))}${place(l) ? " · " + esc(place(l)) : ""}</small></span></a>` : "";
  }
  async function findObjects(text) {   // поиск любого объекта на 1+1 — для окна события
    const [a, b] = await Promise.all(["sale", "rent"].map((deal) =>
      call(`/api/listings?deal=${deal}&size=8&q=${encodeURIComponent(text)}`)));
    const items = [...(a.ok ? a.data.items : []), ...(b.ok ? b.data.items : [])].slice(0, 12);
    items.forEach((l) => objects.set(l.id, { ...l, photo: (l.photos || [])[0] }));
    return items.map((l) => ({ value: l.id, label: `${l.complex ? `ЖК ${l.complex}` : l.title} · ${money(l.price, l.deal)}` }));
  }
  function renderKinds() {
    $("evKinds").innerHTML = Object.entries(KINDS).map(([k, v]) => `<button type="button" role="radio" aria-checked="${k === kind}"
      class="ev-kind c-${KCOLOR[k]}${k === kind ? " on" : ""}" data-kind="${k}">${ic(KIC[k])}<span>${esc(v)}</span></button>`).join("");
  }
  function renderColors() {
    const cur = color || KCOLOR[kind];
    $("evColors").innerHTML = Object.entries(COLORS).map(([c, t]) => `<button type="button" role="radio" aria-checked="${c === cur}"
      class="ev-color c-${c}${c === cur ? " on" : ""}" data-color-pick="${c}" title="${t}" aria-label="${t}">${ic("check")}</button>`).join("");
  }
  function upgradeForm() {
    const f = $("evForm");
    if (!window.Pick || f.dataset.up) return;
    f.dataset.up = "1";
    Pick.date(f.date); Pick.time(f.time, { from: 7, to: 22 });
    Pick.chips(f.dur_min); Pick.chips(f.remind_min);
    Pick.select($("evListing"), { remote: findObjects, remoteTitle: "Объекты на 1+1", searchPlaceholder: "Избранное или любой: ЖК, улица, «2к фмр»" });
    Pick.select($("evClient"), { searchPlaceholder: "Найти клиента по имени или телефону" });
  }
  const CLIENT_ID = Number(new URLSearchParams(location.search).get("client")) || null;

  async function openEvent(ev, preset = {}) {
    editing = ev || null;
    const f = $("evForm");
    f.reset();
    $("evErr").hidden = true;
    kind = ev ? ev.kind : preset.kind || "show";
    color = ev ? ev.color || "" : "";
    colorTouched = !!(ev && ev.color);
    renderKinds(); renderColors();
    $("evTitle").textContent = ev ? "Событие" : "Новое событие";
    $("evDel").hidden = !ev;
    const src = ev || preset.clone || null;   // правим событие или делаем его копию
    const base = ev ? new Date(ev.ts * 1000) : preset.clone ? (() => {
      const d = new Date(preset.clone.ts * 1000); d.setDate(d.getDate() + 1); return d;
    })() : (() => {
      const d = preset.day ? new Date(`${preset.day}T10:00`) : new Date(Date.now() + 3600 * 1000);
      if (!preset.day) d.setMinutes(0, 0, 0);
      return d;
    })();
    f.date.value = dayKey(base);
    f.time.value = `${pad(base.getHours())}:${pad(base.getMinutes())}`;
    if (preset.clone) {
      kind = preset.clone.kind; color = preset.clone.color || ""; colorTouched = !!preset.clone.color;
      renderKinds(); renderColors();
      $("evTitle").textContent = "Копия события";
    }
    if (src) {
      for (const k of ["title", "contact_name", "contact_phone", "place", "note"]) f[k].value = src[k] || "";
      f.dur_min.value = String(src.dur_min || 60);
      f.remind_min.value = String(src.remind_min ?? 60);
    }
    let lid = src ? src.listing_id : preset.listing || null;
    if (lid && !objects.has(lid)) {
      const r = await call(`/api/listings/${lid}`);
      if (r.ok) objects.set(lid, { ...r.data, photo: (r.data.photos || [])[0] }); else lid = null;
    }
    fillListings(lid);
    autoContact = { name: "", phone: "" };
    const cid = src ? src.client_id : preset.client || CLIENT_ID || null;
    fillClients(cid);
    if (!ev && cid) pickClient(cid);
    if (!ev && lid) await prefillFromListing(lid);
    upgradeForm();
    if (window.Pick) Pick.refresh(f);
    const dlg = $("evDlg");
    if (!dlg.open) dlg.showModal();
  }
  function viewEvent(e) {
    if (!e) return;
    const l = e.listing, cl = e.client;
    const end = e.ts + (e.dur_min || 60) * 60;
    const d = new Date(e.ts * 1000);
    const phone = e.contact_phone || (cl && cl.phone) || "";
    const when = `${d.toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" })}, ${hm(e.ts)} – ${hm(end)}`;
    const remind = e.remind_min ? (e.remind_min >= 1440 ? "за день" : e.remind_min >= 60 ? `за ${e.remind_min / 60} ч` : `за ${e.remind_min} мин`) : "";
    $("evvBody").innerHTML = `
      <div class="evv c-${colorOf(e)}${e.done ? " done" : ""}">
        <span class="evv-bar"></span>
        <div class="evv-head"><span class="evv-kind">${ic(KIC[e.kind])}${KINDS[e.kind]}</span>
          <button type="button" class="close" data-close aria-label="Закрыть">${ic("x")}</button></div>
        <h2 id="evvTitle">${esc(e.title)}</h2>
        <p class="evv-when">${ic("clock")}${esc(when[0].toUpperCase() + when.slice(1))} · ${dur(e.dur_min || 60)}${remind && !e.done ? ` · ${ic("bell")}напомним ${remind}` : ""}</p>
        ${e.done ? `<p class="evv-done">${ic("check-circle")}Сделано</p>` : ""}
        ${cl ? `<a class="evv-row" href="/clients?id=${cl.id}"><span class="evv-ava">${esc((cl.name || "?")[0].toUpperCase())}</span>
          <span><small>Клиент</small><b>${esc(cl.name)}</b></span>${ic("chevron")}</a>` : ""}
        ${l ? `<a class="evv-row" href="/?open=${l.id}">${l.photo ? `<img src="${esc(l.photo)}" alt="">` : `<span class="evv-ava">${ic("home")}</span>`}
          <span><small>Объект</small><b>${esc(l.complex ? `ЖК ${l.complex}` : l.title)}</b><em>${esc(money(l.price, l.deal))}${place(l) ? " · " + esc(place(l)) : ""}</em></span>${ic("chevron")}</a>` : ""}
        ${e.contact_name || phone ? `<div class="evv-row static"><span class="evv-ava">${ic("user")}</span>
          <span><small>Контакт</small><b>${esc(e.contact_name || phone)}</b>${e.contact_name && phone ? `<em>${esc(phone)}</em>` : ""}</span>
          ${phone ? `<a class="pv-mini" href="tel:${esc(phone)}" aria-label="Позвонить">${ic("phone")}</a><button type="button" class="pv-mini" data-max="${esc(phone)}">MAX</button>` : ""}</div>` : ""}
        ${e.place ? `<a class="evv-row" href="https://yandex.ru/maps/35/krasnodar/?text=${encodeURIComponent(e.place)}" target="_blank" rel="noopener">
          <span class="evv-ava">${ic("pin")}</span><span><small>Где</small><b>${esc(e.place)}</b></span>${ic("external")}</a>` : ""}
        ${e.note ? `<div class="evv-note">${esc(e.note)}</div>` : ""}
        <div class="evv-acts">
          <button type="button" class="pill" data-edit="${e.id}">${ic("edit")}Изменить</button>
          <button type="button" class="pill light" data-clone="${e.id}">${ic("plus-circle")}Копировать</button>
          <button type="button" class="pill light" data-view-done="${e.id}">${ic("check")}${e.done ? "Вернуть в планы" : "Сделано"}</button>
          <a class="pill light" href="/api/planner/${e.id}.ics">${ic("calendar")}В календарь телефона</a>
          <button type="button" class="chip danger" data-view-del="${e.id}">${ic("trash")}Удалить</button>
        </div>
      </div>`;
    const dlg = $("evView");
    if (!dlg.open) dlg.showModal();
  }
  async function prefillFromListing(id) {
    const f = $("evForm");
    const l = objects.get(id);
    if (!l) return;
    if (!f.title.value) f.title.value = `${KINDS[kind]}: ${l.complex ? `ЖК ${l.complex}` : l.title}`;
    if (!f.place.value) f.place.value = place(l);
    if (!f.contact_phone.value) {
      const r = await call(`/api/listings/${id}`);
      if (r.ok && r.data.phones && r.data.phones.length) f.contact_phone.value = r.data.phones[0];
    }
  }
  async function saveEvent(e) {
    e.preventDefault();
    const f = $("evForm");
    const ts = Math.floor(new Date(`${f.date.value}T${f.time.value || "10:00"}`).getTime() / 1000);
    const body = { kind, title: f.title.value, ts, dur_min: Number(f.dur_min.value), listing_id: Number(f.listing_id.value) || null,
      contact_name: f.contact_name.value, contact_phone: f.contact_phone.value, place: f.place.value, note: f.note.value,
      remind_min: Number(f.remind_min.value), done: editing ? editing.done : 0, color: colorTouched ? color : "",
      client_id: Number($("evClient").value) || null };
    if (editing) body.id = editing.id;
    const r = await call("/api/planner", body);
    if (!r.ok) { $("evErr").textContent = r.data.detail || "Не получилось сохранить."; $("evErr").hidden = false; return; }
    $("evDlg").close();
    toast(editing ? "Сохранено" : `Запланировано на ${new Date(ts * 1000).toLocaleString("ru-RU", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" })}`, 3000);
    await reload(dayKey(new Date(ts * 1000)));
  }
  async function reload(day) {
    const r = await call("/api/planner");
    if (r.ok) { data = r.data; (data.favorites || []).forEach((l) => objects.set(l.id, l)); }
    if (day) { selDay = day; const d = fromKey(day); month = new Date(d.getFullYear(), d.getMonth(), 1); }
    await loadMonth();
    renderAll(); renderNotes(); greet();
  }

  // ─── заметки ───
  function renderNotes() {
    $("plNoteList").innerHTML = data.notes.map((n) => `<div class="pl-n${n.pinned ? " pinned" : ""} c-${n.color || "none"}" data-note="${n.id}">
      <textarea rows="4" maxlength="5000" data-note-text="${n.id}">${esc(n.text)}</textarea>
      <div class="pl-n-acts">
        <button type="button" class="pl-n-pin${n.pinned ? " on" : ""}" data-pin="${n.id}" title="${n.pinned ? "Открепить" : "Закрепить сверху"}">${ic("pushpin")}</button>
        ${["", "yellow", "green", "red", "blue"].map((c) => `<button type="button" class="pl-n-color c-${c || "none"}${(n.color || "") === c ? " on" : ""}" data-color="${c}" data-id="${n.id}" aria-label="Цвет"></button>`).join("")}
        <small>${new Date(n.updated * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "short" })}</small>
        <button type="button" class="pl-n-del" data-ndel="${n.id}" title="Удалить">${ic("trash")}</button>
      </div></div>`).join("") || `<p class="note">Своих заметок пока нет.</p>`;
    $("plObjNotes").innerHTML = data.object_notes.map((n) => `<li><a href="/?open=${n.listing_id}">${esc(n.title)}</a>
      ${n.status ? `<span class="chip on">${STATUSES[n.status] || ""}</span>` : ""}${n.is_active ? "" : ` <small>снят с сайта</small>`}
      ${n.text ? `<p>${esc(n.text)}</p>` : ""}</li>`).join("") || `<li class="note">Пока нет. В окне объекта можно поставить статус и написать заметку.</li>`;
  }
  async function saveNote(id, patch) {
    const n = data.notes.find((x) => x.id === id);
    if (!n) return;
    Object.assign(n, patch);
    const r = await call("/api/planner/notes", { id, text: n.text, pinned: n.pinned, color: n.color || "" });
    if (!r.ok) { toast(r.data.detail || "Не сохранилось"); return; }
    if (r.data.note && r.data.note.deleted) data.notes = data.notes.filter((x) => x.id !== id);
  }

  // ─── нажатия ───
  let clickTimer = 0;
  document.addEventListener("click", async (e) => {
    const t = e.target.closest("button, [data-day], [data-ev-open]");
    if (!t) return;
    if (t.dataset.add) { openEvent(null, { kind: t.dataset.add, day: t.dataset.onDay || selDay }); return; }
    if (t.dataset.addDay !== undefined && t.dataset.addDay) { openEvent(null, { day: t.dataset.addDay }); return; }
    if (t.dataset.done) {
      const ev = findEv(Number(t.dataset.done));
      const done = !(ev && ev.done);
      t.classList.toggle("on", done);
      t.closest(".pv-ev") && t.closest(".pv-ev").classList.toggle("done", done);
      await call("/api/planner", { id: Number(t.dataset.done), done });
      toast(done ? "Сделано — отлично!" : "Снова в планах", 1600);
      await reload(selDay); return;
    }
    if (t.dataset.ev && t.classList.contains("pv-pill")) { viewEvent(findEv(Number(t.dataset.ev))); return; }
    if (t.dataset.evOpen && !e.target.closest("a, button")) { viewEvent(findEv(Number(t.dataset.evOpen))); return; }
    if (t.dataset.edit) { $("evView").close(); openEvent(findEv(Number(t.dataset.edit))); return; }
    if (t.dataset.clone) { $("evView").close(); openEvent(null, { clone: findEv(Number(t.dataset.clone)) }); return; }
    if (t.dataset.viewDone) {
      const ev = findEv(Number(t.dataset.viewDone));
      await call("/api/planner", { id: ev.id, done: !ev.done });
      toast(ev.done ? "Снова в планах" : "Сделано — отлично!", 1600);
      $("evView").close(); await reload(selDay); return;
    }
    if (t.dataset.viewDel) {
      if (!confirm("Удалить событие?")) return;
      await call("/api/planner", { id: Number(t.dataset.viewDel) }, "DELETE");
      $("evView").close(); toast("Удалено"); await reload(selDay); return;
    }
    if (t.dataset.month) { goMonth(new Date(month.getFullYear(), month.getMonth() + Number(t.dataset.month), 1)); return; }
    if (t.dataset.dayStep) { const d = fromKey(selDay); d.setDate(d.getDate() + Number(t.dataset.dayStep)); pickDay(dayKey(d)); return; }
    if (t.id === "pvToday") { pickDay(dayKey(new Date())); return; }
    if (t.dataset.day) {   // один щелчок — выбрать день, двойной — новое событие в этот день
      const k = t.dataset.day;
      if (clickTimer && t.dataset.day === clickTimer.k) { clearTimeout(clickTimer.id); clickTimer = 0; openEvent(null, { day: k }); return; }
      clickTimer = { k, id: setTimeout(() => { clickTimer = 0; }, 320) };
      pickDay(k, true); return;
    }
    if (t.dataset.kindToggle) {
      const k = t.dataset.kindToggle;
      if (hidden.has(k)) hidden.delete(k); else hidden.add(k);
      try { localStorage.setItem("plHidden", JSON.stringify([...hidden])); } catch { /* */ }
      renderAll(); return;
    }
    if (t.dataset.tab) {
      document.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("on", b === t));
      $("plCal").hidden = t.dataset.tab !== "cal"; $("plNotes").hidden = t.dataset.tab !== "notes";
      $("pvKinds").hidden = t.dataset.tab !== "cal"; return;
    }
    if (t.dataset.kind) { kind = t.dataset.kind; renderKinds(); if (!colorTouched) renderColors(); return; }
    if (t.dataset.colorPick) { color = t.dataset.colorPick; colorTouched = true; renderColors(); return; }
    if (t.id === "evDel" && editing) {
      if (!confirm("Удалить событие?")) return;
      await call("/api/planner", { id: editing.id }, "DELETE");
      $("evDlg").close(); toast("Удалено"); await reload(selDay); return;
    }
    if (t.hasAttribute("data-close")) { t.closest("dialog").close(); return; }
    if (t.id === "plPhone") {
      const url = `${location.origin}${data.feed}`;
      $("plFeed").value = url;
      $("plWebcal").href = url.replace(/^https?:/, "webcal:");
      $("phoneDlg").showModal(); return;
    }
    if (t.id === "plFeedCopy") {
      try { await navigator.clipboard.writeText($("plFeed").value); toast("Ссылка скопирована"); }
      catch { $("plFeed").select(); document.execCommand("copy"); toast("Ссылка скопирована"); }
      return;
    }
    if (t.dataset.pin) { const n = data.notes.find((x) => x.id === Number(t.dataset.pin)); await saveNote(n.id, { pinned: n.pinned ? 0 : 1 }); await reload(); return; }
    if (t.dataset.color !== undefined && t.dataset.id) { await saveNote(Number(t.dataset.id), { color: t.dataset.color }); renderNotes(); return; }
    if (t.dataset.ndel) {
      if (!confirm("Удалить заметку?")) return;
      await saveNote(Number(t.dataset.ndel), { text: "" }); renderNotes(); return;
    }
  });
  document.addEventListener("keydown", (e) => {   // день с клавиатуры: Enter — выбрать
    if (e.key === "Enter" && e.target.classList && e.target.classList.contains("pv-cell")) pickDay(e.target.dataset.day);
  });
  $("pvMonth").addEventListener("change", (e) => goMonth(new Date(month.getFullYear(), Number(e.target.value), 1)));
  $("pvYear").addEventListener("change", (e) => goMonth(new Date(Number(e.target.value), month.getMonth(), 1)));
  let qTimer = 0;
  $("pvQ").addEventListener("input", (e) => { clearTimeout(qTimer); qTimer = setTimeout(() => { q = e.target.value.trim(); renderGrid(); renderDay(); }, 200); });
  $("evListing").addEventListener("change", () => { showObj(); prefillFromListing(Number($("evListing").value)); });
  $("evClient").addEventListener("change", () => pickClient(Number($("evClient").value)));
  $("evForm").addEventListener("submit", saveEvent);
  let noteTimer = 0;
  document.addEventListener("input", (e) => {
    const id = Number(e.target.dataset && e.target.dataset.noteText);
    if (!id) return;
    clearTimeout(noteTimer);
    const text = e.target.value;
    if (!text.trim()) return;   // пустую заметку удаляем только кнопкой
    noteTimer = setTimeout(() => saveNote(id, { text }), 700);
  });
  $("plNoteNew").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = $("plNoteText").value.trim();
    if (!text) return;
    const r = await call("/api/planner/notes", { text });
    if (!r.ok) { toast(r.data.detail || "Не сохранилось"); return; }
    $("plNoteText").value = "";
    data.notes.unshift(r.data.note);
    renderNotes();
  });
  document.querySelectorAll("[data-ic]").forEach((el) => { el.outerHTML = ic(el.dataset.ic); });

  load().then(async (ok) => {
    if (!ok) return;
    renderNotes();
    const sp = new URLSearchParams(location.search);
    if (sp.get("add")) {
      await openEvent(null, { kind: KINDS[sp.get("add")] ? sp.get("add") : "show", listing: Number(sp.get("listing")) || null });
      if (CLIENT_ID) {   // из карточки клиента: подставить имя и телефон клиента
        const r = await call(`/api/clients?id=${CLIENT_ID}`);
        if (r.ok) { const f = $("evForm"); f.contact_name.value = r.data.client.name || ""; if (r.data.client.phone) f.contact_phone.value = r.data.client.phone; }
      }
    } else if (sp.get("event")) {
      const id = Number(sp.get("event"));
      let ev = findEv(id);
      if (!ev) {
        const now = Math.floor(Date.now() / 1000);
        const r = await call(`/api/planner?from=${now - 62 * 86400}&to=${now + 400 * 86400}`);
        ev = r.ok ? r.data.events.find((x) => x.id === id) : null;
      }
      if (ev) {
        await pickDay(dayKey(new Date(ev.ts * 1000)));
        const c = document.querySelector(`.pv-ev[data-ev="${id}"]`);
        if (c) { c.classList.add("hl"); c.scrollIntoView({ block: "center", behavior: "smooth" }); }
        viewEvent(ev);
      }
    }
    if (sp.get("tab") === "notes") document.querySelector('[data-tab="notes"]').click();
    if ([...sp.keys()].length) history.replaceState(null, "", location.pathname);
  });
})();
