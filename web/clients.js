/* CRM «Клиенты» (/clients): доска этапов (перетаскивание карточек), список, карточка клиента —
   данные и пожелания, объекты клиента (статусы) и подбор по пожеланиям, история, дела из планера. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ic = (n) => (window.ic ? window.ic(n) : "");
  const num = (x, d = 0) => Number(x || 0).toLocaleString("ru-RU", { maximumFractionDigits: d });
  const STAGE_C = { new: "#b8c8e4", search: "#8fd1cb", shows: "#e3cb8f", talks: "#d9b3e6", deposit: "#f0b77a", deal: "#a7d1b6", lost: "#e2a596" };
  const ROOMS = [[0, "Студия"], [1, "1"], [2, "2"], [3, "3"], [4, "4+"]];
  let data = { items: [], stages: {}, kinds: {}, item_status: {} }, view = "board", kindFilter = "", districts = [];
  let cur = null;   // открытая карточка: {client, items, log, events} или null (новый)
  let form = { kind: "buyer", rooms: [], districts: [], wishes: {} };
  // расширенные пожелания (все участвуют в подборе)
  const W_TYPES = [["flat", "Квартира"], ["house", "Дом"], ["land", "Участок"], ["commercial", "Коммерция"]];
  const W_COND = [["pcho", "ПЧО / предчистовая"], ["repair", "С ремонтом"], ["furniture", "С мебелью"]];
  const W_PAY = [["cash", "Наличные"], ["mortgage", "Ипотека"], ["matcap", "Маткапитал"], ["cert", "Сертификат"]];
  const REACT = { like: "нравится", no: "не подходит", want_show: "хочет посмотреть", question: "вопрос" };
  let complexes = [];

  async function call(url, body, method) {
    const opts = { credentials: "same-origin", method: method || (body !== undefined ? "POST" : "GET") };
    if (body !== undefined) { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(body); }
    let r = await fetch(url, opts);
    for (let i = 0; i < 3 && r.status === 503; i++) { await new Promise((res) => setTimeout(res, 1200 * (i + 1))); r = await fetch(url, opts); }
    let d = {};
    try { d = await r.json(); } catch { /* */ }
    return { ok: r.ok, status: r.status, data: d };
  }
  function toast(text, ms = 2400) {
    const t = $("toast");
    t.textContent = text;
    try { t.showPopover(); } catch { t.hidden = false; }
    clearTimeout(toast.t);
    toast.t = setTimeout(() => { try { t.hidePopover(); } catch { t.hidden = true; } }, ms);
  }
  const money = (p, deal) => !p ? "" : deal === "rent" ? `${num(p / 1000)} тыс` : `${num(p / 1e6, 2)} млн`;
  const budget = (c) => c.budget_min && c.budget_max ? `${money(c.budget_min, c.deal)}–${money(c.budget_max, c.deal)}`
    : c.budget_max ? `до ${money(c.budget_max, c.deal)}` : c.budget_min ? `от ${money(c.budget_min, c.deal)}` : "";
  const roomsTxt = (r) => (r || []).map((x) => (x === 0 ? "студия" : x === 4 ? "4+" : `${x}к`)).join(", ");
  const wish = (c) => [roomsTxt(c.rooms), budget(c), (c.districts || []).slice(0, 2).join(", ")].filter(Boolean).join(" · ");
  function nextHTML(c) {
    if (!c.next_ts) return "";
    const d = new Date(c.next_ts * 1000), now = new Date(), t0 = new Date(now); t0.setHours(0, 0, 0, 0);
    const over = c.next_ts * 1000 < Date.now(), today = d >= t0 && d < new Date(t0.getTime() + 86400000);
    const when = today ? `сегодня ${d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}`
      : d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
    return `<span class="c-next${over ? " over" : today ? " today" : ""}">${ic("clock")}${esc(c.next_text || "Связаться")} · ${when}</span>`;
  }

  // ─── доска и список ─────────────────────────────────────────────────
  function visible() { return data.items.filter((c) => !c.archived && (!kindFilter || c.kind === kindFilter)); }
  function cardHTML(c) {
    return `<button type="button" class="c-card" data-open="${c.id}">
      <div class="c-card-row"><b>${esc(c.name)}</b><span class="c-tag">${esc(data.kinds[c.kind] || "")}</span></div>
      ${wish(c) ? `<small>${esc(wish(c))}</small>` : ""}
      ${nextHTML(c)}
      ${c.n_items ? `<small>${ic("home")} объектов: ${c.n_items}${c.n_liked ? ` · понравилось: ${c.n_liked}` : ""}</small>` : ""}</button>`;
  }
  function render() {
    const vis = visible();
    $("cTotal").textContent = data.items.length ? data.items.filter((c) => !c.archived).length : "";
    const counts = {};
    data.items.filter((c) => !c.archived).forEach((c) => { counts[c.kind] = (counts[c.kind] || 0) + 1; });
    $("cKinds").innerHTML = `<button type="button" class="chip${kindFilter ? "" : " on"}" data-kind="">Все</button>` +
      Object.entries(data.kinds).filter(([k]) => counts[k]).map(([k, t]) =>
        `<button type="button" class="chip${kindFilter === k ? " on" : ""}" data-kind="${k}">${esc(t)}<b>${counts[k]}</b></button>`).join("");
    $("cEmpty").hidden = !!data.items.length;
    $("cBoard").hidden = view !== "board" || !data.items.length;
    $("cList").hidden = view !== "list" || !data.items.length;
    $("cBoard").innerHTML = Object.entries(data.stages).map(([k, t]) => {
      const list = vis.filter((c) => c.stage === k);
      return `<section class="c-col" data-stage="${k}" style="--c:${STAGE_C[k]}">
        <div class="c-col-h"><span><i></i>${esc(t)}</span><b>${list.length}</b></div>
        <div class="c-col-body">${list.map(cardHTML).join("")}</div>
        ${k === "new" ? `<button type="button" class="c-col-add" data-new="${k}">+ Клиент</button>` : ""}</section>`;
    }).join("");
    $("cList").innerHTML = vis.map((c) => `<div class="c-row" data-open="${c.id}">
        <div><b>${esc(c.name)}</b><small>${esc(c.phone || "")}</small></div>
        <span class="c-stage-pill" style="--c:${STAGE_C[c.stage]}">${esc(data.stages[c.stage])}</span>
        <small>${esc(wish(c))}</small><span>${nextHTML(c)}</span><small>${c.n_items ? `объектов: ${c.n_items}` : ""}</small></div>`).join("");
  }
  async function load() {
    const q = $("cQ").value.trim();
    const r = await call(`/api/clients${q ? `?q=${encodeURIComponent(q)}` : ""}`);
    if (r.status === 401) { $("cGuest").hidden = false; return false; }
    $("cApp").hidden = false;
    data = r.data;
    render();
    return true;
  }

  // ─── перетаскивание карточки в другой этап: плавно, с «призраком», доска листается у краёв ───
  // Мышь — сразу после сдвига на 6 px; палец — долгое нажатие (≈0,3 с), чтобы не мешать прокрутке.
  let drag = null, justDragged = false;
  async function moveTo(id, stage) {
    const c = data.items.find((x) => x.id === id);
    if (!c || c.stage === stage) return;
    c.stage = stage;
    render();
    const el = document.querySelector(`.c-col[data-stage="${stage}"] .c-card[data-open="${id}"]`);
    if (el) el.animate([{ transform: "scale(1.04)", boxShadow: "0 0 0 2px #e3cb8f" }, { transform: "none", boxShadow: "none" }], { duration: 500, easing: "ease-out" });
    const r = await call("/api/clients", { id, stage });
    if (!r.ok) { toast("Не получилось перенести"); load(); } else toast(`${c.name} → ${data.stages[stage]}`);
  }
  function beginDrag(st, ev) {
    const r = st.card.getBoundingClientRect();
    const ghost = st.card.cloneNode(true);
    ghost.classList.add("c-ghost");
    ghost.style.width = `${r.width}px`;
    document.body.appendChild(ghost);
    st.card.classList.add("dragging");
    document.body.classList.add("c-dragging");
    drag = { ...st, ghost, offX: ev.clientX - r.left, offY: ev.clientY - r.top, x: ev.clientX, y: ev.clientY, col: null };
    if (navigator.vibrate && st.touch) navigator.vibrate(12);
    follow(ev);
    autoScroll();
  }
  function follow(ev) {
    drag.x = ev.clientX; drag.y = ev.clientY;
    drag.ghost.style.transform = `translate(${ev.clientX - drag.offX}px, ${ev.clientY - drag.offY}px) rotate(2.5deg) scale(1.03)`;
    const under = document.elementFromPoint(ev.clientX, ev.clientY);
    const col = under && under.closest(".c-col");
    if (col !== drag.col) {
      if (drag.col) drag.col.classList.remove("drop");
      if (col) col.classList.add("drop");
      drag.col = col;
    }
  }
  function autoScroll(t) {
    if (!drag) return;
    const dt = drag.t && t ? Math.min(t - drag.t, 50) / 1000 : 0;
    drag.t = t;
    // чем ближе к краю, тем быстрее (до ~900 px/с), одинаково на любом экране
    const b = $("cBoard"), r = b.getBoundingClientRect(), edge = 90;
    const depth = drag.x < r.left + edge ? -(r.left + edge - drag.x) : drag.x > r.right - edge ? drag.x - (r.right - edge) : 0;
    const k = Math.min(Math.abs(depth) / edge, 1);
    const v = Math.sign(depth) * Math.round(900 * k * k * dt);
    if (v) {
      b.scrollLeft += v;
      const under = document.elementFromPoint(drag.x, drag.y);   // доска уехала — колонка под пальцем другая
      const col = under && under.closest(".c-col");
      if (col !== drag.col) { if (drag.col) drag.col.classList.remove("drop"); if (col) col.classList.add("drop"); drag.col = col; }
    }
    requestAnimationFrame(autoScroll);
  }
  function endDrag() {
    const d = drag;
    drag = null;
    document.body.classList.remove("c-dragging");
    d.card.classList.remove("dragging");
    if (d.col) d.col.classList.remove("drop");
    justDragged = true;
    setTimeout(() => { justDragged = false; }, 50);
    const target = d.col && d.col.dataset.stage;
    const to = target && d.col.querySelector(".c-col-body");
    if (to) {   // «призрак» мягко садится в колонку
      const r = to.getBoundingClientRect();
      d.ghost.style.transition = "transform .28s cubic-bezier(.22,1,.36,1), opacity .28s";
      d.ghost.style.transform = `translate(${r.left}px, ${r.top}px) rotate(0) scale(1)`;
      d.ghost.style.opacity = "0";
      setTimeout(() => d.ghost.remove(), 280);
    } else d.ghost.remove();
    if (target) moveTo(d.id, target);
  }
  document.addEventListener("pointerdown", (e) => {
    const card = e.target.closest(".c-board .c-card");
    if (!card || e.button > 0) return;
    const st = { card, id: Number(card.dataset.open), x0: e.clientX, y0: e.clientY, touch: e.pointerType !== "mouse" };
    let timer = st.touch ? setTimeout(() => beginDrag(st, { clientX: st.lx ?? st.x0, clientY: st.ly ?? st.y0 }), 300) : 0;
    const move = (ev) => {
      st.lx = ev.clientX; st.ly = ev.clientY;
      if (drag) { ev.preventDefault(); follow(ev); return; }
      const dist = Math.hypot(ev.clientX - st.x0, ev.clientY - st.y0);
      if (!st.touch && dist > 6) beginDrag(st, ev);
      else if (st.touch && dist > 10) { clearTimeout(timer); stop(); }   // палец поехал — это прокрутка
    };
    const up = () => { clearTimeout(timer); if (drag) endDrag(); stop(); };
    function stop() { removeEventListener("pointermove", move); removeEventListener("pointerup", up); removeEventListener("pointercancel", up); }
    addEventListener("pointermove", move, { passive: false });
    addEventListener("pointerup", up);
    addEventListener("pointercancel", up);
  });
  // пока тащим пальцем — страница не прокручивается
  document.addEventListener("touchmove", (e) => { if (drag) e.preventDefault(); }, { passive: false });
  document.addEventListener("click", (e) => { if (justDragged && e.target.closest(".c-card")) { e.stopPropagation(); e.preventDefault(); } }, true);

  // ─── карточка клиента ──────────────────────────────────────────────
  function tab(name) {
    document.querySelectorAll(".c-tabs [data-tab]").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
    document.querySelectorAll("#cDlg [data-pane]").forEach((p) => { p.hidden = p.dataset.pane !== name; });
    if (name === "items" && cur && !$("dMatchList").dataset.loaded) match();
  }
  function stagesHTML() {
    const keys = Object.keys(data.stages), at = cur ? keys.indexOf(cur.client.stage) : 0;
    $("dStages").hidden = !cur;
    // кнопки уже есть (тот же клиент обновился) — не пересобираем, иначе плашка «прыгнет»
    if (cur && $("dStages").dataset.cid === String(cur.client.id) && $("dStages").querySelector(".c-stage-ind")) {
      slideStage(cur.client.stage);
      return;
    }
    $("dStages").dataset.cid = cur ? cur.client.id : "";
    $("dStages").innerHTML = `<i class="c-stage-ind" aria-hidden="true"></i>` + keys.map((k, i) => `<button type="button" data-stage="${k}"
      class="${i === at ? "on" : i < at && k !== "lost" ? "past" : ""}" style="--c:${STAGE_C[k]}">${esc(data.stages[k])}</button>`).join("");
    requestAnimationFrame(() => slideStage(cur ? cur.client.stage : keys[0], false));
  }
  // плашка выбранного этапа плавно переезжает к нажатому
  function slideStage(stage, animate = true) {
    const box = $("dStages"), ind = box.querySelector(".c-stage-ind"), b = box.querySelector(`[data-stage="${stage}"]`);
    if (!ind || !b) return;
    if (!animate) ind.style.transition = "none";
    ind.style.width = `${b.offsetWidth}px`;
    ind.style.transform = `translateX(${b.offsetLeft}px)`;
    ind.style.background = STAGE_C[stage];
    if (!animate) { void ind.offsetWidth; ind.style.transition = ""; }
    const keys = Object.keys(data.stages), at = keys.indexOf(stage);
    box.querySelectorAll("[data-stage]").forEach((x, i) => {
      x.classList.toggle("on", x === b);
      x.classList.toggle("past", i < at && x.dataset.stage !== "lost");
    });
    const sl = b.offsetLeft - box.clientWidth / 2 + b.offsetWidth / 2;   // на телефоне — прокрутить к выбранному
    box.scrollTo({ left: sl, behavior: animate ? "smooth" : "auto" });
  }
  function fillForm() {
    const c = cur ? cur.client : { kind: "buyer", rooms: [], districts: [], deal: "sale" };
    form = { kind: c.kind, rooms: [...(c.rooms || [])], districts: [...(c.districts || [])], wishes: { ...(c.wishes || {}) } };
    const f = $("pCard");
    const w = form.wishes;
    f.area_min.value = w.area_min || ""; f.area_max.value = w.area_max || "";
    f.buy_by.value = w.buy_by || ""; f.budget_tol.value = w.budget_tol || "";
    fillComplexes();
    f.name.value = c.name || ""; f.phone.value = c.phone || "";
    const rent = c.deal === "rent" || ["tenant", "landlord"].includes(c.kind);
    const unit = rent ? 1000 : 1e6;
    f.budget_min.value = c.budget_min ? String(+(c.budget_min / unit).toFixed(2)).replace(".", ",") : "";
    f.budget_max.value = c.budget_max ? String(+(c.budget_max / unit).toFixed(2)).replace(".", ",") : "";
    f.next_text.value = c.next_text || ""; f.source.value = c.source || ""; f.note.value = c.note || "";
    const nd = c.next_ts ? new Date(c.next_ts * 1000 - new Date().getTimezoneOffset() * 60000).toISOString() : "";
    f.next_day.value = nd ? nd.slice(0, 10) : ""; f.next_time.value = nd ? nd.slice(11, 16) : "";
    if (window.Pick) Pick.refresh(f);
    renderFormChips();
    $("fErr").hidden = true;
    $("dDelete").hidden = $("dArchive").hidden = !cur;
    $("dArchive").textContent = cur && cur.client.archived ? "Вернуть из архива" : "В архив";
  }
  function renderFormChips() {
    const rent = ["tenant", "landlord"].includes(form.kind);
    $("fBudgetL").textContent = rent ? "Бюджет от, тыс ₽/мес" : "Бюджет от, млн ₽";
    $("fBudgetH").textContent = rent ? "Бюджет до, тыс ₽/мес" : "Бюджет до, млн ₽";
    $("fKind").innerHTML = Object.entries(data.kinds).map(([k, t]) => `<button type="button" class="chip${form.kind === k ? " on" : ""}" data-fkind="${k}">${esc(t)}</button>`).join("");
    $("fRooms").innerHTML = ROOMS.map(([k, t]) => `<button type="button" class="chip${form.rooms.includes(k) ? " on" : ""}" data-froom="${k}">${t}</button>`).join("");
    $("fDistricts").innerHTML = "";   // выбранные районы видны в самой кнопке списка — без дублей чипами
    const w = form.wishes, arr = (k) => w[k] || [];
    const chips = (id, list, key) => { $(id).innerHTML = list.map(([k, t]) => `<button type="button" class="chip${arr(key).includes(k) ? " on" : ""}" data-wl="${key}:${k}">${t}</button>`).join(""); };
    chips("fTypes", W_TYPES, "types"); chips("fCond", W_COND, "cond"); chips("fPay", W_PAY, "pay");
    $("fFloor").innerHTML = [["not_first", "Не первый"], ["not_last", "Не последний"]].map(([k, t]) => `<button type="button" class="chip${w[k] ? " on" : ""}" data-wb="${k}">${t}</button>`).join("");
    $("fEnc").innerHTML = `<button type="button" class="chip${w.no_encumbrance ? " on" : ""}" data-wb="no_encumbrance">Только без обременения</button>`;
    const n = ["types", "cond", "pay", "complexes"].reduce((s2, k) => s2 + arr(k).length, 0) + ["not_first", "not_last", "no_encumbrance", "area_min", "area_max", "buy_by", "budget_tol"].filter((k) => w[k]).length;
    $("fMoreN").textContent = n ? `· ${n}` : "";
    const sel = $("fDistrictAdd");   // отмеченные в списке = чипы (убрали чип — снялась галочка)
    [...sel.options].forEach((o) => { o.selected = form.districts.includes(o.value); });
    sel.dispatchEvent(new Event("pk-refresh"));
  }
  function fillComplexes() {
    const sel = $("fComplexes");
    const chosen = form.wishes.complexes || [];
    const names = [...new Set([...chosen, ...complexes])];
    sel.innerHTML = names.map((n) => `<option value="${esc(n)}"${chosen.includes(n) ? " selected" : ""}>${esc(n)}</option>`).join("");
    sel.dispatchEvent(new Event("pk-refresh"));
  }
  function header() {
    const c = cur ? cur.client : null;
    $("dName").textContent = c ? c.name : "Новый клиент";
    $("dAva").textContent = c ? (c.name.trim()[0] || "К").toUpperCase() : "+";
    $("dSub").textContent = c ? [data.kinds[c.kind], c.phone].filter(Boolean).join(" · ") : "Заполните, кто это и что ищет — сервис подберёт объекты";
    const digits = c && c.phone ? c.phone.replace(/\D/g, "") : "";
    $("dActs").innerHTML = digits ? `<a href="tel:${esc(c.phone)}" aria-label="Позвонить">${ic("phone")}</a>
      <button type="button" class="c-max" data-max="${esc(c.phone)}" aria-label="Написать в MAX" title="Написать в MAX">MAX</button>` : "";
    $("dItemsN").textContent = cur && cur.items.length ? cur.items.length : "";
    document.querySelectorAll('.c-tabs [data-tab]:not([data-tab="card"])').forEach((b) => { b.hidden = !cur; });
  }
  function itemsHTML() {
    const st = data.item_status;
    $("dItems").innerHTML = cur.items.length ? cur.items.map((o) => `<div class="c-it${o.is_active ? "" : " off"}">
        ${o.photo ? `<img src="${esc(o.photo)}" alt="" loading="lazy">` : `<span class="noimg"></span>`}
        <div><a href="/?open=${o.id}" target="_blank">${esc(o.complex ? `ЖК ${o.complex}` : o.title)}</a>
          <small>${esc(o.title)} · ${esc(money(o.price, o.deal))} ₽${o.is_active ? "" : " · снят"}</small>
          ${o.reaction ? `<small class="c-react r-${esc(o.reaction)}">Ответ клиента: ${esc(REACT[o.reaction] || "")}${o.reaction_text ? ` — «${esc(o.reaction_text)}»` : ""}</small>` : ""}
          ${(() => { const w = lastWork(o.id); return w ? `<small class="c-wk">${ic((WK[w.kind] || WK.note)[0])}${(WK[w.kind] || WK.note)[1]}${w.text ? ": " + esc(w.text.slice(0, 90)) : ""}</small>` : ""; })()}</div>
        <select data-item="${o.id}" aria-label="Статус">${Object.entries(st).map(([k, t]) => `<option value="${k}"${o.status === k ? " selected" : ""}>${t}</option>`).join("")}
          <option value="__del">Убрать</option></select></div>`).join("")
      : `<p class="g-empty">Объектов пока нет. Отметьте подходящие ниже и нажмите «В подборку» — или добавьте из окна объекта кнопкой «Клиенту».</p>`;
  }
  const plural = (n, a, b, c) => (n % 10 === 1 && n % 100 !== 11 ? a : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? b : c);
  // подборки клиента: все его ссылки в одном месте; новая — сразу привязана к клиенту
  function picksHTML() {
    const ps = cur.picks || [];
    $("dPicks").innerHTML = `<div class="c-picks-h"><h3>Подборки клиента</h3>
        <button type="button" class="pill light" data-new-pick>${ic("plus")}Новая подборка</button></div>
      ${ps.length ? `<div class="c-picks-l">${ps.map((p) => { const v = p.views_unique || p.views; return `<div class="c-pick">
          <span class="c-pick-c c-${esc(p.color || "gold")}">${ic("folder")}</span>
          <span><b>${esc(p.title)}</b><small>${p.n} ${plural(p.n, "объект", "объекта", "объектов")} · ${v
            ? `открывал ${v} ${plural(v, "раз", "раза", "раз")}` : "ещё не открывал"}</small></span>
          <span class="c-pick-a"><button type="button" class="c-x" data-copy-pick="${esc(p.url)}" title="Скопировать ссылку для клиента" aria-label="Скопировать ссылку">${ic("link")}</button>
            <a class="pill light" href="/picks?open=${p.id}">Открыть</a></span></div>`; }).join("")}</div>`
        : `<p class="g-empty">Подборок пока нет. Объекты из подборки клиента сами появятся ниже со статусом «отправлен».</p>`}`;
  }
  async function newPick() {
    const matched = cur.items.filter((o) => o.status === "matched").map((o) => o.id);
    const n = (cur.picks || []).length + 1;
    const r = await call("/api/picks", { title: `Для ${cur.client.name}${n > 1 ? ` (${n})` : ""}`.slice(0, 120), client_id: cur.client.id });
    if (!r.ok) { toast(r.data.detail || "Подборки — по подписке"); return; }
    if (matched.length && confirm(`Добавить в новую подборку подобранные объекты (${matched.length})?`)) {
      await call(`/api/clients/${cur.client.id}/topick`, { ids: matched });
    }
    await refreshCur();
    toast("Подборка создана — «Открыть» рядом с ней", 3000);
  }
  const WK = { note: ["note", "Заметка"], call: ["phone", "Звонок"], show: ["home", "Показ"], meet: ["users", "Встреча"] };
  const lastWork = (lid) => (cur.work || []).find((w) => w.listing_id === lid);
  function upgradeItems() { if (window.Pick) $("dItems").querySelectorAll("select[data-item]").forEach((s) => Pick.select(s)); }
  function logHTML() {
    const ico = { note: "note", stage: "layers", object: "home", event: "calendar", new: "user", next: "clock" };
    const when = (ts) => new Date(ts * 1000).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
    // журнал по объектам (из окна объекта и подборок) — вместе с историей клиента, по времени
    const rows = cur.log.map((x) => ({ ts: x.ts, html: `<li>${ic(ico[x.kind] || "note")}<span>${esc(x.text)}</span><small>${when(x.ts)}</small></li>` }))
      .concat((cur.work || []).map((w) => ({ ts: w.ts, html: `<li class="c-log-wk">${ic((WK[w.kind] || WK.note)[0])}<span>
          <b>${(WK[w.kind] || WK.note)[1]}</b> · <a href="/?open=${w.listing_id}">${esc(w.l_complex ? `ЖК ${w.l_complex}` : w.l_title || "объект")}</a>
          ${w.pick_title ? `<a class="c-log-tag" href="/picks?open=${w.collection_id}">${esc(w.pick_title)}</a>` : ""}
          ${w.text ? `<br>${esc(w.text)}` : ""}</span><small>${when(w.ts)}</small></li>` })))
      .sort((a, b) => b.ts - a.ts);
    $("dLog").innerHTML = rows.map((x) => x.html).join("");
  }
  function tasksHTML() {
    const id = cur.client.id;
    $("dTaskActs").innerHTML = `<a class="pill light" href="/planner?add=show&client=${id}">${ic("home")}Показ</a>
      <a class="pill light" href="/planner?add=call&client=${id}">${ic("phone")}Созвон</a>
      <a class="pill light" href="/planner?add=meet&client=${id}">${ic("users")}Встреча</a>`;
    $("dEvents").innerHTML = cur.events.length ? cur.events.map((e) => `<li><a class="${e.done ? "done" : ""}" href="/planner?event=${e.id}">
        <span>${esc(e.title)}</span><small>${new Date(e.ts * 1000).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</small></a></li>`).join("")
      : `<p class="g-empty">Событий пока нет — запланируйте показ или созвон, они попадут сюда и в историю.</p>`;
  }
  async function open(id) {
    $("dMatchList").innerHTML = ""; delete $("dMatchList").dataset.loaded;
    if (id) {
      const r = await call(`/api/clients?id=${id}`);
      if (!r.ok) { toast("Клиент не найден"); return; }
      cur = r.data;
      itemsHTML(); upgradeItems(); logHTML(); tasksHTML(); picksHTML();
    } else cur = null;
    header(); stagesHTML(); fillForm(); tab("card");
    if (!$("cDlg").open) $("cDlg").showModal();
    history.replaceState(null, "", id ? `/clients?id=${id}` : "/clients");
  }
  async function refreshCur() {
    const r = await call(`/api/clients?id=${cur.client.id}`);
    if (r.ok) { cur = r.data; header(); stagesHTML(); itemsHTML(); upgradeItems(); logHTML(); tasksHTML(); picksHTML(); }
  }
  let matchPage = 1, matchSince = 0, freshIds = [];   // matchSince — из уведомления «новое под клиента»
  let mSort = "new", mPhoto = false, mNotFirst = false, showHidden = false;
  function matchItem(o) {
    const diff = o.market_diff != null && o.market_diff < -2 ? `<span class="c-good">ниже рынка ${Math.abs(o.market_diff)}%</span>` : "";
    return `<div class="c-it${showHidden ? "" : " c-mit"}" data-mid="${o.id}">
        ${showHidden ? "" : `<label class="c-chk"><input type="checkbox" data-msel="${o.id}" aria-label="Отметить"></label>`}
        ${o.photos && o.photos[0] ? `<img src="${esc(o.photos[0])}" alt="" loading="lazy">` : `<span class="noimg"></span>`}
        <div><a href="/?open=${o.id}" target="_blank">${esc(o.complex ? `ЖК ${o.complex}` : o.title)}</a>
          <small>${esc(o.title)} · ${esc(money(o.price, o.deal))} ₽${o.district ? ` · ${esc(o.district)}` : ""} ${diff}</small></div>
        ${showHidden ? `<button type="button" class="pill light" data-unhide="${o.id}">Вернуть</button>`
          : `<span class="c-mact"><button type="button" class="pill light" data-offer="${o.id}" title="В подборку клиента">В подборку</button>
            <button type="button" class="c-x" data-nope="${o.id}" title="Не подходит — больше не предлагать" aria-label="Не подходит">${ic("x")}</button></span>`}</div>`;
  }
  const selected = () => [...document.querySelectorAll("[data-msel]:checked")].map((x) => Number(x.dataset.msel));
  function selBar() {
    const n = selected().length;
    $("dMbar").hidden = showHidden || !$("dMatchList").querySelector("[data-msel]");
    $("mSel").textContent = n ? `отмечено: ${n}` : "";
    $("mToPick").disabled = $("mNo").disabled = !n;
    $("mAll").checked = n && n === document.querySelectorAll("[data-msel]").length;
  }
  async function sendToPick(ids) {
    const r = await call(`/api/clients/${cur.client.id}/topick`, { ids });
    if (!r.ok) { toast(r.data.detail || "Не получилось"); return; }
    try { await navigator.clipboard.writeText(r.data.pick.url); } catch { /* */ }
    toast(`В подборке «${r.data.pick.title}»: +${r.data.added}. Ссылка скопирована — отправьте клиенту`, 4200);
    await refreshCur(); load(); match();
  }
  async function hideIds(ids, on = true) {
    await call(`/api/clients/${cur.client.id}/${on ? "hide" : "unhide"}`, { ids });
    toast(on ? "Больше не предлагаем — вернуть можно в «Скрытых»" : "Вернули в подбор", 2400);
    await refreshCur(); match();
  }
  async function toPick() {
    if (!freshIds.length) return;
    const r = await call(`/api/clients/${cur.client.id}/topick`, { ids: freshIds });
    if (!r.ok) { toast(r.data.detail || "Не получилось"); return; }
    const url = r.data.pick.url;
    try { await navigator.clipboard.writeText(url); } catch { /* */ }
    toast(`Добавлено в «${r.data.pick.title}»: ${r.data.added}. Ссылка для клиента скопирована`, 4000);
    await refreshCur();
    matchSince = 0; match();
  }
  async function match(more = false) {
    const box = $("dMatchList");
    box.dataset.loaded = "1";
    matchPage = more ? matchPage + 1 : 1;
    if (!more) box.innerHTML = `<p class="g-empty">Ищем все подходящие…</p>`;
    const qs = new URLSearchParams({ page: matchPage, sort: mSort, ...(mPhoto ? { photo: "1" } : {}), ...(mNotFirst ? { not_first: "1" } : {}),
      ...(matchSince ? { since: matchSince } : {}) });
    const r = showHidden ? await call(`/api/clients/${cur.client.id}/hidden`)
      : await call(`/api/clients/${cur.client.id}/match?${qs}`);
    const d = r.ok ? r.data : { items: [], total: 0 };
    if (showHidden) { d.total = d.items.length; d.more = false; }
    if (matchSince && !more) freshIds = d.items.map((x) => x.id);
    const old = box.querySelector(".c-more");
    if (old) old.remove();
    if (!more) {
      $("dMatchN").textContent = showHidden ? `· скрытые ${d.total}` : d.total ? num(d.total) : "";
      const hb = $("mHidden");
      const hn = showHidden ? 0 : (d.hidden || 0);
      hb.hidden = !showHidden && !hn;
      hb.textContent = showHidden ? "← К подбору" : `Скрытые · ${hn}`;
      box.innerHTML = d.items.length ? "" : `<p class="g-empty">${wish(cur.client) ? "Сейчас подходящих нет — уточните пожелания или загляните позже." : "Укажите в карточке комнаты, бюджет или районы — и сервис подберёт объекты."}</p>`;
    }
    if (matchSince && !more) {   // новые с прошлой проверки — плашкой сверху, «всё в подборку» одной кнопкой
      box.insertAdjacentHTML("afterbegin", `<div class="c-fresh">${ic("sparkle")}<span><b>Новые с ${new Date(matchSince * 1000).toLocaleString("ru-RU", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" })}: ${d.items.length}</b>
        <small>Объекты, появившиеся под пожелания клиента</small></span>
        ${d.items.length ? `<button type="button" class="pill" data-topick>Всё в подборку</button>` : ""}
        <button type="button" class="chip" data-all-match>Все подходящие</button></div>`);
    }
    box.insertAdjacentHTML("beforeend", d.items.map(matchItem).join(""));
    if (d.more) box.insertAdjacentHTML("beforeend", `<button type="button" class="pill light c-more" data-match-more>Показать ещё</button>`);
    selBar();
  }

  // ─── события ───────────────────────────────────────────────────────
  document.addEventListener("click", async (e) => {
    const t = e.target.closest("[data-open], [data-new], [data-kind], [data-view], [data-tab], [data-stage], [data-fkind], [data-froom], [data-fdel], [data-offer], [data-wl], [data-wb], button");
    if (!t) return;
    if (t.dataset.open && !t.closest("dialog")) { open(Number(t.dataset.open)); return; }
    if (t.dataset.new !== undefined || t.id === "cNew") { open(null); return; }
    if (t.dataset.kind !== undefined && !t.closest("dialog")) { kindFilter = t.dataset.kind; render(); return; }
    if (t.dataset.view) { view = t.dataset.view; document.querySelectorAll("[data-view]").forEach((b) => b.classList.toggle("on", b === t)); render(); return; }
    if (t.dataset.tab) { tab(t.dataset.tab); return; }
    if (t.dataset.stage && t.closest("#dStages") && cur) {
      slideStage(t.dataset.stage);   // сразу, не дожидаясь сервера
      const r = await call("/api/clients", { id: cur.client.id, stage: t.dataset.stage });
      if (r.ok) { await refreshCur(); load(); toast(`Этап: ${data.stages[t.dataset.stage]}`); }
      return;
    }
    if (t.dataset.fkind) { form.kind = t.dataset.fkind; renderFormChips(); return; }
    if (t.dataset.froom !== undefined) { const k = Number(t.dataset.froom); form.rooms = form.rooms.includes(k) ? form.rooms.filter((x) => x !== k) : [...form.rooms, k]; renderFormChips(); return; }
    if (t.dataset.fdel) { form.districts = form.districts.filter((x) => x !== t.dataset.fdel); renderFormChips(); return; }
    if (t.dataset.offer) { sendToPick([Number(t.dataset.offer)]); return; }
    if (t.dataset.nope) { t.closest(".c-it").remove(); hideIds([Number(t.dataset.nope)]); return; }
    if (t.dataset.unhide) { t.closest(".c-it").remove(); hideIds([Number(t.dataset.unhide)], false); return; }
    if (t.id === "mToPick") { const ids = selected(); if (ids.length) sendToPick(ids); return; }
    if (t.id === "mNo") { const ids = selected(); if (ids.length) hideIds(ids); return; }
    if (t.id === "mHidden") { showHidden = !showHidden; match(); return; }
    if (t.dataset.mf) {
      if (t.dataset.mf === "photo") mPhoto = !mPhoto; else mNotFirst = !mNotFirst;
      t.classList.toggle("on"); match(); return;
    }
    if (t.dataset.wl) {   // пожелания-списки (тип, состояние, оплата)
      const [k, v] = t.dataset.wl.split(":");
      const a = form.wishes[k] || [];
      form.wishes[k] = a.includes(v) ? a.filter((x) => x !== v) : [...a, v];
      renderFormChips(); return;
    }
    if (t.dataset.wb) { form.wishes[t.dataset.wb] = !form.wishes[t.dataset.wb]; renderFormChips(); return; }
    if (t.id === "dMatch") { matchSince = 0; showHidden = false; match(); return; }
    if (t.hasAttribute("data-all-match")) { matchSince = 0; match(); return; }
    if (t.hasAttribute("data-topick")) { toPick(); return; }
    if (t.hasAttribute("data-new-pick")) { newPick(); return; }
    if (t.dataset.copyPick) {
      try { await navigator.clipboard.writeText(t.dataset.copyPick); toast("Ссылка скопирована — отправьте клиенту"); }
      catch { prompt("Скопируйте ссылку:", t.dataset.copyPick); }
      return;
    }
    if (t.hasAttribute("data-match-more")) { t.disabled = true; match(true); return; }
    if (t.id === "dDelete" && cur) {
      if (!confirm(`Удалить клиента «${cur.client.name}» со всей историей?`)) return;
      await call("/api/clients", { id: cur.client.id }, "DELETE");
      $("cDlg").close(); load(); toast("Клиент удалён"); return;
    }
    if (t.id === "dArchive" && cur) {
      const r = await call("/api/clients", { ...cur.client, archived: !cur.client.archived, next_ts: cur.client.next_ts });
      if (r.ok) { $("cDlg").close(); load(); toast(cur.client.archived ? "Клиент возвращён" : "Клиент в архиве"); }
      return;
    }
    if (t.hasAttribute("data-close")) t.closest("dialog").close();
  });
  document.addEventListener("change", (e) => {
    if (e.target.id === "mAll") { document.querySelectorAll("[data-msel]").forEach((x) => { x.checked = e.target.checked; }); selBar(); return; }
    if (e.target.dataset && e.target.dataset.msel) { selBar(); return; }
    if (e.target.id === "mSort") { mSort = e.target.value; match(); }
  });
  document.addEventListener("change", async (e) => {
    const s = e.target.closest("[data-item]");
    if (!s || !cur) return;
    const status = s.value === "__del" ? null : s.value;
    const r = await call(`/api/clients/${cur.client.id}/items`, { listing_id: Number(s.dataset.item), status });
    if (r.ok) { await refreshCur(); load(); }
  });
  $("pCard").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    const body = { name: f.name.value, phone: f.phone.value, kind: form.kind, rooms: form.rooms, districts: form.districts,
      budget_min: f.budget_min.value, budget_max: f.budget_max.value, next_text: f.next_text.value, source: f.source.value,
      note: f.note.value, next_ts: f.next_day.value ? Math.floor(new Date(`${f.next_day.value}T${f.next_time.value || "10:00"}`).getTime() / 1000) : null,
      stage: cur ? cur.client.stage : "new", archived: cur ? cur.client.archived : 0,
      wishes: { ...form.wishes, area_min: f.area_min.value, area_max: f.area_max.value, buy_by: f.buy_by.value, budget_tol: f.budget_tol.value,
        complexes: [...$("fComplexes").selectedOptions].map((o) => o.value) } };
    if (cur) body.id = cur.client.id;
    const r = await call("/api/clients", body);
    if (!r.ok) { $("fErr").textContent = r.data.detail || "Не получилось сохранить"; $("fErr").hidden = false; return; }
    const isNew = !cur;
    cur = r.data;
    header(); stagesHTML(); itemsHTML(); upgradeItems(); logHTML(); tasksHTML();
    load();
    toast(isNew ? "Клиент добавлен — подберите объекты" : "Сохранено");
    if (isNew) { history.replaceState(null, "", `/clients?id=${cur.client.id}`); tab("items"); }
  });
  $("dNote").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = e.target.text.value.trim();
    if (!text || !cur) return;
    const r = await call(`/api/clients/${cur.client.id}/note`, { text });
    if (r.ok) { e.target.text.value = ""; await refreshCur(); }
  });
  $("cDlg").addEventListener("close", () => history.replaceState(null, "", "/clients"));
  let qTimer = 0;
  $("cQ").addEventListener("input", () => { clearTimeout(qTimer); qTimer = setTimeout(load, 300); });
  // районы: список с галочками и поиском (pickers.js) — можно отметить сразу несколько
  $("fDistrictAdd").addEventListener("change", (e) => {
    form.districts = [...e.target.selectedOptions].map((o) => o.value).filter(Boolean);
    renderFormChips();
  });

  document.querySelectorAll("[data-ic]").forEach((el) => { el.outerHTML = ic(el.dataset.ic); });
  (async () => {
    if (!(await load())) return;
    const sp = new URLSearchParams(location.search);
    if (Number(sp.get("id"))) {
      matchSince = Number(sp.get("fresh")) || 0;   // из уведомления «новое под клиента»
      await open(Number(sp.get("id")));
      if (matchSince) {
        tab("items");
        if (sp.has("topick")) { await new Promise((res) => setTimeout(res, 600)); toPick(); }
      }
    }
    else if (sp.has("new")) {
      open(null);
      if (sp.get("from")) {   // «Создать клиента из этого поиска» на главной: фильтры → пожелания
        try {
          const q = JSON.parse(sp.get("from"));
          form.rooms = (q.rooms || []).map(Number); form.districts = q.districts || [];
          Object.assign(form.wishes, q.wishes || {});
          const f = $("pCard");
          if (q.price_min) f.budget_min.value = String(q.price_min / 1e6).replace(".", ",");
          if (q.price_max) f.budget_max.value = String(q.price_max / 1e6).replace(".", ",");
          if (q.wishes && q.wishes.area_min) f.area_min.value = q.wishes.area_min;
          if (q.wishes && q.wishes.area_max) f.area_max.value = q.wishes.area_max;
          renderFormChips();
          toast("Пожелания взяты из поиска — впишите имя и сохраните", 3500);
        } catch { /* */ }
      }
    }
    const fc = await call("/api/facets?deal=sale");
    complexes = (fc.ok && fc.data.complexes || []).map((x) => x.name);
    fillComplexes();
    const m = await call("/api/meta");
    districts = (m.ok && m.data.districts) || [];
    $("fDistrictAdd").innerHTML = districts.map((d) => `<option value="${esc(d)}"${form.districts.includes(d) ? " selected" : ""}>${esc(d)}</option>`).join("");
    if (window.Pick) {
      const f = $("pCard");
      Pick.multi($("fDistrictAdd"), { placeholder: "Выбрать районы" });
      Pick.multi($("fComplexes"), { placeholder: "Любые ЖК", searchPlaceholder: "Найти ЖК" });
      Pick.select($("mSort"));
      Pick.date(f.next_day, { clearable: true, placeholder: "Дата" });
      Pick.time(f.next_time, { placeholder: "Время" });
    }
  })();
})();
