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

  const loaders = { overview, users, promos, optouts, complaints, edits, chats };

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
