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
  let form = { kind: "buyer", rooms: [], districts: [] };

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
    return `<button type="button" class="c-card" draggable="true" data-open="${c.id}">
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

  // перетаскивание карточки в другой этап
  let dragId = null;
  document.addEventListener("dragstart", (e) => {
    const c = e.target.closest(".c-card");
    if (!c) return;
    dragId = Number(c.dataset.open);
    c.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
  });
  document.addEventListener("dragend", (e) => { const c = e.target.closest(".c-card"); if (c) c.classList.remove("dragging"); });
  document.addEventListener("dragover", (e) => {
    const col = e.target.closest(".c-col");
    if (!col || dragId == null) return;
    e.preventDefault();
    document.querySelectorAll(".c-col.drop").forEach((x) => x !== col && x.classList.remove("drop"));
    col.classList.add("drop");
  });
  document.addEventListener("drop", async (e) => {
    const col = e.target.closest(".c-col");
    document.querySelectorAll(".c-col.drop").forEach((x) => x.classList.remove("drop"));
    if (!col || dragId == null) return;
    e.preventDefault();
    const id = dragId, stage = col.dataset.stage;
    dragId = null;
    const c = data.items.find((x) => x.id === id);
    if (!c || c.stage === stage) return;
    c.stage = stage;
    render();
    const r = await call("/api/clients", { id, stage });
    if (!r.ok) { toast("Не получилось перенести"); load(); } else toast(`${c.name} → ${data.stages[stage]}`);
  });

  // ─── карточка клиента ──────────────────────────────────────────────
  function tab(name) {
    document.querySelectorAll(".c-tabs [data-tab]").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
    document.querySelectorAll("#cDlg [data-pane]").forEach((p) => { p.hidden = p.dataset.pane !== name; });
    if (name === "items" && cur && !$("dMatchList").dataset.loaded) match();
  }
  function stagesHTML() {
    const keys = Object.keys(data.stages), at = cur ? keys.indexOf(cur.client.stage) : 0;
    $("dStages").hidden = !cur;
    $("dStages").innerHTML = keys.map((k, i) => `<button type="button" data-stage="${k}" class="${i === at ? "on" : i < at && k !== "lost" ? "past" : ""}"
      style="--c:${STAGE_C[k]}">${esc(data.stages[k])}</button>`).join("");
  }
  function fillForm() {
    const c = cur ? cur.client : { kind: "buyer", rooms: [], districts: [], deal: "sale" };
    form = { kind: c.kind, rooms: [...(c.rooms || [])], districts: [...(c.districts || [])] };
    const f = $("pCard");
    f.name.value = c.name || ""; f.phone.value = c.phone || "";
    const rent = c.deal === "rent" || ["tenant", "landlord"].includes(c.kind);
    const unit = rent ? 1000 : 1e6;
    f.budget_min.value = c.budget_min ? String(+(c.budget_min / unit).toFixed(2)).replace(".", ",") : "";
    f.budget_max.value = c.budget_max ? String(+(c.budget_max / unit).toFixed(2)).replace(".", ",") : "";
    f.next_text.value = c.next_text || ""; f.source.value = c.source || ""; f.note.value = c.note || "";
    f.next_date.value = c.next_ts ? new Date(c.next_ts * 1000 - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16) : "";
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
    $("fDistricts").innerHTML = form.districts.map((d) => `<button type="button" class="chip on" data-fdel="${esc(d)}">${esc(d)} ✕</button>`).join("");
  }
  function header() {
    const c = cur ? cur.client : null;
    $("dName").textContent = c ? c.name : "Новый клиент";
    $("dAva").textContent = c ? (c.name.trim()[0] || "К").toUpperCase() : "+";
    $("dSub").textContent = c ? [data.kinds[c.kind], c.phone].filter(Boolean).join(" · ") : "Заполните, кто это и что ищет — сервис подберёт объекты";
    const digits = c && c.phone ? c.phone.replace(/\D/g, "") : "";
    $("dActs").innerHTML = digits ? `<a href="tel:${esc(c.phone)}" aria-label="Позвонить">${ic("phone")}</a>
      <a href="https://wa.me/${digits}" target="_blank" rel="noopener" aria-label="WhatsApp">${ic("chat")}</a>` : "";
    $("dItemsN").textContent = cur && cur.items.length ? cur.items.length : "";
    document.querySelectorAll('.c-tabs [data-tab]:not([data-tab="card"])').forEach((b) => { b.hidden = !cur; });
  }
  function itemsHTML() {
    const st = data.item_status;
    $("dItems").innerHTML = cur.items.length ? cur.items.map((o) => `<div class="c-it${o.is_active ? "" : " off"}">
        ${o.photo ? `<img src="${esc(o.photo)}" alt="" loading="lazy">` : `<span class="noimg"></span>`}
        <div><a href="/?open=${o.id}" target="_blank">${esc(o.complex ? `ЖК ${o.complex}` : o.title)}</a>
          <small>${esc(o.title)} · ${esc(money(o.price, o.deal))} ₽${o.is_active ? "" : " · снят"}</small></div>
        <select data-item="${o.id}" aria-label="Статус">${Object.entries(st).map(([k, t]) => `<option value="${k}"${o.status === k ? " selected" : ""}>${t}</option>`).join("")}
          <option value="__del">Убрать</option></select></div>`).join("")
      : `<p class="g-empty">Объектов пока нет. Нажмите «Подобрать» — сервис найдёт подходящие по пожеланиям, или добавьте из окна объекта кнопкой «Клиенту».</p>`;
  }
  function logHTML() {
    const ico = { note: "note", stage: "layers", object: "home", event: "calendar", new: "user", next: "clock" };
    $("dLog").innerHTML = cur.log.map((x) => `<li>${ic(ico[x.kind] || "note")}<span>${esc(x.text)}</span>
      <small>${new Date(x.ts * 1000).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</small></li>`).join("");
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
      itemsHTML(); logHTML(); tasksHTML();
    } else cur = null;
    header(); stagesHTML(); fillForm(); tab("card");
    if (!$("cDlg").open) $("cDlg").showModal();
    history.replaceState(null, "", id ? `/clients?id=${id}` : "/clients");
  }
  async function refreshCur() {
    const r = await call(`/api/clients?id=${cur.client.id}`);
    if (r.ok) { cur = r.data; header(); stagesHTML(); itemsHTML(); logHTML(); tasksHTML(); }
  }
  async function match() {
    $("dMatchList").dataset.loaded = "1";
    $("dMatchList").innerHTML = `<p class="g-empty">Ищем…</p>`;
    const r = await call(`/api/clients/${cur.client.id}/match`);
    const items = r.ok ? r.data.items : [];
    $("dMatchList").innerHTML = items.length ? items.map((o) => `<div class="c-it">
        ${o.photos && o.photos[0] ? `<img src="${esc(o.photos[0])}" alt="" loading="lazy">` : `<span class="noimg"></span>`}
        <div><a href="/?open=${o.id}" target="_blank">${esc(o.complex ? `ЖК ${o.complex}` : o.title)}</a>
          <small>${esc(o.title)} · ${esc(money(o.price, o.deal))} ₽${o.district ? ` · ${esc(o.district)}` : ""}</small></div>
        <button type="button" class="pill light" data-offer="${o.id}">Предложить</button></div>`).join("")
      : `<p class="g-empty">${wish(cur.client) ? "Сейчас подходящих нет — уточните пожелания или загляните позже." : "Укажите в карточке комнаты, бюджет или районы — и сервис подберёт объекты."}</p>`;
  }

  // ─── события ───────────────────────────────────────────────────────
  document.addEventListener("click", async (e) => {
    const t = e.target.closest("[data-open], [data-new], [data-kind], [data-view], [data-tab], [data-stage], [data-fkind], [data-froom], [data-fdel], [data-offer], button");
    if (!t) return;
    if (t.dataset.open && !t.closest("dialog")) { open(Number(t.dataset.open)); return; }
    if (t.dataset.new !== undefined || t.id === "cNew") { open(null); return; }
    if (t.dataset.kind !== undefined && !t.closest("dialog")) { kindFilter = t.dataset.kind; render(); return; }
    if (t.dataset.view) { view = t.dataset.view; document.querySelectorAll("[data-view]").forEach((b) => b.classList.toggle("on", b === t)); render(); return; }
    if (t.dataset.tab) { tab(t.dataset.tab); return; }
    if (t.dataset.stage && t.closest("#dStages") && cur) {
      const r = await call("/api/clients", { id: cur.client.id, stage: t.dataset.stage });
      if (r.ok) { await refreshCur(); load(); toast(`Этап: ${data.stages[t.dataset.stage]}`); }
      return;
    }
    if (t.dataset.fkind) { form.kind = t.dataset.fkind; renderFormChips(); return; }
    if (t.dataset.froom !== undefined) { const k = Number(t.dataset.froom); form.rooms = form.rooms.includes(k) ? form.rooms.filter((x) => x !== k) : [...form.rooms, k]; renderFormChips(); return; }
    if (t.dataset.fdel) { form.districts = form.districts.filter((x) => x !== t.dataset.fdel); renderFormChips(); return; }
    if (t.dataset.offer) {
      const r = await call(`/api/clients/${cur.client.id}/items`, { listing_id: Number(t.dataset.offer), status: "offered" });
      if (r.ok) { t.closest(".c-it").remove(); await refreshCur(); load(); toast("Объект добавлен клиенту"); }
      return;
    }
    if (t.id === "dMatch") { match(); return; }
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
      note: f.note.value, next_ts: f.next_date.value ? Math.floor(new Date(f.next_date.value).getTime() / 1000) : null,
      stage: cur ? cur.client.stage : "new", archived: cur ? cur.client.archived : 0 };
    if (cur) body.id = cur.client.id;
    const r = await call("/api/clients", body);
    if (!r.ok) { $("fErr").textContent = r.data.detail || "Не получилось сохранить"; $("fErr").hidden = false; return; }
    const isNew = !cur;
    cur = r.data;
    header(); stagesHTML(); itemsHTML(); logHTML(); tasksHTML();
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
  // район: пишем — подсказываем из справочника
  $("fDistrictAdd").addEventListener("change", (e) => {
    const v = e.target.value.trim();
    const hit = districts.find((d) => d.toLowerCase() === v.toLowerCase()) || districts.find((d) => d.toLowerCase().startsWith(v.toLowerCase()));
    if (hit && !form.districts.includes(hit)) { form.districts.push(hit); renderFormChips(); }
    e.target.value = "";
  });

  document.querySelectorAll("[data-ic]").forEach((el) => { el.outerHTML = ic(el.dataset.ic); });
  (async () => {
    if (!(await load())) return;
    const sp = new URLSearchParams(location.search);
    if (Number(sp.get("id"))) open(Number(sp.get("id")));
    else if (sp.has("new")) open(null);
    const m = await call("/api/meta");
    districts = (m.ok && m.data.districts) || [];
    const dl = document.createElement("datalist");
    dl.id = "districtList";
    dl.innerHTML = districts.map((d) => `<option value="${esc(d)}">`).join("");
    document.body.appendChild(dl);
    $("fDistrictAdd").setAttribute("list", "districtList");
  })();
})();
