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
    const s = $("pkNewClient");
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
    if (p.views) return `<span class="pk2-st seen"><i></i>Открывал ${p.views} ${plural(p.views, "раз", "раза", "раз")} · ${esc(ago(p.viewed_at))}</span>`;
    return `<span class="pk2-st"><i></i>${p.n ? "Ещё не открывал" : "Пустая"}</span>`;
  }
  function visible() {
    const s = q.trim().toLowerCase();
    return list.filter((p) => (filter === "all" || (filter === "seen" ? p.views : !p.views))
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
      <ol class="pk2-steps"><li><b>1</b><span>Назовите подборку — например, по имени клиента</span></li>
        <li><b>2</b><span>Добавьте объекты из избранного или кнопкой «В подборку» в окне объекта</span></li>
        <li><b>3</b><span>Отправьте ссылку — увидите, когда клиент её откроет</span></li></ol>
      ${list.length ? "" : `<button type="button" class="pill" data-focus-new>${ic("plus")}Создать подборку</button>`}</div>`;
  }
  function objCard(p, it, i) {
    return `<article class="pk2-obj${it.is_active ? "" : " gone"}" draggable="true" data-lid="${it.listing_id}" data-pid="${p.id}" style="--i:${i}">
      <a class="pk2-ph" href="/?open=${it.listing_id}">${it.photo ? `<img src="${esc(it.photo)}" alt="" loading="lazy">` : `<span>${ic("home")}Фото у агента</span>`}
        <span class="pk2-num">${i + 1}</span>${it.is_active ? "" : `<span class="pk2-gone">Снят с сайта</span>`}</a>
      <div class="pk2-ob">
        <b class="pk2-price">${esc(money(it.price, it.deal))}</b>
        <a class="pk2-ot" href="/?open=${it.listing_id}">${esc(it.title)}</a>
        <small>${esc(sub(it))}</small>
        <input class="pk2-com" data-com="${it.listing_id}" data-pid="${p.id}" maxlength="1000" value="${esc(it.note || "")}"
          placeholder="Комментарий для клиента" aria-label="Комментарий для клиента">
        ${notesFor(p, it)}
        <div class="pk2-oacts">
          <button type="button" class="pk2-ib" data-move="-1" data-i="${i}" data-pid="${p.id}" ${i ? "" : "disabled"} aria-label="Выше" title="Выше">${ic("arrow-up")}</button>
          <button type="button" class="pk2-ib" data-move="1" data-i="${i}" data-pid="${p.id}" ${i < p.items.length - 1 ? "" : "disabled"} aria-label="Ниже" title="Ниже">${ic("arrow-down")}</button>
          <button type="button" class="pk2-tb${it.text ? " on" : ""}" data-text="${it.listing_id}" data-pid="${p.id}" title="Текст объявления для клиента">${ic("edit")}${it.text ? "Свой текст" : "Текст"}</button>
          <button type="button" class="pk2-tb" data-wnote="${it.listing_id}" data-pid="${p.id}" title="Записать: звонок, показ, заметка">${ic("note")}Запись</button>
          <button type="button" class="pk2-ib danger" data-rm="${it.listing_id}" data-pid="${p.id}" aria-label="Убрать из подборки" title="Убрать">${ic("x")}</button>
        </div></div></article>`;
  }
  // журнал по объекту в этой подборке (и про её клиента) — видит только агент
  function notesFor(p, it) {
    const ns = (p.notes || {})[it.listing_id] || [];
    if (!ns.length) return "";
    const n = ns[0];
    return `<div class="pk2-wn" title="Журнал — видите только вы"><span class="pk2-wn-k k-${esc(n.kind)}">${NK[n.kind] || "Заметка"} · ${esc(when(n.ts))}</span>
      ${n.text ? `<p>${esc(n.text)}</p>` : ""}${ns.length > 1 ? `<small>ещё ${ns.length - 1} в журнале объекта</small>` : ""}</div>`;
  }
  function addStrip(p) {
    if (favs === null) return `<p class="pk2-hint">Загружаем избранное…</p>`;
    const inPick = new Set(p.items.map((x) => x.listing_id));
    const s = favQ.trim().toLowerCase();
    const add = favs.filter((f) => !inPick.has(f.id) && (!s || `${f.title} ${f.complex || ""} ${f.district || ""}`.toLowerCase().includes(s)));
    if (!favs.length) return `<p class="pk2-hint">Избранное пусто. Отмечайте объекты сердечком на <a href="/">поиске</a> — они появятся здесь.</p>`;
    if (!add.length) return `<p class="pk2-hint">${s ? "Ничего не нашлось." : "Всё избранное уже в подборке."}</p>`;
    return `<div class="pk2-strip">${add.slice(0, 40).map((f) => `<div class="pk2-fav">
        <span class="pk2-fph">${f.photos && f.photos[0] ? `<img src="${esc(f.photos[0])}" alt="" loading="lazy">` : ic("home")}</span>
        <b>${esc(money(f.price, f.deal))}</b><small>${esc(f.complex ? `ЖК ${f.complex} · ${f.title}` : f.title)}</small>
        ${f.description ? `<p class="pk2-fdesc">${esc(f.description.slice(0, 220))}</p>` : ""}
        <a class="pk2-fopen" href="/?open=${f.id}">Открыть объект</a>
        <button type="button" class="pk2-addb" data-add="${f.id}" data-pid="${p.id}" aria-label="Добавить в подборку">${ic("plus")}</button></div>`).join("")}</div>`;
  }
  function renderDetail() {
    const box = $("pkDetail");
    const p = list.find((x) => x.id === open);
    document.body.classList.toggle("pk-open", !!p);
    if (!p) { box.innerHTML = emptyState(); return; }
    const ph = p.items.map((x) => x.photo).filter(Boolean).slice(0, 3);
    box.innerHTML = `
      <button type="button" class="pk2-back" data-back>${ic("chevron-left")}Все подборки</button>
      <header class="pk2-hero">
        <div class="pk2-col n${ph.length} c-${esc(p.color)}">${ph.map((u) => `<img src="${esc(u)}" alt="">`).join("") || `<span>${ic("folder")}</span>`}</div>
        <div class="pk2-hero-b">
          <p class="pk2-eyebrow">Подборка · обновлена ${esc(ago(p.updated))}</p>
          <h2>${esc(p.title)}</h2>
          <p class="pk2-hero-m"><span>${p.n} ${plural(p.n, "объект", "объекта", "объектов")}</span>${seenBadge(p)}</p>
          <div class="pk2-client">${ic("user")}<span>Клиент</span>
            <select data-pk-client="${p.id}" aria-label="Клиент подборки">${clientOptions(p.client_id)}</select>
            ${p.client_id ? `<a href="/clients?id=${p.client_id}" title="Карточка клиента: пожелания, история, все объекты">Карточка ${ic("chevron")}</a>` : ""}</div>
        </div>
      </header>
      <div class="pk2-share">
        <div class="pk2-link"><span title="${esc(link(p))}">${esc(link(p).replace(/^https?:\/\//, ""))}</span>
          <button type="button" class="pill" data-copy="${p.id}">${ic("link")}Копировать</button></div>
        <button type="button" class="pk2-act" data-share-text="${esc(`${p.title}\n${link(p)}`)}">${ic("share")}<span>Поделиться</span></button>
        <a class="pk2-act" href="${esc(link(p))}" target="_blank" rel="noopener">${ic("eye")}<span>Как видит клиент</span></a>
        <button type="button" class="pk2-act" data-dup="${p.id}">${ic("layers")}<span>Дублировать</span></button>
        <button type="button" class="pk2-act danger" data-del="${p.id}">${ic("trash")}<span>Удалить</span></button>
      </div>
      ${p.contact_phone ? "" : `<button type="button" class="pk2-warn" data-tab="setup">${ic("alert")}Укажите свой телефон — клиент увидит только ваш контакт</button>`}
      <nav class="pk2-tabs"><button type="button" data-tab="items" class="${tab === "items" ? "on" : ""}">Объекты <b>${p.items.length}</b></button>
        <button type="button" data-tab="setup" class="${tab === "setup" ? "on" : ""}">Оформление и контакт</button></nav>
      ${tab === "items" ? `
        ${p.items.length ? `<p class="pk2-hint">Порядок — как увидит клиент. ${isPhone() ? "Стрелки меняют порядок." : "Перетащите карточку, чтобы поменять порядок."}</p>
          <div class="pk2-grid" id="pkGrid">${p.items.map((it, i) => objCard(p, it, i)).join("")}</div>`
          : `<div class="pk2-noitems">${ic("home")}<b>В подборке пока нет объектов</b><span>Добавьте из избранного ниже или кнопкой «В подборку» в окне объекта.</span></div>`}
        <section class="pk2-add">
          <div class="pk2-add-h"><h3>${ic("heart")}Добавить из избранного</h3>
            <label class="pk2-search sm"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
              <input id="pkFavQ" type="search" value="${esc(favQ)}" placeholder="ЖК, улица" aria-label="Найти в избранном"></label></div>
          <div id="pkFavs">${addStrip(p)}</div>
        </section>` : `
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
          <div class="agent-field"><span>Цвет подборки в списке</span><div class="pk2-colors">${COLORS.map((c) =>
            `<button type="button" class="pk2-color c-${c}${c === p.color ? " on" : ""}" data-color="${c}" aria-label="Цвет ${c}"></button>`).join("")}</div></div>
          <p class="pk2-hint">Цвет, обложку и подпись подборок можно задать в <a href="/settings">Настройках</a> — для всех подборок сразу.</p>
          <div class="pk2-form-acts"><button type="submit" class="pill">Сохранить</button></div>
        </form>`}`;
    if (window.Pick) box.querySelectorAll("select").forEach((s) => Pick.select(s, { searchPlaceholder: "Найти клиента" }));
  }
  function render() { stats(); renderList(); renderDetail(); }

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

  document.addEventListener("click", async (e) => {
    const t = e.target.closest("button, [data-open]");
    if (!t || t.closest("#pkText") || t.closest(".pk-panel")) return;
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
      try { await navigator.clipboard.writeText(link(p)); toast("Ссылка скопирована — отправьте её клиенту"); }
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
    if (t.dataset.dup) {
      const p = list.find((x) => x.id === Number(t.dataset.dup));
      const r = await call("/api/picks", { title: `${p.title} (копия)`.slice(0, 120), note: p.note || "", contact_name: p.contact_name || "",
        contact_phone: p.contact_phone || "", copy_of: p.id });
      if (!r.ok) { toast(r.data.detail || "Не получилось"); return; }
      open = r.data.pick.id; tab = "setup"; toast("Копия готова — у неё своя ссылка"); await reload(); return;
    }
    if (t.dataset.del) {
      if (!confirm("Удалить подборку? Ссылка у клиента перестанет открываться.")) return;
      await call("/api/picks", { id: Number(t.dataset.del) }, "DELETE");
      open = 0; toast("Подборка удалена"); await reload(); return;
    }
  });
  // клиент подборки: выбрали — сохраняем сразу; «+ Новый клиент…» — окно создания
  let clientFor = null;   // {pid} — для какой подборки создаём клиента; null — для формы «Создать»
  document.addEventListener("change", async (e) => {
    const s = e.target;
    if (s.id === "pkNewClient" && s.value === "new") { clientFor = null; openClient(); return; }
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
    if (clientFor && p) renderDetail(); else if (!clientFor && $("pkNewClient").value === "new") fillNewClient(null);
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

  $("pkSeg").addEventListener("click", (e) => {
    const b = e.target.closest("[data-f]");
    if (!b) return;
    filter = b.dataset.f;
    $("pkSeg").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    renderList();
  });
  $("pkQ").addEventListener("input", (e) => { q = e.target.value; renderList(); });
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
      const cl = Number($("pkNewClient").value) || null;
      const cName = cl && (clientsList.find((c) => c.id === cl) || {}).name;
      const r = await call("/api/picks", { title: f.title.value.trim() || (cName ? `Для ${cName}` : ""), ...(cl ? { client_id: cl } : {}) });
      if (!r.ok) { toast(r.data.detail || "Не получилось"); return; }
      f.title.value = ""; fillNewClient(null); open = r.data.pick.id; tab = "items"; await reload(); loadSide(); return;
    }
    if (f.dataset.edit) {
      e.preventDefault();
      const r = await call("/api/picks", { id: Number(f.dataset.edit), title: f.title.value, note: f.note.value, client_title: f.client_title.value,
        contact_name: f.contact_name.value, contact_phone: f.contact_phone.value });
      if (!r.ok) { toast(r.data.detail || "Не получилось"); return; }
      toast("Сохранено"); await reload();
    }
  });
  // комментарий к объекту — сохраняем, когда поле теряет фокус
  document.addEventListener("change", async (e) => {
    const inp = e.target;
    if (!inp.dataset || !inp.dataset.com) return;
    const r = await call(`/api/picks/${inp.dataset.pid}/items`, { listing_id: Number(inp.dataset.com), add: true, note: inp.value });
    if (r.ok) { const it = list.find((x) => x.id === Number(inp.dataset.pid)).items.find((x) => x.listing_id === Number(inp.dataset.com)); it.note = inp.value; }
    toast(r.ok ? "Комментарий сохранён" : r.data.detail || "Не сохранилось", 1500);
  });

  // перетаскивание карточек (мышь; на телефоне — стрелки)
  let drag = null;
  document.addEventListener("dragstart", (e) => {
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
