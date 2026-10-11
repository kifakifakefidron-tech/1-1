/* Личный кабинет (/cabinet): профиль и доступ, разделы, сводка админа, поиски, «Приведи коллегу», промокод. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ic = (n) => (window.ic ? window.ic(n) : "");
  const plural = (n, a, b, c) => { const m10 = n % 10, m100 = n % 100; return m10 === 1 && m100 !== 11 ? a : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? b : c; };
  const day = (ts) => new Date(ts * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
  const num = (x) => Number(x || 0).toLocaleString("ru-RU");
  let me = null, meta = {};

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

  function profile() {
    const name = me.name || me.email || (me.tg_username && "@" + me.tg_username) || "Коллега";
    $("cbAva").textContent = (name.trim()[0] || "1").toUpperCase();
    $("cbName").textContent = name;
    $("cbMail").textContent = [me.name && me.email, me.tg_username && "@" + me.tg_username].filter(Boolean).join(" · ");
    const left = me.access_until ? Math.max(0, Math.ceil((me.access_until - Date.now() / 1000) / 86400)) : 0;
    if (me.is_admin) $("cbStatus").innerHTML = `<span class="cb-badge gold">Администратор</span><p>Доступ ко всему без ограничений.</p>`;
    else if (me.access) {
      $("cbStatus").innerHTML = `<span class="cb-badge ok">Доступ открыт</span>
        <div class="cb-meter"><i style="--w:${Math.min(100, left / 7 * 100)}%"></i></div>
        <p>До ${esc(day(me.access_until))} · осталось ${left} ${plural(left, "день", "дня", "дней")}</p>`;
    } else $("cbStatus").innerHTML = `<span class="cb-badge off">Доступа к номерам нет</span><p>Поиск, карта и избранное — бесплатно всегда.</p>`;
    $("cbPlan").innerHTML = me.is_admin ? "" : `<div class="cb-plan-card"><span>Подписка</span><b>${esc(meta.price)} ₽</b>
      <small>за ${esc(meta.period_days)} ${plural(Number(meta.period_days) || 0, "день", "дня", "дней")} — номера агентов и все инструменты</small>
      ${meta.payments ? `<button type="button" class="pill wide" id="cbPay">Оформить</button>` : `<small class="cb-soon">Оплата появится скоро</small>`}</div>`;
  }

  function tiles() {
    const t = (href, icon, label, hint, count) => `<a class="cb-tile" href="${href}">${ic(icon)}<span><b>${label}</b><small>${hint}</small></span>
      ${count ? `<em>${count}</em>` : ""}</a>`;
    $("cbTiles").innerHTML = [
      t("/?fav=1", "heart", "Избранное", "объекты и статусы", me.favorites.length || ""),
      t("/notifications", "bell", "Уведомления", "поиски, цены, напоминания", me.unread || ""),
      t("/planner", "calendar", "Планер", "показы и созвоны"),
      t("/picks", "folder", "Подборки", "ссылки для клиентов"),
      t("/requests", "users", "Запросы", "кто сейчас ищет"),
      t("/market", "chart", "Аналитика", "цена м² по районам"),
      t("/?agent=1", "tag", "Мои объявления", "кабинет агента"),
      me.is_admin ? t("/admin", "sliders", "Админка", "сайт и база") : "",
    ].join("");
  }

  async function admin() {
    if (!me.is_admin) return;
    $("cbAdmin").hidden = false;
    $("cbStats").innerHTML = `<div class="cb-stat skel"></div>`.repeat(6);
    const r = await call("/api/admin/overview");
    if (!r.ok) return;
    const o = r.data;
    const s = (n, l, warn) => `<div class="cb-stat${warn ? " warn" : ""}"><b>${num(n)}</b><span>${l}</span></div>`;
    $("cbStats").innerHTML = [s(o.listings, "объектов на сайте"), s(o.users, "пользователей"), s(o.users_access, "с доступом"),
      s(o.queue, "ждут разбора"), s(o.views_24h, "номеров за сутки"), s(o.complaints_new, "новых жалоб", o.complaints_new > 0)].join("");
  }

  async function saved() {
    const r = await call("/api/saved");
    const items = r.ok ? r.data.items || [] : [];
    $("cbSavedN").textContent = items.length || "";
    $("cbSaved").innerHTML = items.length ? items.map((x) => `<li><a href="${esc(x.url)}">${ic("search")}<span>${esc(x.title)}</span></a>
        <button type="button" class="cb-x" data-unsave="${x.id}" aria-label="Не следить">${ic("x")}</button></li>`).join("")
      : `<li class="cb-empty">Настройте фильтры на главной и нажмите «Следить» — новые объекты придут в уведомления и на почту.</li>`;
  }

  function referral() {
    const r = me.ref;
    if (!r) return;
    $("cbRefSec").hidden = false;
    const link = `${location.origin}/?ref=${r.code}`;
    const text = `Пользуюсь 1+1 — все объекты Краснодара из риелторских чатов в одном поиске, с картой и номерами агентов: ${link}`;
    $("cbRef").innerHTML = `<p class="cb-text">Отправьте ссылку коллеге-риелтору. Когда он войдёт и подтвердит свой номер агента —
        вам <b>+${r.days} дней</b> доступа.${r.invited ? ` Пришли по ссылке: ${r.invited}, подтвердили: ${r.rewarded}.` : ""}</p>
      <div class="cb-ref-row"><input readonly value="${esc(link)}" aria-label="Ваша ссылка">
        <button type="button" class="pill light" id="cbCopy">${ic("link")}Копировать</button>
        <a class="pill light" href="https://wa.me/?text=${encodeURIComponent(text)}" target="_blank" rel="noopener">WhatsApp</a></div>`;
    $("cbCopy").addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(link); toast("Ссылка скопирована"); } catch { prompt("Скопируйте ссылку:", link); }
    });
  }

  async function load() {
    const [m, mt] = await Promise.all([call("/api/me"), call("/api/meta")]);
    me = m.data.me;
    meta = mt.data || {};
    if (!me) { $("cbGuest").hidden = false; return; }
    $("cbApp").hidden = false;
    profile(); tiles(); referral(); admin(); saved();
  }

  document.addEventListener("click", async (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.unsave) {
      b.closest("li").classList.add("leaving");
      await call("/api/saved", { id: Number(b.dataset.unsave) }, "DELETE");
      setTimeout(saved, 250);
    } else if (b.id === "cbLogout") {
      await call("/api/auth/logout", {});
      location.href = "/";
    } else if (b.id === "cbPay") {
      const r = await call("/api/pay", {});
      if (r.ok && r.data.url) location.href = r.data.url; else toast(r.data.detail || "Оплата скоро появится");
    }
  });
  $("cbPromo").addEventListener("submit", async (e) => {
    e.preventDefault();
    const code = $("cbPromoInput").value.trim();
    if (!code) return;
    const r = await call("/api/promo", { code });
    $("cbPromoMsg").hidden = false;
    $("cbPromoMsg").textContent = r.data.text || r.data.detail || "Не получилось";
    if (r.ok && r.data.me) { me = r.data.me; profile(); }
  });
  load();
})();
