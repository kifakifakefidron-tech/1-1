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

  const loaders = { overview, users, promos, optouts, complaints };

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
      } else if (b.dataset.c) {
        await call("/api/admin/complaints", { id: Number(b.dataset.c), status: "done", hide_listing: !!b.dataset.hide });
        complaints();
      }
    } catch (err) { fail(err); }
  });

  $("userSearch").addEventListener("submit", (e) => { e.preventDefault(); users().catch(fail); });
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
