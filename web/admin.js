/* Админка 1+1: обзор, пользователи (продлить/сбросить/блок/админ), промокоды,
   скрытые номера агентов, жалобы. Все действия — через /api/admin/*. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const day = (ts) => (ts ? new Date(ts * 1000).toLocaleDateString("ru-RU") : "—");
  const now = () => Date.now() / 1000;

  async function call(url, body, method) {
    const opts = { credentials: "same-origin", method: method || (body !== undefined ? "POST" : "GET") };
    if (body !== undefined) { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(body); }
    const r = await fetch(url, opts);
    let data = {};
    try { data = await r.json(); } catch { /* пусто */ }
    if (!r.ok) throw new Error(data.detail || `Ошибка ${r.status}`);
    return data;
  }
  const fail = (e) => alert(e.message);

  // ─── обзор ──────────────────────────────────────────────────────────────
  async function overview() {
    const o = await call("/api/admin/overview");
    const card = (n, label) => `<div class="adm-card"><b>${esc(n)}</b><span>${esc(label)}</span></div>`;
    $("overview").innerHTML = [
      card(o.listings, "объектов на сайте"), card(o.listings_feed, "из них — СТРЕЛЫ (фид)"),
      card(o.listings_no_district, "без района (не находятся фильтром)"), card(o.listings_no_map, "без точки на карте"),
      card(o.listings_no_place, "скрыты: нет адреса, ЖК и района"),
      card(o.queue, "сообщений ждут разбора"), card(o.users, "пользователей"),
      card(o.users_access, "с открытым доступом"), card(o.users_paid, "оплатили подписку"),
      card(o.views_24h, "открытий номеров за сутки"), card(o.complaints_new, "новых жалоб"),
      card(o.optouts, "скрытых номеров"),
      card(o.wappi_enabled ? "включён" : "на паузе", "сбор из чатов (Wappi)"),
      card(o.payments ? "включена" : "выключена", `оплата (${o.price} ₽ / ${o.period_days} дн.)`),
      card(day(o.feed_synced), "фид СТРЕЛ обновлён"),
    ].join("");
  }

  // ─── пользователи ───────────────────────────────────────────────────────
  async function users() {
    const { items } = await call(`/api/admin/users?q=${encodeURIComponent($("userQ").value)}`);
    $("users").innerHTML = `<tr><th>#</th><th>Telegram</th><th>Почта</th><th>Телефон</th><th>Доступ до</th><th>Зарегистрирован</th><th></th></tr>` +
      items.map((u) => {
        const until = Math.max(u.trial_until, u.paid_until);
        const status = u.blocked ? `<span class="bad">заблокирован</span>`
          : u.is_admin ? "админ" : until > now() ? `<span class="ok">${day(until)}</span>${u.paid_until > now() ? " (оплачен)" : " (пробный)"}` : "нет";
        return `<tr><td>${u.id}</td><td>${u.tg_username ? "@" + esc(u.tg_username) : "—"}<br><small>${esc(u.name || "")}</small></td>
          <td>${esc(u.email || "—")}</td><td>${esc(u.phone || "—")}</td><td>${status}</td><td>${day(u.created)}</td>
          <td class="acts">
            <button data-u="${u.id}" data-a="extend" data-days="7">+7 дн.</button>
            <button data-u="${u.id}" data-a="extend" data-days="30">+30 дн.</button>
            <button data-u="${u.id}" data-a="reset">Сбросить доступ</button>
            <button data-u="${u.id}" data-a="${u.blocked ? "unblock" : "block"}">${u.blocked ? "Разблокировать" : "Заблокировать"}</button>
            <button data-u="${u.id}" data-a="${u.is_admin ? "unadmin" : "admin"}">${u.is_admin ? "Снять админа" : "Сделать админом"}</button>
          </td></tr>`;
      }).join("");
  }

  // ─── промокоды ──────────────────────────────────────────────────────────
  async function promos() {
    const { items } = await call("/api/admin/promos");
    $("promos").innerHTML = `<tr><th>Код</th><th>Дней</th><th>Использован</th><th>Заметка</th><th>Создан</th><th></th></tr>` +
      items.map((p) => `<tr><td><b>${esc(p.code)}</b></td><td>${p.days}</td><td>${p.used} из ${p.max_uses}</td>
        <td>${esc(p.note || "")}</td><td>${day(p.created)}</td>
        <td class="acts"><button data-del-promo="${esc(p.code)}">Удалить</button></td></tr>`).join("");
  }

  // ─── скрытые номера ─────────────────────────────────────────────────────
  async function optouts() {
    const { items } = await call("/api/admin/optouts");
    const src = { agent: "агент сам, через бота", admin: "вручную" };
    $("optouts").innerHTML = `<tr><th>Номер</th><th>Кто скрыл</th><th>Когда</th><th></th></tr>` +
      items.map((o) => `<tr><td>${esc(o.phone)}</td><td>${esc(src[o.source] || o.source || "")}</td><td>${day(o.ts)}</td>
        <td class="acts"><button data-unopt="${esc(o.phone)}">Снова показывать</button></td></tr>`).join("");
  }

  // ─── жалобы ─────────────────────────────────────────────────────────────
  async function complaints() {
    const { items } = await call("/api/admin/complaints");
    const reason = { sold: "продан/неактуален", wrong: "неверные данные", my_phone: "это мой номер", other: "другое" };
    $("complaints").innerHTML = `<tr><th>Когда</th><th>Объект</th><th>Причина</th><th>Текст</th><th>Статус</th><th></th></tr>` +
      items.map((c) => `<tr><td>${day(c.ts)}</td>
        <td>${c.listing_id ? `<a href="/?open=${c.listing_id}" target="_blank">${esc(c.title || "#" + c.listing_id)}</a>` : "—"}</td>
        <td>${esc(reason[c.reason] || c.reason)}</td><td>${esc(c.text || "")}</td>
        <td>${c.status === "new" ? "<b>новая</b>" : esc(c.status)}</td>
        <td class="acts">${c.status === "new" ? `<button data-c="${c.id}">Готово</button>
          <button data-c="${c.id}" data-hide="1">Скрыть объект</button>` : ""}</td></tr>`).join("");
  }

  async function edits() {
    const { items } = await call("/api/admin/edits");
    const names = { price: "цена", description: "описание", status: "статус", create: "добавил объект", confirm: "ответ на письмо",
      "photo+": "добавил фото", "photo-": "удалил фото", type: "тип", deal: "сделка", rooms: "комнат", area: "площадь",
      land: "участок", floor: "этаж", floors: "этажность", district: "район", complex: "ЖК", street: "улица", house: "дом" };
    const val = (v) => { try { const x = JSON.parse(v); return x == null ? "—" : String(x); } catch { return v || "—"; } };
    const revertable = new Set(["price", "description", "type", "deal", "rooms", "area", "land", "floor", "floors",
      "district", "complex", "street", "house"]);
    $("edits").innerHTML = `<tr><th>Когда</th><th>Кто</th><th>Объект</th><th>Что</th><th>Было</th><th>Стало</th><th></th></tr>` +
      items.map((e) => `<tr><td>${day(e.ts)}</td><td>${esc(e.email || e.name || "#" + e.user_id)}</td>
        <td><a href="/?open=${e.listing_id}" target="_blank">${esc(e.title || "#" + e.listing_id)}</a></td>
        <td>${esc(names[e.field] || e.field)}</td>
        <td>${esc(val(e.old).slice(0, 120))}</td><td>${esc(val(e.new).slice(0, 120))}</td>
        <td class="acts">${e.reverted ? "откачено" : revertable.has(e.field) ? `<button data-revert="${e.id}">Откатить</button>` : ""}</td></tr>`).join("");
  }

  let chatList = [];
  let chatShow = "";
  const SRC = { wa: "WhatsApp", tg: "Telegram", max: "MAX" };
  const chatKey = (c) => `${c.source}|${c.chat_id}`;
  async function chats() {
    chatList = (await call("/api/admin/chats")).items;
    renderChats();
  }
  function chatRow(c) {
    const off = c.blocked || c.foreign;
    const name = c.name || `Без названия · ${SRC[c.source] || c.source} ${c.chat_id.slice(0, 18)}`;
    const toggle = c.foreign
      ? `<span class="note">другой город</span>`
      : `<label class="switch" title="${off ? "Не берём — включить" : "Берём — выключить"}">
           <input type="checkbox" data-toggle="${esc(chatKey(c))}"${off ? "" : " checked"}><span></span></label>`;
    return `<tr class="${off ? "muted" : ""}" data-row="${esc(chatKey(c))}">
      <td><b>${esc(name)}</b><div class="note">${SRC[c.source] || c.source} · ${c.purged ? "сообщения удалены" : "сообщений " + c.messages}
        ${c.last_ts ? " · последнее " + day(c.last_ts) : ""}</div></td>
      <td class="num">${c.listings}<div class="note">на сайте</div></td>
      <td class="acts">
        ${c.messages ? `<button data-peek="${esc(chatKey(c))}">Посмотреть</button>` : ""}
        ${c.link ? `<a class="btn-link" href="${esc(c.link)}" target="_blank" rel="noopener">Открыть чат ↗</a>` : ""}
      </td>
      <td class="acts">${toggle}</td></tr>`;
  }
  function renderChats() {
    const q = $("chatQ").value.trim().toLowerCase();
    const rows = chatList.filter((c) => {
      const off = c.blocked || c.foreign;
      if (q && !(c.name || c.chat_id).toLowerCase().includes(q)) return false;
      if (chatShow === "on") return !off;
      if (chatShow === "off") return off;
      if (chatShow === "empty") return !c.listings;
      return true;
    });
    $("chats").innerHTML = rows.length ? rows.map(chatRow).join("") : `<tr><td class="note">Ничего не нашлось</td></tr>`;
  }
  async function peek(key, btn) {
    const row = document.querySelector(`[data-row="${CSS.escape(key)}"]`);
    const next = row.nextElementSibling;
    if (next && next.classList.contains("peek")) { next.remove(); btn.textContent = "Посмотреть"; return; }
    const [source, chat_id] = key.split(/\|(.*)/s);
    btn.textContent = "Загружаю…";
    const d = await call(`/api/admin/chats?source=${encodeURIComponent(source)}&chat_id=${encodeURIComponent(chat_id)}`);
    btn.textContent = "Скрыть";
    const objs = d.listings.map((o) => `<li><a href="/?open=${o.id}" target="_blank">${esc(o.title)}</a>
        ${o.is_active ? "" : '<span class="note">(скрыт)</span>'}
        <span class="note">${esc([o.complex && "ЖК " + o.complex, o.district, o.street].filter(Boolean).join(" · "))}</span></li>`).join("");
    const msgs = d.messages.map((m) => `<li><span class="note">${day(m.ts)}${m.sender_name ? " · " + esc(m.sender_name) : ""}</span>
        <div class="msg">${esc(m.text)}</div></li>`).join("");
    row.insertAdjacentHTML("afterend", `<tr class="peek"><td colspan="4">
        <div class="peek-grid"><div><h4>Последние сообщения</h4><ul>${msgs || "<li class=note>нет</li>"}</ul></div>
        <div><h4>Объекты из этого чата</h4><ul>${objs || "<li class=note>нет</li>"}</ul></div></div></td></tr>`);
  }
  let toastTimer = 0;
  function chatToast(html) {
    const t = $("chatToast");
    t.innerHTML = html; t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, 8000);
  }
  async function setChat(key, blocked) {
    const c = chatList.find((x) => chatKey(x) === key);
    const [source, chat_id] = key.split(/\|(.*)/s);
    c.blocked = blocked ? 1 : 0;
    renderChats();
    try {
      const r = await call("/api/admin/chats", { source, chat_id, blocked });
      const name = esc(c.name || "чат");
      chatToast(blocked
        ? `«${name}» — не берём. Скрыто объектов: ${r.hidden || 0} (те, что были и в других чатах, остаются).
           Сообщения чата удалятся через 10 минут. <button data-undo="${esc(key)}">Отменить</button>`
        : `«${name}» — снова берём. Вернулось объектов: ${r.returned || 0}.`);
      chats();
    } catch (err) {
      c.blocked = blocked ? 0 : 1;
      renderChats();
      alert(err.message);
    }
  }

  // ─── Карта: объекты без точки — разбирать по одному ──────────────────────
  let geoKind = "none";
  async function geo() {
    const d = await call(`/api/admin/geo-queue?kind=${geoKind === "learned" ? "none" : geoKind}`);
    const c = d.counts;
    const label = { none: "Нет на карте", approx: "Примерные", pending: "Ещё ищем", learned: "Выученные адреса" };
    document.querySelectorAll("#geoKinds .chip").forEach((b) => {
      const k = b.dataset.geoKind;
      b.textContent = `${label[k]} · ${c[k] ?? 0}`;
      b.classList.toggle("on", k === geoKind);
    });
    if (geoKind === "learned") {
      $("geoTable").innerHTML = d.learned.length
        ? `<tr><th>Адрес / ЖК</th><th>Правок</th><th>Точка</th><th>Когда</th></tr>` + d.learned.map((l) => `<tr>
            <td>${esc(l.label || l.key)}</td><td>${l.n}</td>
            <td><a href="https://yandex.ru/maps/?pt=${l.lon},${l.lat}&z=17&l=map" target="_blank">${l.lat.toFixed(5)}, ${l.lon.toFixed(5)} ↗</a></td>
            <td>${day(l.ts)}</td></tr>`).join("")
        : `<tr><td class="note">Пока ничего не выучено — поправьте первую точку.</td></tr>`;
      return;
    }
    const addr = (o) => [o.complex && "ЖК " + o.complex, o.district, o.settlement, o.street && `ул. ${o.street}${o.house ? ", " + o.house : ""}`]
      .filter(Boolean).join(" · ");
    $("geoTable").innerHTML = d.items.length
      ? `<tr><th>Объект</th><th>Адрес из объявления</th><th></th></tr>` + d.items.map((o) => `<tr>
          <td><b>${esc(o.title)}</b><div class="note msg-snip">${esc(o.text)}</div></td>
          <td>${esc(addr(o)) || '<span class="note">—</span>'}</td>
          <td class="acts"><a class="btn-link pill-link" href="/fix?mode=geo&kind=${geoKind}&id=${o.id}">📍 Поставить точку</a></td></tr>`).join("")
      : `<tr><td class="note">Здесь пусто — всё на карте 🎉</td></tr>`;
  }

  // ─── ЖК и районы: разбор и выученные правила ───────────────────────────
  let placeKind = "nodistrict";
  async function place() {
    const d = await call(`/api/admin/place-queue?kind=${placeKind === "rules" ? "fixed" : placeKind}`);
    const label = { nodistrict: "Без района", nocomplex: "Квартиры без ЖК", fixed: "Поправлено вручную", rules: "Выученные правила" };
    document.querySelectorAll("#placeKinds .chip").forEach((b) => {
      const k = b.dataset.placeKind;
      b.textContent = `${label[k]} · ${d.counts[k] ?? 0}`;
      b.classList.toggle("on", k === placeKind);
    });
    if (placeKind === "rules") {
      const kinds = { addr: "дом", cx_alias: "название ЖК", cx_district: "район ЖК" };
      $("placeTable").innerHTML = d.rules.length
        ? `<tr><th>Правило</th><th>Вид</th><th>Раз</th><th>Когда</th><th></th></tr>` + d.rules.map((r) => `<tr>
            <td>${esc(r.label)}</td><td>${kinds[r.kind] || r.kind}</td><td>${r.n}</td><td>${day(r.ts)}</td>
            <td class="acts"><button data-forget="${r.id}">Забыть</button></td></tr>`).join("")
        : `<tr><td class="note">Пока ничего не выучено.</td></tr>`;
      return;
    }
    const addr = (o) => [o.complex && "ЖК " + o.complex, o.district, o.settlement, o.street && `ул. ${o.street}${o.house ? ", " + o.house : ""}`]
      .filter(Boolean).join(" · ");
    $("placeTable").innerHTML = d.items.length
      ? `<tr><th>Объект</th><th>Сейчас</th><th></th></tr>` + d.items.map((o) => `<tr>
          <td><b>${esc(o.title)}</b><div class="note msg-snip">${esc(o.text)}</div></td>
          <td>${esc(addr(o)) || '<span class="note">—</span>'}</td>
          <td class="acts"><a class="btn-link pill-link" href="/fix?mode=place&kind=${placeKind}&id=${o.id}">✎ Поправить</a></td></tr>`).join("")
      : `<tr><td class="note">Здесь пусто 🎉</td></tr>`;
  }

  // ─── Справочник: районы / ЖК / улицы — список слева, карточка справа ─────
  const NONE = "__none__";
  let spKind = "district", spItems = [], spDistricts = [], spOpen = "", spBack = [];
  const KIND_LABEL = { district: "Район", complex: "ЖК", street: "Улица" };
  const HINT = {
    district: "Нажмите на район — увидите его ЖК, улицы и все объекты. Можно переименовать, удалить (с переносом объектов), перенести любой ЖК или улицу в другой район.",
    complex: "Нажмите на ЖК — увидите, в каких районах его объекты и почему. Можно перенести ЖК в район, сказать «это другой ЖК» или «это не ЖК».",
    street: "Нажмите на улицу — увидите, в каких районах её объекты. Можно перенести улицу (или её часть из одного района) в другой район.",
  };
  const plural = (n, a, b, c) => { const m = n % 10, h = n % 100; return m === 1 && h !== 11 ? a : m >= 2 && m <= 4 && (h < 10 || h >= 20) ? b : c; };
  const objs = (n) => `${n} ${plural(n, "объект", "объекта", "объектов")}`;
  const dname = (n) => (n === NONE ? "без района" : n);
  const price = (p) => (p ? `${(p / 1e6).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} млн` : "—");

  async function districts() { await spLoad(); }
  async function spLoad(kind) {
    if (kind) { spKind = kind; spOpen = ""; spBack = []; }
    document.querySelectorAll("#spKinds .seg").forEach((b) => b.classList.toggle("on", b.dataset.spKind === spKind));
    $("spHint").textContent = HINT[spKind];
    $("spNew").hidden = spKind !== "district";
    $("spQ").placeholder = spKind === "district" ? "Найти район…" : spKind === "complex" ? "Найти ЖК…" : "Найти улицу…";
    const d = await call(`/api/admin/dir?kind=${spKind}`);
    spItems = d.items; spDistricts = d.districts;
    spRenderList();
    if (spOpen) spShow(spOpen);
    else $("spCard").innerHTML = `<div class="sp-empty">Выберите слева, что посмотреть.</div>`;
  }
  function spRenderList() {
    const q = ($("spQ").value || "").trim().toLowerCase().replace(/ё/g, "е");
    const list = spItems.filter((x) => !q || x.title.toLowerCase().replace(/ё/g, "е").includes(q)).slice(0, 600);
    $("spList").innerHTML = list.map((x) => `<li><button type="button" class="sp-item${x.name === spOpen ? " on" : ""}${x.special ? " special" : ""}"
        data-sp-open="${esc(x.name)}"><span class="sp-name">${esc(x.title)}${x.custom ? ' <small>свой</small>' : ""}
        ${x.districts && x.districts.length ? `<small>${esc(x.districts.join(", "))}</small>` : ""}</span><b>${x.count}</b></button></li>`).join("")
      || `<li class="note sp-none">Ничего не нашлось</li>`;
  }

  // Карточка
  async function spShow(name, kind) {
    if (kind && kind !== spKind) { spBack.push([spKind, spOpen]); spKind = kind; await spLoad(); }
    spOpen = name;
    spRenderList();
    $("spCard").innerHTML = `<div class="sp-empty">Загружаю…</div>`;
    const d = await call(`/api/admin/dir?kind=${spKind}&name=${encodeURIComponent(name)}`);
    const back = spBack.length ? `<button type="button" class="sp-back" data-sp-back="1">← назад</button>` : "";
    const listingRows = d.listings.map((o) => `<li>
        <a href="/?open=${o.id}" target="_blank"><b>${esc(o.title)}</b></a>
        <span>${price(o.price)}</span>
        <span class="note">${esc([o.complex && "ЖК " + o.complex, [o.district, ...(o.extra_districts || [])].filter(Boolean).join(" / ") || "без района",
          o.street && `ул. ${o.street}${o.house ? ", " + o.house : ""}`].filter(Boolean).join(" · "))}</span>
        <a class="sp-fix" href="/fix?mode=place&kind=fixed&id=${o.id}" title="Поправить ЖК и район этого объекта">✎</a></li>`).join("");
    const listingsBlock = `<details class="sp-sec" ${d.listings.length <= 12 ? "open" : ""}>
        <summary>Объекты · ${d.total}${d.total > d.listings.length ? ` (показаны последние ${d.listings.length})` : ""}</summary>
        <ul class="sp-objs">${listingRows || '<li class="note">нет</li>'}</ul></details>`;
    let html = "";
    if (spKind === "district") {
      const special = name === NONE;
      const row = (kind, x) => `<li><button type="button" class="link" data-sp-goto="${kind}" data-name="${esc(x.name)}">${esc(x.name)}</button>
          <small>${objs(x.n)}</small>
          <button type="button" class="chip" data-sp-move="${kind}" data-name="${esc(x.name)}">${special ? "Назначить район…" : "Перенести в…"}</button></li>`;
      html = `<div class="sp-head">${back}<span class="sp-kind">${special ? "Без района" : "Район"}</span>
          <h2>${special ? "Объекты без района" : esc(name)}</h2><span class="sp-count">${objs(d.total)}</span></div>
        ${special ? `<p class="sp-explain">Эти объекты не находятся по фильтру районов. Назначьте район улице или ЖК — сразу для всех их объектов
            и для новых объявлений. Или поправьте объект по одному (✎).</p>`
          : `<div class="sp-actions">
            <button type="button" class="pill light" data-sp-act="rename_district">✎ Переименовать</button>
            <button type="button" class="pill light" data-sp-act="merge_district">⇄ Слить с другим районом</button>
            <button type="button" class="pill light danger" data-sp-act="delete_district">🗑 Удалить с переносом объектов</button>
          </div>`}
        <div class="sp-ask" id="spAsk" hidden></div>
        <div class="sp-cols">
          <details class="sp-sec" open><summary>ЖК · ${d.complexes.length}</summary><ul class="sp-sub">${d.complexes.map((x) => row("complex", x)).join("") || '<li class="note">нет</li>'}</ul></details>
          <details class="sp-sec" open><summary>Улицы без ЖК · ${d.streets.length}</summary><ul class="sp-sub">${d.streets.map((x) => row("street", x)).join("") || '<li class="note">нет</li>'}</ul></details>
        </div>
        ${listingsBlock}`;
    } else if (spKind === "complex" && name === NONE) {
      html = `<div class="sp-head">${back}<span class="sp-kind">Без ЖК</span><h2>Квартиры без ЖК</h2><span class="sp-count">${objs(d.total)}</span></div>
        <p class="sp-explain">Квартиры, у которых не указан ЖК. Если ЖК виден в тексте — поправьте объект (✎), и сервис запомнит.</p>
        ${listingsBlock}`;
    } else {
      const isCx = spKind === "complex";
      const where = d.districts.map((x) => `<li><span>${esc(dname(x.name))}</span><small>${objs(x.n)}</small>
          ${!isCx && d.districts.length > 1 ? `<button type="button" class="chip" data-sp-move-from="${esc(x.name)}">Только эти → в…</button>` : ""}</li>`).join("");
      html = `<div class="sp-head">${back}<span class="sp-kind">${KIND_LABEL[spKind]}</span><h2>${isCx ? "ЖК " : "ул. "}${esc(name)}</h2>
          <span class="sp-count">${objs(d.total)}</span></div>
        <div class="sp-facts">
          <div><span class="sp-flabel">Сейчас в районах</span><ul class="sp-where">${where || '<li class="note">объектов нет</li>'}</ul></div>
          <div><span class="sp-flabel">По справочнику</span><b>${esc(d.kb_district || "—")}</b></div>
          <div><span class="sp-flabel">Ваше правило</span><b>${d.rule ? esc(d.rule) : "—"}</b>${d.alias ? `<small>${d.alias.complex ? "это ЖК " + esc(d.alias.complex) : "это не ЖК"}</small>` : ""}</div>
        </div>
        <div class="sp-actions">
          <button type="button" class="pill" data-sp-act="move_all">→ Перенести ${isCx ? "ЖК" : "улицу"} в район…</button>
          ${isCx ? `<button type="button" class="pill light" data-sp-act="rename_complex">✎ Это другой ЖК / переименовать</button>
                    <button type="button" class="pill light danger" data-sp-act="not_complex">Это не ЖК</button>` : ""}
        </div>
        <div class="sp-ask" id="spAsk" hidden></div>
        ${listingsBlock}`;
    }
    $("spCard").innerHTML = html;
  }

  // Вопрос «куда?» — прямо в карточке, с выпадающим списком
  function spAsk(title, options, onPick, newLabel) {
    const box = $("spAsk");
    box.hidden = false;
    box.innerHTML = `<div class="sp-ask-title">${title}</div><div class="sp-ask-row"><input placeholder="Начните вводить…">
      <button type="button" class="ghost" data-sp-cancel="1">Отмена</button></div>`;
    const input = box.querySelector("input");
    Combo(input, { options: () => options, onPick, ...(newLabel ? { newLabel, onNew: onPick } : {}) });
    input.focus();
    box.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
  async function spDo(body, msg) {
    try {
      const r = await call("/api/admin/dir", body);
      spToast(msg(r));
      if (body.action === "rename_district" || body.action === "delete_district") spOpen = r.name || body.to || "";
      if (body.action === "rename_complex" && r.name) spOpen = r.name;
      await spLoad();
    } catch (err) { fail(err); }
  }
  function spToast(text) {
    const t = $("chatToast");
    t.innerHTML = esc(text); t.hidden = false;
    clearTimeout(spToast.timer);
    spToast.timer = setTimeout(() => { t.hidden = true; }, 6000);
  }
  function spAction(act, btn) {
    const name = spOpen;
    const others = spDistricts.filter((d) => d !== name);
    if (act === "rename_district") {
      spAsk(`Новое название района «${esc(name)}». Старое продолжит узнаваться в объявлениях.`, [], (to) =>
        spDo({ action: "rename_district", name, to }, (r) => `«${name}» → «${r.name || to}» · объектов: ${r.listings}`), (v) => `Назвать «${v}»`);
    } else if (act === "merge_district" || act === "delete_district") {
      const del = act === "delete_district";
      spAsk(del ? `Удалить район «${esc(name)}». Куда перенести его объекты? (старое название будет узнаваться как выбранный район)`
                : `Слить «${esc(name)}» с районом:`, others, (to) => {
        if (!confirm(`${del ? "Удалить" : "Слить"} «${name}» → «${to}»? Все объекты, правила и подписки перейдут в «${to}».`)) return;
        spDo({ action: "delete_district", name, to }, (r) => `Район «${name}» ${del ? "удалён" : "слит"}: объекты в «${to}» (${r.listings})`);
      });
    } else if (act === "move_all") {
      spAsk(`В какой район перенести ${spKind === "complex" ? "ЖК" : "улицу"} «${esc(name)}»? (все объекты и новые объявления)`, spDistricts, (to) =>
        spDo({ action: "move", kind: spKind, name, to }, (r) => `«${name}» → ${to} · перенесено объектов: ${r.listings}`));
    } else if (act === "rename_complex") {
      const cxs = spItems.filter((x) => !x.special && x.name !== name).map((x) => x.name);
      spAsk(`Как правильно называется ЖК «${esc(name)}»? Можно выбрать существующий — объединятся.`, cxs, (to) =>
        spDo({ action: "rename_complex", name, to }, (r) => `«${name}» → ЖК ${r.name} · объектов: ${r.listings}`), (v) => `Назвать «${v}»`);
    } else if (act === "not_complex") {
      if (!confirm(`«${name}» — это не ЖК? У всех объектов ЖК уберётся, и в новых объявлениях так больше считаться не будет.`)) return;
      spDo({ action: "rename_complex", name, to: "" }, (r) => `«${name}» больше не считается ЖК · объектов: ${r.listings}`);
    }
  }

  const loaders = { overview, users, promos, optouts, complaints, edits, chats, geo, place, districts };

  function show(tab) {
    document.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
    document.querySelectorAll("[data-pane]").forEach((s) => { s.hidden = s.dataset.pane !== tab; });
    loaders[tab]().catch(fail);
  }

  document.addEventListener("click", async (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    try {
      if (b.dataset.tab) show(b.dataset.tab);
      else if (b.dataset.u) {
        if (b.dataset.a === "block" && !confirm("Заблокировать пользователя? Он сразу выйдет со всех устройств.")) return;
        await call(`/api/admin/users/${b.dataset.u}`, { action: b.dataset.a, days: Number(b.dataset.days || 0) });
        users();
      } else if (b.dataset.delPromo) {
        if (!confirm(`Удалить промокод ${b.dataset.delPromo}?`)) return;
        await call(`/api/admin/promos?code=${encodeURIComponent(b.dataset.delPromo)}`, undefined, "DELETE");
        promos();
      } else if (b.dataset.unopt) {
        await call("/api/admin/optouts", { remove: b.dataset.unopt });
        optouts();
      } else if (b.dataset.spKind) {
        await spLoad(b.dataset.spKind);
      } else if (b.dataset.spOpen) {
        spBack = [];
        await spShow(b.dataset.spOpen);
      } else if (b.dataset.spGoto) {
        await spShow(b.dataset.name, b.dataset.spGoto);
      } else if (b.dataset.spBack) {
        const [k, n] = spBack.pop();
        spKind = k; await spLoad(); await spShow(n);
      } else if (b.dataset.spMove) {
        const kind = b.dataset.spMove, name = b.dataset.name, from = spOpen;
        spAsk(`${from === NONE ? "Назначить район" : "Перенести в район"}: ${kind === "complex" ? "ЖК" : "ул."} «${esc(name)}»`,
          spDistricts.filter((d) => d !== from), (to) =>
            spDo({ action: "move", kind, name, from, to }, (r) => `«${name}» → ${to} · объектов: ${r.listings}`));
      } else if (b.dataset.spMoveFrom) {
        const from = b.dataset.spMoveFrom, name = spOpen;
        spAsk(`Объекты ул. «${esc(name)}» из «${esc(dname(from))}» — перенести в:`, spDistricts.filter((d) => d !== from), (to) =>
          spDo({ action: "move", kind: "street", name, from, to }, (r) => `ул. ${name}: ${dname(from)} → ${to} · объектов: ${r.listings}`));
      } else if (b.dataset.spAct) {
        spAction(b.dataset.spAct, b);
      } else if (b.dataset.spCancel) {
        $("spAsk").hidden = true;
      } else if (b.dataset.placeKind) {
        placeKind = b.dataset.placeKind;
        await place();
      } else if (b.dataset.forget) {
        if (!confirm("Забыть это правило? Уже поправленные объекты останутся как есть, новые — без этого правила.")) return;
        await call("/api/admin/place-queue", { forget: Number(b.dataset.forget) });
        await place();
      } else if (b.dataset.geoKind) {
        geoKind = b.dataset.geoKind;
        await geo();
      } else if (b.dataset.peek) {
        await peek(b.dataset.peek, b);
      } else if (b.dataset.undo) {
        $("chatToast").hidden = true;
        await setChat(b.dataset.undo, false);
      } else if (b.dataset.show !== undefined) {
        chatShow = b.dataset.show;
        document.querySelectorAll("#chatShow .chip").forEach((x) => x.classList.toggle("on", x === b));
        renderChats();
      } else if (b.dataset.revert) {
        if (!confirm("Вернуть прежнее значение?")) return;
        await call("/api/admin/edits", { id: Number(b.dataset.revert) });
        edits();
      } else if (b.dataset.c) {
        await call("/api/admin/complaints", { id: Number(b.dataset.c), status: "done", hide_listing: !!b.dataset.hide });
        complaints();
      }
    } catch (err) { fail(err); }
  });

  $("userSearch").addEventListener("submit", (e) => { e.preventDefault(); users().catch(fail); });
  $("chatSearch").addEventListener("submit", (e) => e.preventDefault());
  $("chatQ").addEventListener("input", renderChats);
  $("spQ").addEventListener("input", spRenderList);
  $("spNew").addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = e.target.name.value.trim();
    if (!name) return;
    try {
      const r = await call("/api/admin/districts", { name });
      e.target.name.value = "";
      spOpen = r.name;
      await spLoad();
      spToast(`Район «${r.name}» добавлен`);
    } catch (err) { fail(err); }
  });
  document.addEventListener("change", (e) => {
    const t = e.target;
    if (t.dataset && t.dataset.toggle) setChat(t.dataset.toggle, !t.checked);
  });
  $("promoNew").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    try {
      const r = await call("/api/admin/promos", { code: f.code.value.trim() || null, days: Number(f.days.value),
                                                  max_uses: Number(f.max_uses.value), note: f.note.value });
      f.code.value = ""; f.note.value = "";
      await promos();
      alert(`Промокод создан: ${r.code}`);
    } catch (err) { fail(err); }
  });
  $("optoutNew").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await call("/api/admin/optouts", { phone: e.target.phone.value }); e.target.phone.value = ""; optouts(); }
    catch (err) { fail(err); }
  });

  (async () => {
    try {
      await call("/api/admin/overview");
      $("app").hidden = false;
      show("overview");
    } catch {
      $("denied").hidden = false;
    }
  })();
})();
