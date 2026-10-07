/* Мои подборки для клиентов (/picks). API: /api/picks, /api/picks/<id>/items, /api/favorites.
   ?open=<id> — раскрыть подборку. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const money = (p, deal) => (!p ? "" : deal === "rent" ? `${Math.round(p / 1000).toLocaleString("ru-RU")} тыс. ₽/мес`
    : `${(p / 1e6).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} млн ₽`);
  const ago = (ts) => {
    if (!ts) return "";
    const m = Math.round((Date.now() / 1000 - ts) / 60);
    return m < 60 ? `${Math.max(1, m)} мин назад` : m < 1440 ? `${Math.round(m / 60)} ч назад` : new Date(ts * 1000).toLocaleDateString("ru-RU");
  };
  let list = [], favs = null, open = Number(new URLSearchParams(location.search).get("open")) || 0;

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
  const link = (p) => `${location.origin}/c/${p.token}`;

  async function load() {
    const r = await call("/api/picks");
    if (r.status === 401) { $("pksMsg").hidden = false; $("pksMsg").innerHTML = `Подборки доступны после входа. <a href="/?login=1">Войти</a>`; return; }
    if (r.status === 403) { $("pksMsg").hidden = false; $("pksMsg").innerHTML = `${esc(r.data.detail)} <a href="/?cabinet=1">Открыть кабинет</a>`; return; }
    list = r.data.items;
    $("pksApp").hidden = false;
    render();
  }

  function pickHTML(p) {
    const isOpen = p.id === open;
    const seen = p.views ? `клиент открывал ${p.views} ${p.views % 10 === 1 && p.views % 100 !== 11 ? "раз" : (p.views % 10 >= 2 && p.views % 10 <= 4 && (p.views % 100 < 12 || p.views % 100 > 14)) ? "раза" : "раз"}, последний — ${ago(p.viewed_at)}` : "клиент ещё не открывал";
    const items = p.items.map((it, i) => `<li class="pks-it${it.is_active ? "" : " gone"}">
        ${it.photo ? `<img src="${esc(it.photo)}" alt="" loading="lazy">` : `<span class="pks-noph"></span>`}
        <div><a href="/?open=${it.listing_id}" target="_blank"><b>${esc(it.title)}</b></a> <span>${esc(money(it.price, it.deal))}</span>
          ${it.is_active ? "" : `<small class="pks-gone">снят с сайта</small>`}
          <input class="pks-com" data-com="${it.listing_id}" data-pid="${p.id}" maxlength="1000" value="${esc(it.note || "")}" placeholder="Комментарий для клиента (необязательно)">
          <details class="pks-text"${it.text ? " open" : ""}><summary>✎ Текст объявления для клиента${it.text ? " · изменён" : ""}</summary>
            <textarea rows="6" maxlength="5000" data-text="${it.listing_id}" data-pid="${p.id}">${esc(it.text || it.orig || "")}</textarea>
            <div class="pks-text-acts"><button type="button" class="chip" data-text-save="${it.listing_id}" data-pid="${p.id}">Сохранить текст</button>
              ${it.text ? `<button type="button" class="chip" data-text-reset="${it.listing_id}" data-pid="${p.id}">Вернуть исходный</button>` : ""}
              <span class="note">Номера телефонов из текста убираем — клиент видит только ваш контакт.</span></div>
          </details></div>
        <div class="pks-it-acts">
          <button type="button" data-up="${i}" data-pid="${p.id}" ${i ? "" : "disabled"} aria-label="Выше">↑</button>
          <button type="button" data-down="${i}" data-pid="${p.id}" ${i < p.items.length - 1 ? "" : "disabled"} aria-label="Ниже">↓</button>
          <button type="button" data-rm="${it.listing_id}" data-pid="${p.id}" aria-label="Убрать">✕</button></div></li>`).join("");
    const inPick = new Set(p.items.map((x) => x.listing_id));
    const add = (favs || []).filter((f) => !inPick.has(f.id));
    return `<article class="pks-card${isOpen ? " open" : ""}" data-pick="${p.id}">
      <div class="pks-top" data-toggle="${p.id}">
        <div><h2>${esc(p.title)}</h2><span class="note">${p.n} ${p.n % 10 === 1 && p.n % 100 !== 11 ? "объект" : (p.n % 10 >= 2 && p.n % 10 <= 4 && (p.n % 100 < 12 || p.n % 100 > 14)) ? "объекта" : "объектов"} · ${esc(seen)}</span></div>
        <span class="pks-arrow">${isOpen ? "▲" : "▼"}</span>
      </div>
      ${p.contact_phone ? "" : `<p class="pks-warn">⚠ Укажите свой телефон в подборке (раскройте её) — клиент увидит только ваш контакт.</p>`}
      <div class="pks-share">
        <button type="button" class="pill" data-copy="${p.id}">Скопировать ссылку</button>
        <a class="pill light" href="https://wa.me/?text=${encodeURIComponent(`${p.title}\n${link(p)}`)}" target="_blank" rel="noopener">Отправить в WhatsApp</a>
        <a class="pill light" href="https://t.me/share/url?url=${encodeURIComponent(link(p))}&text=${encodeURIComponent(p.title)}" target="_blank" rel="noopener">Telegram</a>
        <a class="chip" href="/c/${esc(p.token)}" target="_blank">Как увидит клиент ↗</a>
      </div>
      ${isOpen ? `<form class="pks-edit" data-edit="${p.id}">
        <label class="agent-field"><span>Название</span><input name="title" maxlength="120" value="${esc(p.title)}"></label>
        <label class="agent-field"><span>Сообщение клиенту</span><textarea name="note" rows="3" maxlength="2000" placeholder="Например: подобрал варианты по вашему запросу, первые два — лучшие по цене">${esc(p.note || "")}</textarea></label>
        <div class="pks-two">
          <label class="agent-field"><span>Ваше имя</span><input name="contact_name" maxlength="100" value="${esc(p.contact_name || "")}" placeholder="Как клиенту вас называть"></label>
          <label class="agent-field"><span>Ваш телефон</span><input name="contact_phone" maxlength="30" inputmode="tel" value="${esc(p.contact_phone || "")}" placeholder="+7…"></label>
        </div>
        <div class="dlg-actions"><button type="button" class="ghost danger" data-del="${p.id}">Удалить подборку</button><button type="submit" class="pill">Сохранить</button></div>
      </form>
      <h3 class="pks-h">Объекты · ${p.items.length}</h3>
      <ol class="pks-items">${items || `<li class="note">Пока пусто — добавьте из избранного ниже или кнопкой «📁 В подборку» в окне объекта.</li>`}</ol>
      <details class="pks-add" ${p.items.length ? "" : "open"}><summary>+ Добавить из избранного${favs ? ` · ${add.length}` : ""}</summary>
        ${favs === null ? `<p class="note">Загружаем…</p>` : add.length ? `<ul>${add.map((f) => `<li>
          ${f.photos && f.photos[0] ? `<img src="${esc(f.photos[0])}" alt="" loading="lazy">` : `<span class="pks-noph"></span>`}
          <span><b>${esc(f.title)}</b> ${esc(money(f.price, f.deal))}</span>
          <button type="button" class="chip" data-add="${f.id}" data-pid="${p.id}">+ Добавить</button></li>`).join("")}</ul>`
          : `<p class="note">Всё избранное уже в подборке. Добавьте объекты в избранное ♡ на главной.</p>`}</details>` : ""}
    </article>`;
  }

  function render() {
    $("pksList").innerHTML = list.map(pickHTML).join("") || `<p class="note">Подборок пока нет — создайте первую выше.</p>`;
  }
  async function reload() {
    const r = await call("/api/picks");
    if (r.ok) list = r.data.items;
    render();
  }
  async function loadFavs() {
    if (favs !== null) return;
    const r = await call("/api/favorites");
    favs = r.ok ? r.data.items : [];
    render();
  }

  document.addEventListener("click", async (e) => {
    const t = e.target.closest("button, [data-toggle]");
    if (!t) return;
    if (t.dataset.toggle) { open = open === Number(t.dataset.toggle) ? 0 : Number(t.dataset.toggle); render(); if (open) loadFavs(); return; }
    const pid = Number(t.dataset.pid);
    if (t.dataset.copy) {
      const p = list.find((x) => x.id === Number(t.dataset.copy));
      try { await navigator.clipboard.writeText(link(p)); toast("✓ Ссылка скопирована — отправьте её клиенту"); }
      catch { prompt("Скопируйте ссылку:", link(p)); }
      return;
    }
    if (t.dataset.add) {
      const r = await call(`/api/picks/${pid}/items`, { listing_id: Number(t.dataset.add), add: true });
      if (!r.ok) { toast(r.data.detail || "Не получилось"); return; }
      toast("✓ Добавлено"); await reload(); return;
    }
    if (t.dataset.textSave || t.dataset.textReset) {
      const lid = Number(t.dataset.textSave || t.dataset.textReset);
      const ta = document.querySelector(`textarea[data-text="${lid}"][data-pid="${pid}"]`);
      const text = t.dataset.textReset ? "" : ta.value;
      const r = await call(`/api/picks/${pid}/items`, { listing_id: lid, add: true, text });
      if (!r.ok) { toast(r.data.detail || "Не сохранилось"); return; }
      toast(t.dataset.textReset ? "Вернули исходный текст" : "✓ Текст для клиента сохранён"); await reload(); return;
    }
    if (t.dataset.rm) {
      await call(`/api/picks/${pid}/items`, { listing_id: Number(t.dataset.rm), add: false });
      await reload(); return;
    }
    if (t.dataset.up !== undefined || t.dataset.down !== undefined) {
      const p = list.find((x) => x.id === pid);
      const ids = p.items.map((x) => x.listing_id);
      const i = Number(t.dataset.up ?? t.dataset.down), j = t.dataset.up !== undefined ? i - 1 : i + 1;
      [ids[i], ids[j]] = [ids[j], ids[i]];
      await call(`/api/picks/${pid}/items`, { order: ids });
      await reload(); return;
    }
    if (t.dataset.del) {
      if (!confirm("Удалить подборку? Ссылка у клиента перестанет открываться.")) return;
      await call("/api/picks", { id: Number(t.dataset.del) }, "DELETE");
      open = 0; toast("Подборка удалена"); await reload(); return;
    }
  });
  document.addEventListener("submit", async (e) => {
    const f = e.target;
    if (f.id === "pksNew") {
      e.preventDefault();
      const r = await call("/api/picks", { title: f.title.value });
      if (!r.ok) { toast(r.data.detail || "Не получилось"); return; }
      f.reset(); open = r.data.pick.id; await reload(); loadFavs(); return;
    }
    if (f.dataset.edit) {
      e.preventDefault();
      const r = await call("/api/picks", { id: Number(f.dataset.edit), title: f.title.value, note: f.note.value,
        contact_name: f.contact_name.value, contact_phone: f.contact_phone.value });
      if (!r.ok) { toast(r.data.detail || "Не получилось"); return; }
      toast("✓ Сохранено"); await reload();
    }
  });
  // комментарий к объекту — сохраняем, когда поле теряет фокус
  document.addEventListener("change", async (e) => {
    const inp = e.target;
    if (!inp.dataset || !inp.dataset.com) return;
    const r = await call(`/api/picks/${inp.dataset.pid}/items`, { listing_id: Number(inp.dataset.com), add: true, note: inp.value });
    toast(r.ok ? "✓ Комментарий сохранён" : r.data.detail || "Не сохранилось", 1500);
  });

  load().then(() => { if (open) loadFavs(); if (location.search) history.replaceState(null, "", location.pathname); });
})();
