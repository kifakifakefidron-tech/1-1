/* Планер: показы, созвоны, встречи, задачи + свои заметки. API: /api/planner, /api/planner/notes.
   Ссылки: ?add=show&listing=ID — новое событие с объектом; ?event=ID — открыть событие (из уведомления). */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const KINDS = { show: "🏠 Показ", call: "📞 Созвон", meet: "🤝 Встреча", task: "✅ Задача" };
  const STATUSES = { call: "📞 Звонил", show: "👀 Показ", think: "🤔 Думает", refuse: "❌ Отказ", deal: "✅ Сделка" };
  const MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];
  const WD = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"];
  let data = null, month = new Date(), pickedDay = "", dayEvents = null, editing = null, kind = "show";
  month.setDate(1);

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
  const pad = (n) => String(n).padStart(2, "0");
  const dayKey = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const time = (ts) => new Date(ts * 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  const money = (p, deal) => (!p ? "" : deal === "rent" ? `${Math.round(p / 1000).toLocaleString("ru-RU")} тыс. ₽/мес`
    : p >= 1e6 ? `${(p / 1e6).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} млн ₽` : `${p.toLocaleString("ru-RU")} ₽`);
  function dayTitle(d) {
    const t = new Date(); t.setHours(0, 0, 0, 0);
    const x = new Date(d); x.setHours(0, 0, 0, 0);
    const diff = Math.round((x - t) / 86400000);
    const name = x.toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" });
    return diff === 0 ? `Сегодня, ${name}` : diff === 1 ? `Завтра, ${name}` : diff === -1 ? `Вчера, ${name}` : name[0].toUpperCase() + name.slice(1);
  }
  const place = (l) => [l.complex && `ЖК ${l.complex}`, l.street && `ул. ${l.street}${l.house ? ", " + l.house : ""}`, l.district]
    .filter(Boolean).join(" · ");

  // ─── загрузка ───
  async function load() {
    const r = await call("/api/planner");
    if (r.status === 401) { $("plGuest").hidden = false; return false; }
    data = r.data;
    $("plApp").hidden = false;
    renderMonth(); renderAgenda(); renderNotes();
    return true;
  }

  // ─── календарь на месяц ───
  function renderMonth() {
    $("plMonthName").textContent = `${MONTHS[month.getMonth()]} ${month.getFullYear()}`;
    const first = new Date(month); const shift = (first.getDay() + 6) % 7;
    const start = new Date(first); start.setDate(1 - shift);
    const today = dayKey(new Date());
    let html = WD.map((w) => `<span class="pl-wd">${w}</span>`).join("");
    for (let i = 0; i < 42; i++) {
      const d = new Date(start); d.setDate(start.getDate() + i);
      const k = dayKey(d);
      const kinds = (data.days[k] || []);
      const dots = [...new Set(kinds)].slice(0, 4).map((x) => `<i class="k-${x}"></i>`).join("");
      html += `<button type="button" class="pl-day${d.getMonth() !== month.getMonth() ? " out" : ""}${k === today ? " today" : ""}${k === pickedDay ? " on" : ""}"
        data-day="${k}"><span>${d.getDate()}</span><em>${dots}</em>${kinds.length > 1 ? `<small>${kinds.length}</small>` : ""}</button>`;
    }
    $("plGrid").innerHTML = html;
  }

  // ─── список дел ───
  function evHTML(e, showDate) {
    const l = e.listing;
    const phone = e.contact_phone ? e.contact_phone.replace(/\D/g, "") : "";
    return `<article class="pl-ev k-${e.kind}${e.done ? " done" : ""}" data-ev="${e.id}">
      <div class="pl-ev-time"><b>${time(e.ts)}</b>${showDate ? `<small>${new Date(e.ts * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "short" })}</small>` : ""}</div>
      <div class="pl-ev-main">
        <div class="pl-ev-kind">${KINDS[e.kind] || ""}${e.remind_min && !e.done ? ` <span title="Напомним заранее">🔔</span>` : ""}</div>
        <h3>${esc(e.title)}</h3>
        ${l ? `<a class="pl-obj" href="/?open=${l.id}" target="_blank">${l.photo ? `<img src="${esc(l.photo)}" alt="" loading="lazy">` : ""}
          <span><b>${esc(l.title)}</b>${esc(money(l.price, l.deal))}${l.is_active ? "" : " · снят с сайта"}<small>${esc(place(l))}</small></span></a>` : ""}
        ${e.contact_name || e.contact_phone ? `<div class="pl-contact">${esc(e.contact_name || "")}
          ${phone ? `<a href="tel:${esc(e.contact_phone)}">${esc(e.contact_phone)}</a><a href="https://wa.me/${phone}" target="_blank" rel="noopener">WhatsApp</a>` : ""}</div>` : ""}
        ${e.place ? `<div class="pl-place">📍 ${esc(e.place)}</div>` : ""}
        ${e.note ? `<p class="pl-note">${esc(e.note)}</p>` : ""}
        <div class="pl-ev-acts">
          <button type="button" class="chip${e.done ? " on" : ""}" data-done="${e.id}">${e.done ? "✓ Готово" : "Отметить готовым"}</button>
          <button type="button" class="chip" data-edit="${e.id}">✎ Изменить</button>
          <a class="chip" href="/api/planner/${e.id}.ics" title="Скачать в календарь телефона">📅 В календарь</a>
        </div>
      </div></article>`;
  }
  function groupByDay(list) {
    const out = [];
    for (const e of list) {
      const k = dayKey(new Date(e.ts * 1000));
      if (!out.length || out[out.length - 1].k !== k) out.push({ k, ts: e.ts, items: [] });
      out[out.length - 1].items.push(e);
    }
    return out;
  }
  function renderAgenda() {
    const empty = `<div class="pl-empty"><b>Пока ничего не запланировано</b>Нажмите «+ Показ» или «+ Созвон» вверху —
      или «📅 Запланировать» в окне объекта из избранного.</div>`;
    if (pickedDay) {
      const list = dayEvents || [];
      $("plAgendaTitle").textContent = dayTitle(new Date(`${pickedDay}T12:00`));
      $("plAllDays").hidden = false;
      $("plAgenda").innerHTML = list.length ? list.map((e) => evHTML(e)).join("")
        : `<div class="pl-empty">В этот день ничего нет. <button type="button" class="link" data-add-day="${pickedDay}">+ Добавить</button></div>`;
      return;
    }
    $("plAgendaTitle").textContent = "Ближайшее";
    $("plAllDays").hidden = true;
    let html = "";
    if (data.overdue.length) {
      html += `<h3 class="pl-day-h overdue">Просрочено · ${data.overdue.length}</h3>` + data.overdue.map((e) => evHTML(e, true)).join("");
    }
    for (const g of groupByDay(data.upcoming)) html += `<h3 class="pl-day-h">${esc(dayTitle(new Date(g.ts * 1000)))}</h3>` + g.items.map((e) => evHTML(e)).join("");
    $("plAgenda").innerHTML = html || empty;
  }
  async function pickDay(k) {
    pickedDay = pickedDay === k ? "" : k;
    renderMonth();
    if (pickedDay) {
      const from = Math.floor(new Date(`${k}T00:00`).getTime() / 1000);
      const r = await call(`/api/planner?from=${from}&to=${from + 86400}`);
      dayEvents = r.ok ? r.data.events : [];
    }
    renderAgenda();
  }
  const allEvents = () => [...data.overdue, ...data.upcoming, ...(dayEvents || [])];

  // ─── окно события ───
  function fillListings(selected) {
    const fav = data.favorites || [];
    $("evListing").innerHTML = `<option value="">— без объекта —</option>` + fav.map((l) =>
      `<option value="${l.id}"${l.id === selected ? " selected" : ""}>${esc(l.title)}${l.price ? " · " + esc(money(l.price, l.deal)) : ""}${l.complex ? " · ЖК " + esc(l.complex) : ""}${l.status ? " · " + STATUSES[l.status] : ""}</option>`).join("")
      + (fav.length ? "" : `<option value="" disabled>В избранном пока пусто — добавьте объект ♡</option>`);
    showObj();
  }
  function showObj() {
    const id = Number($("evListing").value);
    const l = (data.favorites || []).find((x) => x.id === id);
    $("evObj").innerHTML = l ? `<a class="pl-obj" href="/?open=${l.id}" target="_blank">${l.photo ? `<img src="${esc(l.photo)}" alt="">` : ""}
      <span><b>${esc(l.title)}</b>${esc(money(l.price, l.deal))}<small>${esc(place(l))}</small></span></a>` : "";
  }
  function renderKinds() {
    $("evKinds").innerHTML = Object.entries(KINDS).map(([k, v]) => `<button type="button" class="chip${k === kind ? " on" : ""}" data-kind="${k}">${v}</button>`).join("");
  }
  function quick() {
    const t = new Date();
    const days = [["Сегодня", 0], ["Завтра", 1], ["Послезавтра", 2]].map(([l, n]) => {
      const d = new Date(t); d.setDate(t.getDate() + n); return `<button type="button" class="chip" data-qdate="${dayKey(d)}">${l}</button>`;
    });
    const hours = ["10:00", "12:00", "15:00", "18:00"].map((h) => `<button type="button" class="chip" data-qtime="${h}">${h}</button>`);
    $("evQuick").innerHTML = days.join("") + hours.join("");
  }
  async function openEvent(ev, preset = {}) {
    editing = ev || null;
    const f = $("evForm");
    f.reset();
    $("evErr").hidden = true;
    kind = ev ? ev.kind : preset.kind || "show";
    renderKinds(); quick();
    $("evTitle").textContent = ev ? "Изменить событие" : "Новое событие";
    $("evDel").hidden = !ev;
    const base = ev ? new Date(ev.ts * 1000) : (() => {
      const d = preset.day ? new Date(`${preset.day}T10:00`) : new Date(Date.now() + 3600 * 1000);
      if (!preset.day) d.setMinutes(0, 0, 0);
      return d;
    })();
    f.date.value = dayKey(base);
    f.time.value = `${pad(base.getHours())}:${pad(base.getMinutes())}`;
    if (ev) {
      for (const k of ["title", "contact_name", "contact_phone", "place", "note"]) f[k].value = ev[k] || "";
      f.dur_min.value = String(ev.dur_min || 60);
      f.remind_min.value = String(ev.remind_min ?? 60);
    }
    let lid = ev ? ev.listing_id : preset.listing || null;
    if (lid && !(data.favorites || []).some((x) => x.id === lid)) {   // объект не из избранного (пришли из окна объекта)
      const r = await call(`/api/listings/${lid}`);
      if (r.ok) data.favorites = [{ ...r.data, photo: (r.data.photos || [])[0] }, ...(data.favorites || [])];
      else lid = null;
    }
    fillListings(lid);
    if (!ev && lid) await prefillFromListing(lid);
    const dlg = $("evDlg");
    if (!dlg.open) dlg.showModal();
  }
  // Объект выбран — подставить заголовок, место и номер агента (если номер виден по подписке)
  async function prefillFromListing(id) {
    const f = $("evForm");
    const l = (data.favorites || []).find((x) => x.id === id);
    if (!l) return;
    if (!f.title.value) f.title.value = `${KINDS[kind].split(" ").slice(1).join(" ")}: ${l.title}`;
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
      remind_min: Number(f.remind_min.value), done: editing ? editing.done : 0 };
    if (editing) body.id = editing.id;
    const r = await call("/api/planner", body);
    if (!r.ok) { $("evErr").textContent = r.data.detail || "Не получилось сохранить."; $("evErr").hidden = false; return; }
    $("evDlg").close();
    toast(editing ? "✓ Сохранено" : `✓ Запланировано на ${new Date(ts * 1000).toLocaleString("ru-RU", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" })}`, 3000);
    await reload();
  }
  async function reload() {
    const r = await call("/api/planner");
    if (r.ok) data = r.data;
    if (pickedDay) { const k = pickedDay; pickedDay = ""; await pickDay(k); } else { renderMonth(); renderAgenda(); }
    renderNotes();
  }

  // ─── заметки ───
  function renderNotes() {
    $("plNoteList").innerHTML = data.notes.map((n) => `<div class="pl-n${n.pinned ? " pinned" : ""} c-${n.color || "none"}" data-note="${n.id}">
      <textarea rows="4" maxlength="5000" data-note-text="${n.id}">${esc(n.text)}</textarea>
      <div class="pl-n-acts">
        <button type="button" class="pl-n-pin${n.pinned ? " on" : ""}" data-pin="${n.id}" title="${n.pinned ? "Открепить" : "Закрепить сверху"}">📌</button>
        ${["", "yellow", "green", "red", "blue"].map((c) => `<button type="button" class="pl-n-color c-${c || "none"}${(n.color || "") === c ? " on" : ""}" data-color="${c}" data-id="${n.id}" aria-label="Цвет"></button>`).join("")}
        <small>${new Date(n.updated * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "short" })}</small>
        <button type="button" class="pl-n-del" data-ndel="${n.id}" title="Удалить">🗑</button>
      </div></div>`).join("") || `<p class="note">Своих заметок пока нет.</p>`;
    $("plObjNotes").innerHTML = data.object_notes.map((n) => `<li><a href="/?open=${n.listing_id}" target="_blank">${esc(n.title)}</a>
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

  // ─── события ───
  document.addEventListener("click", async (e) => {
    const t = e.target.closest("button, [data-day]");
    if (!t) return;
    if (t.dataset.add) { openEvent(null, { kind: t.dataset.add, day: pickedDay || "" }); return; }
    if (t.dataset.addDay) { openEvent(null, { day: t.dataset.addDay }); return; }
    if (t.dataset.month) { month.setMonth(month.getMonth() + Number(t.dataset.month)); renderMonth(); return; }
    if (t.dataset.day) { pickDay(t.dataset.day); return; }
    if (t.id === "plAllDays") { pickedDay = ""; dayEvents = null; renderMonth(); renderAgenda(); return; }
    if (t.dataset.tab) {
      document.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("on", b === t));
      $("plCal").hidden = t.dataset.tab !== "cal"; $("plNotes").hidden = t.dataset.tab !== "notes"; return;
    }
    if (t.dataset.kind) { kind = t.dataset.kind; renderKinds(); return; }
    if (t.dataset.qdate) { $("evForm").date.value = t.dataset.qdate; return; }
    if (t.dataset.qtime) { $("evForm").time.value = t.dataset.qtime; return; }
    if (t.dataset.edit) { openEvent(allEvents().find((x) => x.id === Number(t.dataset.edit))); return; }
    if (t.dataset.done) {
      const ev = allEvents().find((x) => x.id === Number(t.dataset.done));
      const done = !(ev && ev.done);
      await call("/api/planner", { id: Number(t.dataset.done), done });
      toast(done ? "✓ Готово" : "Снова в планах", 1600);
      await reload(); return;
    }
    if (t.id === "evDel" && editing) {
      if (!confirm("Удалить событие?")) return;
      await call("/api/planner", { id: editing.id }, "DELETE");
      $("evDlg").close(); toast("Удалено"); await reload(); return;
    }
    if (t.hasAttribute("data-close")) { t.closest("dialog").close(); return; }
    if (t.id === "plPhone") {
      const url = `${location.origin}${data.feed}`;
      $("plFeed").value = url;
      $("plWebcal").href = url.replace(/^https?:/, "webcal:");
      $("phoneDlg").showModal(); return;
    }
    if (t.id === "plFeedCopy") {
      try { await navigator.clipboard.writeText($("plFeed").value); toast("✓ Ссылка скопирована"); }
      catch { $("plFeed").select(); document.execCommand("copy"); toast("✓ Ссылка скопирована"); }
      return;
    }
    if (t.dataset.pin) { const n = data.notes.find((x) => x.id === Number(t.dataset.pin)); await saveNote(n.id, { pinned: n.pinned ? 0 : 1 }); await reload(); return; }
    if (t.dataset.color !== undefined && t.dataset.id) { await saveNote(Number(t.dataset.id), { color: t.dataset.color }); renderNotes(); return; }
    if (t.dataset.ndel) {
      if (!confirm("Удалить заметку?")) return;
      await saveNote(Number(t.dataset.ndel), { text: "" }); renderNotes(); return;
    }
  });
  $("evListing").addEventListener("change", () => { showObj(); prefillFromListing(Number($("evListing").value)); });
  $("evForm").addEventListener("submit", saveEvent);
  let noteTimer = 0;
  document.addEventListener("input", (e) => {
    const id = Number(e.target.dataset && e.target.dataset.noteText);
    if (!id) return;
    clearTimeout(noteTimer);
    const text = e.target.value;
    if (!text.trim()) return;   // пустую заметку удаляем только кнопкой 🗑
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

  load().then(async (ok) => {
    if (!ok) return;
    const sp = new URLSearchParams(location.search);
    if (sp.get("add")) await openEvent(null, { kind: KINDS[sp.get("add")] ? sp.get("add") : "show", listing: Number(sp.get("listing")) || null });
    else if (sp.get("event")) {
      const id = Number(sp.get("event"));
      let ev = allEvents().find((x) => x.id === id);
      if (!ev) {   // старое событие — ищем за 2 месяца назад
        const now = Math.floor(Date.now() / 1000);
        const r = await call(`/api/planner?from=${now - 62 * 86400}&to=${now + 400 * 86400}`);
        ev = r.ok ? r.data.events.find((x) => x.id === id) : null;
      }
      if (ev) {
        const d = new Date(ev.ts * 1000); month = new Date(d.getFullYear(), d.getMonth(), 1);
        await pickDay(dayKey(d));
        const card = document.querySelector(`[data-ev="${id}"]`);
        if (card) { card.classList.add("hl"); card.scrollIntoView({ block: "center" }); }
      }
    }
    if (sp.get("tab") === "notes") document.querySelector('[data-tab="notes"]').click();
    if ([...sp.keys()].length) history.replaceState(null, "", location.pathname);
  });
})();
