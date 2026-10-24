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
  let mStage = "";  // телефон: какой этап показан под плашками этапов
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
    // телефон: все этапы видны сразу — плашками с числами; ниже — клиенты выбранного этапа (17.10)
    const narrow = innerWidth < 700;
    const stageKeys = Object.keys(data.stages);
    if (narrow && !stageKeys.includes(mStage)) mStage = stageKeys.find((k) => vis.some((c) => c.stage === k)) || stageKeys[0];
    $("cStageBar").hidden = !narrow || view !== "board" || !data.items.length;
    $("cStageBar").innerHTML = stageKeys.map((k) => `<button type="button" class="${k === mStage ? "on" : ""}" data-mstage="${k}" style="--c:${STAGE_C[k]}">
      <i></i>${esc(data.stages[k])}<b>${vis.filter((c) => c.stage === k).length}</b></button>`).join("");
    $("cBoard").classList.toggle("one", narrow);
    $("cBoard").innerHTML = Object.entries(data.stages).filter(([k]) => !narrow || k === mStage).map(([k, t]) => {
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
  let rz = 0;   // ширина окна поменялась (поворот телефона) — доска перестраивается: колонки или плашки этапов
  addEventListener("resize", () => { clearTimeout(rz); rz = setTimeout(() => { if (data.items.length) render(); }, 200); });
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
  // ─── обзор клиента: всё главное на одном экране, правка — кнопкой «Изменить» (17.10) ───
  function infoHTML() {   // 24.10c — по образцу Артёма (фото 89)
    if (!cur) { $("dInfo").innerHTML = ""; return; }
    const c = cur.client, w = c.wishes || {};
    const when = (ts) => new Date(ts * 1000).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
    const lbl = (list, keys) => (keys || []).map((k) => (list.find(([x]) => x === k) || [k, k])[1]);
    const types = lbl(W_TYPES, w.types).map((x) => x.toLowerCase());
    const big = [roomsTxt(c.rooms), types.join(" / ") || (c.rooms && c.rooms.length ? "квартира" : "")].filter(Boolean).join(" ");
    const area = w.area_min || w.area_max ? `${w.area_min ? `от ${w.area_min}` : ""} ${w.area_max ? `до ${w.area_max}` : ""} м²`.trim() : "";
    const sub = [budget(c) && `${budget(c)} ₽`, area].filter(Boolean).join(" · ");
    const chips = [...(c.districts || []), ...(w.complexes || []).map((x) => `ЖК ${x}`), w.not_first && "не первый этаж", w.not_last && "не последний этаж",
      ...lbl(W_COND, w.cond).map((x) => x.toLowerCase()), ...lbl(W_PAY, w.pay).map((x) => x.toLowerCase()),
      w.no_encumbrance && "без обременения", w.buy_by && `срок: ${w.buy_by}`].filter(Boolean);
    const items = cur.items || [], liked = items.filter((o) => ["liked", "show", "talks", "deal"].includes(o.status)).length;
    const shows = items.filter((o) => ["show", "talks", "deal"].includes(o.status)).length;
    const now = Date.now();
    const upcoming = (cur.events || []).filter((e) => !e.done && e.ts * 1000 >= now - 3600e3).sort((a, b) => a.ts - b.ts).slice(0, 3);
    const LOGI = { note: "note", stage: "layers", object: "home", event: "calendar", new: "user", next: "clock" };
    const recent = (cur.log || []).slice().sort((a, b) => b.ts - a.ts).slice(0, 4);
    const over = c.next_ts && c.next_ts * 1000 < now;
    const tile = (t, n) => `<button type="button" data-tab="items"><span>${t}</span><b>${n}</b></button>`;
    const mt = matchFor === c.id && matchTotal != null ? ` · ${num(matchTotal)}` : "";
    // «Этап: Новый → Подбор» — новое значение жирным
    const logText = (x) => esc(x.text).replace(/→\s*(.+)$/, "→ <b>$1</b>");
    $("dInfo").innerHTML = `
      <div class="ci-stats">${tile("Объектов", items.length)}${tile("Понравилось", liked)}${tile("Показов", shows)}${tile("Подборок", (cur.picks || []).length)}</div>
      <div class="ci-grid">
        <section class="ci-box ci-wish"><h4>Что ищет</h4>
          ${big || sub || chips.length ? `${big ? `<p class="ci-big">${esc(big)}</p>` : ""}${sub ? `<p class="ci-sub">${esc(sub)}</p>` : ""}
            ${chips.length ? `<div class="ci-chips">${chips.map((x) => `<span>${esc(x)}</span>`).join("")}</div>` : ""}`
            : `<p class="ci-empty">Пожелания не заполнены — без них сервис не подберёт объекты.</p>
              <div class="ci-acts"><button type="button" class="ci-btn" id="ciFill">${ic("edit")}Заполнить пожелания</button></div>`}
          <button type="button" class="ci-match" data-tab="items">Подходящие объекты<b id="ciMatchN">${mt}</b></button></section>
        <section class="ci-box ci-next${over ? " over" : ""}"><h4>Следующий шаг</h4>
          ${c.next_ts || c.next_text ? `<p class="ci-next-t">${esc(c.next_text || "Связаться")}</p>${c.next_ts ? `<p class="ci-hint${over ? " bad" : ""}">${esc(when(c.next_ts))}${over ? " · просрочено" : ""}</p>` : ""}`
            : `<p class="ci-next-t">Не задан</p><p class="ci-hint">Запланируйте, что и когда сделать — попадёт в «Дела» и в историю.</p>`}
          <div class="ci-acts3"><a class="ci-btn" href="/planner?add=show&client=${c.id}">${ic("home")}Показ</a>
            <a class="ci-btn" href="/planner?add=call&client=${c.id}">${ic("phone")}Созвон</a>
            <a class="ci-btn" href="/planner?add=meet&client=${c.id}">${ic("user")}Встреча</a></div>
          <div class="ci-up">${upcoming.length ? `<ul class="ci-list">${upcoming.map((e) => `<li><a href="/planner?event=${e.id}">${ic("calendar")}<span>${esc(e.title)}</span><small>${esc(when(e.ts))}</small></a></li>`).join("")}</ul>`
            : `<p class="ci-hint">Ближайших дел нет</p>`}</div></section>
        <section class="ci-box ci-last"><h4>Последнее <button type="button" class="ci-more" data-tab="log">Вся история ›</button></h4>
          ${recent.length ? `<ul class="ci-log">${recent.map((x) => `<li>${ic(LOGI[x.kind] || "note")}<span>${logText(x)}</span><small>${esc(when(x.ts))}</small></li>`).join("")}</ul>`
            : `<p class="ci-hint">Истории пока нет.</p>`}</section>
        ${["buyer", "tenant"].includes(c.kind) ? `<section class="ci-box ci-pub" id="ciPub"><h4>Запрос коллегам</h4><p class="ci-hint">Загружаем…</p></section>` : ""}
        ${c.note ? `<section class="ci-box"><h4>Заметка</h4><p class="ci-note">${esc(c.note)}</p></section>` : ""}
        <section class="ci-box ci-meta"><h4>О клиенте</h4><dl class="ci-dl">
          <dt>Кто</dt><dd>${esc(data.kinds[c.kind] || "")}</dd>
          ${c.phone ? `<dt>Телефон</dt><dd><a href="tel:${esc(c.phone)}">${esc(c.phone)}</a></dd>` : ""}
          ${c.source ? `<dt>Откуда</dt><dd>${esc(c.source)}</dd>` : ""}
          ${c.created ? `<dt>Добавлен</dt><dd>${esc(new Date(c.created * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "long" }))}</dd>` : ""}</dl></section>
      </div>`;
    if ($("ciPub")) { if (pubCid === c.id) pubHTML(); else { pubCid = c.id; pubState = null; } pubLoad(); }
    if (!matchSince && !$("dMatchList").dataset.loaded) match();   // число подходящих — сразу в обзоре
  }
  // ─── 19.10: «Запрос коллегам» — пожелания клиента видят все агенты с подпиской (без имени и номера клиента),
  // подтверждённым агентам с подходящими объектами приходит «Под ваш объект есть покупатель» ───
  let pubState = null, pubCid = 0;
  async function pubLoad() {
    const box = $("ciPub");
    if (!box || !cur) return;
    const cid = cur.client.id;
    const r = await call(`/api/clients/${cid}/publish`);
    if (!cur || cur.client.id !== cid) return;
    pubState = r.ok ? r.data.published : null;
    pubHTML();
  }
  function pubHTML(err) {
    const box = $("ciPub");
    if (!box) return;
    const p = pubState;
    const head = `<h4>Запрос коллегам</h4>`;
    if (p) {
      const d = new Date(p.last_seen * 1000), till = new Date((p.last_seen + 10 * 86400) * 1000);
      const f = (x) => x.toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
      box.innerHTML = `${head}<p class="ci-pub-on">Опубликован ${esc(f(d))} · виден до ${esc(f(till))}</p>
        <p class="ci-note">${esc(p.text)}</p>
        <div class="ci-acts"><a class="chip" href="/requests">${ic("search")}Смотреть в запросах</a>
          <button type="button" class="chip" data-pub="go">Обновить по пожеланиям</button>
          <button type="button" class="chip" data-pub="off">Снять</button></div>`;
      return;
    }
    box.innerHTML = `${head}<p class="ci-pub-lead">Покажите пожелания всем агентам 1+1 — у кого есть подходящий объект, позвонит вам.</p>
      <p class="ci-hint">Имя и номер клиента скрыты · запрос живёт 10 дней</p>
      <textarea id="ciPubNote" class="ci-pub-note" rows="2" maxlength="500" data-grow
        placeholder="Комментарий (необязательно): срочность, способ оплаты…"></textarea>
      ${err ? `<p class="ci-pub-err">${esc(err)}</p>` : ""}
      <button type="button" class="ci-pub-go" data-pub="go">Опубликовать запрос</button>`;
  }
  $("dInfo").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-pub]");
    if (!b || !cur) return;
    e.stopPropagation();
    b.disabled = true;
    const off = b.dataset.pub === "off";
    const note = $("ciPubNote");
    const r = await call(`/api/clients/${cur.client.id}/publish`, off ? { off: true } : { comment: note ? note.value : "" });
    b.disabled = false;
    if (!r.ok) {
      const msg = r.data.detail || "Не получилось — попробуйте ещё раз.";
      pubHTML(r.data.need_verify ? `${msg} Подтвердить можно в «Настройках».` : msg);
      return;
    }
    pubState = r.data.published;
    pubHTML();
    toast(off ? "Запрос снят" : "Запрос опубликован — коллеги с подходящими объектами получат уведомление");
  });
  function tab(name) {
    $("dEdit").hidden = !cur || name === "card";
    $("dBackInfo").hidden = !cur || name !== "card";
    document.querySelectorAll(".c-tabs [data-tab]").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
    document.querySelectorAll("#cDlg [data-pane]").forEach((p) => { p.hidden = p.dataset.pane !== name; });
    if (name === "items" && cur && !$("dMatchList").dataset.loaded) match();
  }
  const pl = (n, a, b, c) => (n % 10 === 1 && n % 100 !== 11 ? a : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? b : c);
  function stagesHTML() {   // 24.10c — по образцу: кружки с номерами на линии, пройденные — галочкой, «Отказ» справа
    const keys = Object.keys(data.stages), steps = keys.filter((k) => k !== "lost");
    $("dStages").hidden = !cur;
    $("dStages").innerHTML = `<ol class="c-step" style="--n:${steps.length}">${steps.map((k, i) => `<li><button type="button" data-stage="${k}">
        <i class="n">${i + 1}</i><span>${esc(data.stages[k])}</span></button></li>`).join("")}</ol>
      ${keys.includes("lost") ? `<button type="button" class="c-lost" data-stage="lost">${esc(data.stages.lost)}</button>` : ""}`;
    slideStage(cur ? cur.client.stage : steps[0], false);
  }
  function slideStage(stage) {
    const box = $("dStages"), steps = Object.keys(data.stages).filter((k) => k !== "lost");
    if (!box.querySelector(".c-step")) return;
    const lost = stage === "lost", at = lost ? -1 : steps.indexOf(stage);
    box.classList.toggle("is-lost", lost);
    box.querySelectorAll(".c-step li").forEach((li, i) => {
      li.classList.toggle("done", i < at);
      li.classList.toggle("on", i === at);
      li.querySelector(".n").innerHTML = i < at ? ic("check") : String(i + 1);
    });
    box.querySelector(".c-step").style.setProperty("--fill", at > 0 ? String(at / (steps.length - 1)) : "0");   // доля линии от 0 до 1
    const lb = box.querySelector(".c-lost");
    if (lb) lb.classList.toggle("on", lost);
    const on = box.querySelector(".c-step li.on");
    if (on && box.scrollWidth > box.clientWidth) box.scrollTo({ left: on.offsetLeft - box.clientWidth / 2 + on.offsetWidth / 2, behavior: "smooth" });
  }
  // окно меняет ширину (поворот телефона, раскрытие) — плашка этапа встаёт на место
  const stageRO = window.ResizeObserver ? new ResizeObserver(() => { if (cur) slideStage(cur.client.stage, false); }) : null;
  if (stageRO && $("dStages")) stageRO.observe($("dStages"));
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
    document.querySelectorAll('#pCard [name="budget_min"], #pCard [name="budget_max"]').forEach((i) => { i.dataset.money = rent ? "rent" : "sale"; i.placeholder = rent ? "например, 40" : "например, 5"; });
    if (window.moneyHints) window.moneyHints(document);
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
    const names = [...new Set([...chosen, ...complexes])].sort((a, b) => a.localeCompare(b, "ru", { sensitivity: "base", numeric: true }));   // 24.10g: по алфавиту
    sel.innerHTML = names.map((n) => `<option value="${esc(n)}"${chosen.includes(n) ? " selected" : ""}>${esc(n)}</option>`).join("");
    sel.dispatchEvent(new Event("pk-refresh"));
  }
  function header() {
    const c = cur ? cur.client : null;
    $("dName").textContent = c ? c.name : "Новый клиент";
    $("dAva").textContent = c ? (c.name.trim()[0] || "К").toUpperCase() : "+";
    $("dSub").textContent = c ? [data.kinds[c.kind], c.created && `добавлен ${new Date(c.created * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "long" })}`]
      .filter(Boolean).join(" · ") : "Заполните, кто это и что ищет — сервис подберёт объекты";
    const digits = c && c.phone ? c.phone.replace(/\D/g, "") : "";
    $("dActs").innerHTML = digits ? `<a href="tel:${esc(c.phone)}" aria-label="Позвонить">${ic("phone")}</a>
      <button type="button" class="c-max" data-max="${esc(c.phone)}" aria-label="Написать в MAX" title="Написать в MAX">MAX</button>` : "";
    $("dItemsN").textContent = cur && cur.items.length ? cur.items.length : "";
    $("dLogN").textContent = cur ? cur.log.length + (cur.work || []).length : "";
    $("dTasksN").textContent = cur ? cur.events.filter((e) => !e.done).length : "";
    document.querySelectorAll('.c-tabs [data-tab]:not([data-tab="card"])').forEach((b) => { b.hidden = !cur; });
  }
  function itemsHTML() {   // 24.10c: та же карточка, что в ленте главной; статус — выбором внизу
    const st = data.item_status;
    $("dItems").innerHTML = cur.items.length ? LCard.grid(cur.items.map((o) => LCard.html({ ...o, photos: o.photos || (o.photo ? [o.photo] : []) }, {
        top: `<span class="lc-tag gold">${esc(st[o.status] || o.status)}</span>`,
        extra: `${o.reaction ? `<p class="lc-line c-react r-${esc(o.reaction)}">Ответ клиента: ${esc(REACT[o.reaction] || "")}${o.reaction_text ? ` — «${esc(o.reaction_text)}»` : ""}</p>` : ""}
          ${(() => { const w = lastWork(o.id); return w ? `<p class="lc-line">${ic((WK[w.kind] || WK.note)[0])}${(WK[w.kind] || WK.note)[1]}${w.text ? ": " + esc(w.text.slice(0, 90)) : ""}</p>` : ""; })()}`,
        meta: "",
        foot: `<div class="lc-acts"><select data-item="${o.id}" aria-label="Статус">${Object.entries(st).map(([k, t]) => `<option value="${k}"${o.status === k ? " selected" : ""}>${t}</option>`).join("")}
          <option value="__del">Убрать</option></select></div>`,
      })).join(""), "sm")
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
  // отчёт по клиенту: что отправлено, что открыл, что понравилось, показы
  function reportHTML() {
    const it = cur.items, n = (f) => it.filter(f).length;
    const sent = n((o) => o.status !== "matched"), opened = n((o) => o.opened_at || ["opened", "liked", "rejected", "show", "talks", "deal"].includes(o.status));
    const liked = n((o) => o.status === "liked" || o.reaction === "like" || o.reaction === "want_show"), no = n((o) => o.status === "rejected");
    const shows = n((o) => ["show", "talks", "deal"].includes(o.status)) + (cur.events || []).filter((e) => e.kind === "show").length;
    // 24.10b: цифры видны всегда (и нули) — как на образце; «Скопировать отчёт» — когда есть объекты
    const cell = (v, t, c = "") => `<span class="c-rc ${c}${v ? "" : " zero"}"><b>${v}</b><small>${t}</small></span>`;
    $("dReport").innerHTML = `<div class="c-rep">${cell(sent, "отправлено")}${cell(opened, "открыл")}${cell(liked, "понравилось", "ok")}${cell(no, "не подходит", "bad")}${cell(shows, "показов", "gold")}</div>
      ${it.length ? `<button type="button" class="chip c-rep-copy" data-report-copy>${ic("note")}Скопировать отчёт</button>` : ""}`;
  }
  function reportText() {
    const label = data.item_status;
    const lines = cur.items.map((o) => `• ${o.complex ? `ЖК ${o.complex}, ` : ""}${o.title} — ${money(o.price, o.deal)} ₽: ${(label[o.status] || o.status).toLowerCase()}`
      + (o.reaction ? ` (ответ: ${REACT[o.reaction]}${o.reaction_text ? ` — «${o.reaction_text}»` : ""})` : ""));
    return `Клиент: ${cur.client.name}
Этап: ${data.stages[cur.client.stage]}
Объекты (${cur.items.length}):
${lines.join("\n")}`;
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
      itemsHTML(); upgradeItems(); logHTML(); tasksHTML(); picksHTML(); reportHTML();
    } else cur = null;
    header(); stagesHTML(); fillForm(); infoHTML(); tab(cur ? "info" : "card");   // клиент — сначала обзор; новый — форма
    if (!$("cDlg").open) $("cDlg").showModal();
    history.replaceState(null, "", id ? `/clients?id=${id}` : "/clients");
  }
  async function refreshCur() {
    const r = await call(`/api/clients?id=${cur.client.id}`);
    if (r.ok) { cur = r.data; header(); stagesHTML(); itemsHTML(); upgradeItems(); logHTML(); tasksHTML(); picksHTML(); reportHTML(); infoHTML(); }
  }
  let matchPage = 1, matchSince = 0, freshIds = [];   // matchSince — из уведомления «новое под клиента»
  let mSort = "new", mPhoto = false, mNotFirst = false, showHidden = false, matchFor = 0, matchTotal = null;
  function matchItem(o) {   // 24.10c: общей карточкой, галочка выбора — справа сверху на фото
    const diff = o.market_diff != null && o.market_diff < -2 ? `<span class="lc-tag ok">ниже рынка ${Math.abs(o.market_diff)}%</span>` : "";
    return LCard.html(o, {
      cls: "c-mit",
      top: diff,
      sel: showHidden ? "" : `<label class="lc-sel" title="Отметить"><input type="checkbox" data-msel="${o.id}" aria-label="Отметить"></label>`,
      meta: "",
      foot: `<div class="lc-acts">${showHidden ? `<button type="button" class="lc-btn main" data-unhide="${o.id}">Вернуть</button>`
        : `<button type="button" class="lc-btn main" data-offer="${o.id}">${ic("folder")}В подборку</button>
          <button type="button" class="lc-btn x" data-nope="${o.id}" title="Не подходит — больше не предлагать" aria-label="Не подходит">${ic("x")}</button>`}</div>`,
    });
  }
  const selected = () => [...document.querySelectorAll("[data-msel]:checked")].map((x) => Number(x.dataset.msel));
  function selBar() {
    const n = selected().length;
    $("dMbar").hidden = showHidden || !$("dMatchList").querySelector("[data-msel]");
    $("mSel").textContent = n ? `отмечено: ${n}` : "";
    $("mToPick").disabled = $("mNo").disabled = !n;
    $("mAll").checked = n && n === document.querySelectorAll("[data-msel]").length;
    document.querySelectorAll("[data-msel]").forEach((x) => { const c = x.closest(".lc"); if (c) c.classList.toggle("picked", x.checked); });
  }
  async function sendToPick(ids) {
    const r = await call(`/api/clients/${cur.client.id}/topick`, { ids });
    if (!r.ok) { toast(r.data.detail || "Не получилось"); return; }
    try { await navigator.clipboard.writeText(r.data.pick.url); } catch { /* */ }
    window.pickSent && window.pickSent(r.data.pick.url);
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
    window.pickSent && window.pickSent(url);
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
      if (!showHidden && !matchSince) { matchFor = cur.client.id; matchTotal = d.total || 0;
        if ($("ciMatchN")) $("ciMatchN").textContent = ` · ${num(matchTotal)}`; }
      const hb = $("mHidden");
      const hn = showHidden ? 0 : (d.hidden || 0);
      hb.hidden = !showHidden && !hn;
      hb.textContent = showHidden ? "← К подбору" : `Скрытые · ${hn}`;
      box.innerHTML = d.items.length ? "" : `<p class="g-empty">${wish(cur.client) ? "Сейчас подходящих нет — уточните пожелания или загляните позже." : "Укажите в карточке комнаты, бюджет или районы — и сервис подберёт объекты."}</p>`;
    }
    const fb = $("dFresh");
    if (matchSince && !more && !showHidden) {   // 24.10b: новые с прошлой проверки — отдельным блоком В САМОМ ВЕРХУ вкладки
      const when = new Date(matchSince * 1000).toLocaleString("ru-RU", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });
      fb.hidden = false;
      fb.innerHTML = `<div class="c-fresh">${ic("sparkle")}<span><b>Новые объекты для ${esc(cur.client.name)}: ${d.items.length}</b>
          <small>Появились с ${esc(when)} и подходят под пожелания клиента</small></span>
          ${d.items.length ? `<button type="button" class="pill" data-topick>Всё в подборку</button>` : ""}
          <button type="button" class="chip" data-all-match>Показать все подходящие</button></div>
        ${d.items.length ? LCard.grid(d.items.map(matchItem).join(""), "sm") : `<p class="g-empty">Новых пока нет — все подходящие ниже.</p>`}`;
      box.innerHTML = `<p class="g-empty">Новые объекты — наверху. «Показать все подходящие» — весь подбор здесь.</p>`;
      requestAnimationFrame(() => fb.scrollIntoView({ block: "start", behavior: "smooth" }));
      selBar();
      return;
    }
    if (!matchSince && fb) { fb.hidden = true; fb.innerHTML = ""; }
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
    if (t.id === "dEdit") { tab("card"); return; }
    if (t.id === "dBackInfo") { tab("info"); return; }
    if (t.dataset.mstage) { mStage = t.dataset.mstage; render(); return; }
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
    if (t.dataset.nope) { t.closest(".lc, .c-it").remove(); hideIds([Number(t.dataset.nope)]); return; }
    if (t.dataset.unhide) { t.closest(".lc, .c-it").remove(); hideIds([Number(t.dataset.unhide)], false); return; }
    if (t.id === "ciFill") { tab("card"); return; }
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
    if (t.hasAttribute("data-report-copy")) {
      const txt = reportText();
      try { await navigator.clipboard.writeText(txt); toast("Отчёт скопирован — можно вставить в сообщение"); } catch { prompt("Скопируйте отчёт:", txt); }
      return;
    }
    if (t.dataset.copyPick) {
      try { await navigator.clipboard.writeText(t.dataset.copyPick); toast("Ссылка скопирована — отправьте клиенту"); window.pickSent && window.pickSent(t.dataset.copyPick); }
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
    header(); stagesHTML(); itemsHTML(); upgradeItems(); logHTML(); tasksHTML(); infoHTML();
    load();
    toast(isNew ? "Клиент добавлен — подберите объекты" : "Сохранено");
    if (isNew) { history.replaceState(null, "", `/clients?id=${cur.client.id}`); tab("items"); } else tab("info");
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
    districts.sort((a, b) => a.localeCompare(b, "ru", { sensitivity: "base" }));   // 24.10g: по алфавиту
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
