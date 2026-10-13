/* «В работе» (ТЗ v2): закладки (избранное) без своих статусов — статусы живут у клиентов. На карточке видно, каким
   клиентам объект отправлен и что с ним (отправлен, понравился, показ…), фильтр по клиенту, «Показ» — сразу в планер.
   Заметка сохраняется сама, видно снижение цены и «снят с сайта». API: /api/favorites, /api/notes/{id} {text}. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const ic = (n) => (window.ic ? window.ic(n) : "");
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  // по порядку работы с объектом
  const ST = {
    none: { t: "Без статуса", c: "slate", help: "Просто в избранном. Поставьте статус, когда начнёте работать с объектом." },
    call: { t: "Звонил", c: "sky", help: "Связались с агентом — дальше показ или «думает»." },
    show: { t: "Показ", c: "gold", help: "Объекты, которые показывали или покажете. Запланируйте показ в планере." },
    think: { t: "Думает", c: "violet", help: "Клиент думает — не забудьте перезвонить." },
    refuse: { t: "Отказ", c: "coral", help: "Не подошло — останется для истории, можно вернуть." },
  };
  let items = [], hiddenItems = [], tab = new URLSearchParams(location.search).get("tab") || "all", q = "", sort = "touched", clientF = 0;
  if (!["all", "with", "without", "hidden"].includes(tab)) tab = "all";
  const TABS = { all: "Все закладки", with: "У клиентов", without: "Без клиента", hidden: "Скрытые" };
  const PC = { matched: "slate", sent: "sky", opened: "sky", liked: "ok", rejected: "bad", show: "gold", talks: "violet", deal: "ok", lost: "bad" };
  const REASONS = { sold: "Продан или снят", no_coop: "Не сотрудничаю", not_fit: "Не подходит", other: "Скрыт" };

  async function call(url, body, method) {
    const opts = { credentials: "same-origin", method: method || (body !== undefined ? "POST" : "GET") };
    if (body !== undefined) { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(body); }
    const r = await fetch(url, opts);
    let d = {};
    try { d = await r.json(); } catch { /* */ }
    return { ok: r.ok, status: r.status, data: d };
  }
  function toast(text, ms = 2200) {
    const t = $("toast");
    t.textContent = text;
    try { t.showPopover(); } catch { t.hidden = false; }
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { try { t.hidePopover(); } catch { t.hidden = true; } }, ms);
  }
  const money = (p, deal) => (!p ? "цена не указана" : deal === "rent" ? `${Math.round(p / 1000).toLocaleString("ru-RU")} тыс. ₽/мес`
    : p >= 1e6 ? `${(p / 1e6).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} млн ₽` : `${p.toLocaleString("ru-RU")} ₽`);
  const short = (n) => (Math.abs(n) >= 1e6 ? `${(n / 1e6).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} млн` : `${Math.round(n / 1000).toLocaleString("ru-RU")} тыс`);
  const head = (o) => (o.complex ? `ЖК ${o.complex}` : o.district || (o.street ? `ул. ${o.street}` : o.title));
  const place = (o) => [o.district, o.street && `ул. ${o.street}${o.house ? ", " + o.house : ""}`].filter(Boolean).join(" · ");
  const st = (o) => (o.status && ST[o.status] ? o.status : "none");
  const ago = (ts) => {
    const d = Math.floor((Date.now() / 1000 - ts) / 86400);
    return d <= 0 ? "сегодня" : d === 1 ? "вчера" : d < 7 ? `${d} дн. назад` : new Date(ts * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
  };
  // насколько изменилась цена с момента, когда объект сохранили (или с прошлой цены)
  const drop = (o) => {
    const was = o.fav_price && o.fav_price !== o.price ? o.fav_price : o.prev_price;
    return was && o.price ? o.price - was : 0;
  };

  function visible() {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean);
    let list = items.filter((o) => (tab === "all" || (tab === "with" ? o.clients.length : !o.clients.length))
      && (!clientF || o.clients.some((c) => c.id === clientF))
      && words.every((w) => [o.title, o.complex, o.district, o.street, o.note].filter(Boolean).join(" ").toLowerCase().includes(w)));
    const by = { touched: (a, b) => b.touched - a.touched, price_asc: (a, b) => (a.price || 9e15) - (b.price || 9e15),
      price_desc: (a, b) => (b.price || 0) - (a.price || 0), drop: (a, b) => drop(a) - drop(b) };
    return list.sort(by[sort] || by.touched);
  }

  function renderTabs() {
    const n = (k) => (k === "hidden" ? hiddenItems.length : items.filter((o) => k === "all" || (k === "with" ? o.clients.length : !o.clients.length)).length);
    const nav = $("wkTabs");
    nav.innerHTML = `<i class="wk-ind" aria-hidden="true"></i>` + Object.keys(TABS).map((k) => `<button type="button" class="wk-tab${k === tab ? " on" : ""}"
      data-tab="${k}" aria-pressed="${k === tab}">${TABS[k]}<b>${n(k)}</b></button>`).join("");
    requestAnimationFrame(slide);
    $("wkHelp").textContent = tab === "hidden" ? "Объекты, которые вы скрыли для себя: в поиске и на карте их не видно. «Вернуть» — снова показывать."
      : "Закладки — объекты, отмеченные сердечком. Статусы ставятся клиентам: в окне объекта «Клиенту ▾» или в подборке клиента.";
    const withC = items.filter((o) => o.clients.length).length;
    $("wkSub").textContent = items.length ? `${items.length} в закладках · ${withC} отправлены клиентам` : "Закладки и объекты, с которыми вы работаете";
    // фильтр по клиенту
    const sel = $("wkClient");
    const used = [...new Map(items.flatMap((o) => o.clients).map((c) => [c.id, c.name])).entries()];
    sel.hidden = !used.length;
    if (used.length && sel.options.length !== used.length + 1) {
      sel.innerHTML = `<option value="">Все клиенты</option>` + used.map(([id, nm]) => `<option value="${id}"${id === clientF ? " selected" : ""}>${esc(nm)}</option>`).join("");
      if (window.Pick) Pick.select(sel, { searchPlaceholder: "Найти клиента" });
    }
  }
  function slide(animate = true) {   // плашка выбранной вкладки плавно переезжает
    const nav = $("wkTabs"), ind = nav.querySelector(".wk-ind"), b = nav.querySelector(".wk-tab.on");
    if (!ind || !b) return;
    if (!animate) ind.style.transition = "none";
    ind.style.width = `${b.offsetWidth}px`;
    ind.style.transform = `translateX(${b.offsetLeft}px)`;
    if (!animate) { void ind.offsetWidth; ind.style.transition = ""; }
  }

  function card(o) {
    const d = drop(o);
    const photo = (o.photos || [])[0];
    const cls = o.clients.length ? PC[o.clients[0].status] || "sky" : "slate";
    return `<article class="wk-card c-${cls}${o.is_active ? "" : " gone"}${photo ? "" : " nophoto"}" data-id="${o.id}">
      <a class="wk-photo" href="/?open=${o.id}">${photo ? `<img src="${esc(photo)}" alt="" loading="lazy">` : `<span>${ic("home")}<small>Фото у агента</small></span>`}
        ${o.is_active ? "" : `<em class="wk-gone">Снят с сайта</em>`}
        ${d ? `<em class="wk-drop ${d < 0 ? "down" : "up"}">${ic(d < 0 ? "trend-down" : "trend")}${d < 0 ? "−" : "+"}${short(Math.abs(d))}</em>` : ""}</a>
      <div class="wk-body">
        <div class="wk-top"><a class="wk-title" href="/?open=${o.id}">${esc(head(o))}</a>
          <button type="button" class="wk-x" data-remove="${o.id}" title="Убрать из списка" aria-label="Убрать из списка">${ic("x")}</button></div>
        <b class="wk-price">${esc(money(o.price, o.deal))}</b>
        <p class="wk-what">${esc(o.title)}${place(o) ? ` · ${esc(place(o))}` : ""}</p>
        <div class="wk-cl">${o.clients.length ? o.clients.map((c) => `<a class="wk-c p-${PC[c.status] || "sky"}" href="/clients?id=${c.id}" title="Карточка клиента">
            ${ic("user")}<span>${esc(c.name)}</span><b>${esc(c.label)}</b></a>`).join("")
          : `<span class="wk-c none">Не отправлен клиентам — в окне объекта «Клиенту ▾»</span>`}</div>
        <textarea class="wk-note" data-note="${o.id}" rows="2" maxlength="2000" placeholder="Заметка: о чём договорились, что важно клиенту…">${esc(o.note)}</textarea>
        <div class="wk-foot">
          <span class="wk-when">${ic("clock")}${ago(o.touched)}</span>
          <a class="wk-btn" href="/planner?add=show&listing=${o.id}${o.clients.length === 1 ? `&client=${o.clients[0].id}` : ""}">${ic("calendar")}Показ</a>
          <a class="wk-btn" href="/?open=${o.id}">${ic("external")}Открыть</a>
        </div>
      </div></article>`;
  }
  function hiddenCard(o) {
    const photo = (o.photos || [])[0];
    return `<article class="wk-card c-slate gone" data-hid="${o.id}">
      <a class="wk-photo" href="/?open=${o.id}">${photo ? `<img src="${esc(photo)}" alt="" loading="lazy">` : `<span>${ic("home")}<small>Фото у агента</small></span>`}
        <em class="wk-gone">${esc(REASONS[o.reason] || "Скрыт")}</em></a>
      <div class="wk-body">
        <a class="wk-title" href="/?open=${o.id}">${esc(head(o))}</a>
        <b class="wk-price">${esc(money(o.price, o.deal))}</b>
        <p class="wk-what">${esc(o.title)}${place(o) ? ` · ${esc(place(o))}` : ""}</p>
        <div class="wk-foot"><span class="wk-when">${ic("eye")}скрыт ${ago(o.hidden_at)}</span>
          <button type="button" class="pill" data-unhide="${o.id}">Вернуть</button></div>
      </div></article>`;
  }
  function render() {
    renderTabs();
    if (tab === "hidden") {
      const words = q.toLowerCase().split(/\s+/).filter(Boolean);
      const list = hiddenItems.filter((o) => words.every((w) => [o.title, o.complex, o.district, o.street].filter(Boolean).join(" ").toLowerCase().includes(w)));
      $("wkList").innerHTML = list.map(hiddenCard).join("") || `<div class="wk-empty">${ic("eye")}<b>Скрытых нет</b>
        В окне объекта есть кнопка «Скрыть» — если объект продан или вы не сотрудничаете, он пропадёт только у вас.</div>`;
      return;
    }
    const list = visible();
    $("wkList").innerHTML = list.map(card).join("") || `<div class="wk-empty">${ic(items.length ? "search" : "heart")}
      <b>${items.length ? "Здесь пока пусто" : "Список пуст"}</b>
      ${items.length ? "В этой вкладке нет объектов." : "Нажмите ♡ на объекте в поиске — он появится здесь. Клиентам объекты отправляются кнопкой «Клиенту ▾»."}
      ${items.length ? "" : `<a class="pill" href="/">Перейти к поиску</a>`}</div>`;
  }

  async function load() {
    const r = await call("/api/favorites");
    if (r.status === 401) { $("wkGuest").hidden = false; $("wkTabs").hidden = true; return; }
    items = r.data.items || [];
    const h = await call("/api/hidden");
    hiddenItems = (h.ok && h.data.items) || [];
    render();
  }

  document.addEventListener("click", async (e) => {
    const t = e.target.closest("button");
    if (!t) return;
    if (t.dataset.unhide) {
      const id = Number(t.dataset.unhide);
      await call("/api/hidden", { id, hide: false });
      hiddenItems = hiddenItems.filter((o) => o.id !== id);
      const el = document.querySelector(`.wk-card[data-hid="${id}"]`);
      if (el) { el.classList.add("bye"); setTimeout(render, 260); } else render();
      toast("Объект снова виден в поиске");
      return;
    }
    if (t.dataset.tab) {
      tab = t.dataset.tab;
      history.replaceState(null, "", tab === "all" ? "/work" : `/work?tab=${tab}`);
      render(); return;
    }
    if (t.dataset.status) {
      const o = items.find((x) => x.id === Number(t.dataset.id));
      const next = o.status === t.dataset.status ? "" : t.dataset.status;   // повторное нажатие — снять статус
      o.status = next || null; o.touched = Math.floor(Date.now() / 1000);
      const r = await call(`/api/notes/${o.id}`, { status: next });
      if (!r.ok) { toast("Не сохранилось — попробуйте ещё раз"); return; }
      toast(next ? `Статус: ${ST[next].t}` : "Статус снят");
      const el = document.querySelector(`.wk-card[data-id="${o.id}"]`);
      if (tab !== "all" && st(o) !== tab && el) { el.classList.add("bye"); setTimeout(render, 260); } else render();
      return;
    }
    if (t.dataset.remove) {
      const o = items.find((x) => x.id === Number(t.dataset.remove));
      if (!confirm("Убрать из закладок? Личная заметка тоже удалится (у клиентов объект останется).")) return;
      if (o.favorite) await call(`/api/favorites/${o.id}`, {});
      if (o.note) await call(`/api/notes/${o.id}`, { text: "" });
      items = items.filter((x) => x !== o);
      const el = document.querySelector(`.wk-card[data-id="${o.id}"]`);
      if (el) { el.classList.add("bye"); setTimeout(render, 260); } else render();
      toast("Убрано");
    }
  });
  let noteTimer = 0;
  document.addEventListener("input", (e) => {
    const id = Number(e.target.dataset && e.target.dataset.note);
    if (!id) return;
    const o = items.find((x) => x.id === id);
    o.note = e.target.value;
    clearTimeout(noteTimer);
    noteTimer = setTimeout(async () => {
      const r = await call(`/api/notes/${id}`, { text: o.note });
      if (r.ok) { e.target.classList.add("saved"); setTimeout(() => e.target.classList.remove("saved"), 900); }
    }, 700);
  });
  let qTimer = 0;
  $("wkQ").addEventListener("input", (e) => { clearTimeout(qTimer); qTimer = setTimeout(() => { q = e.target.value.trim(); render(); }, 200); });
  $("wkSort").addEventListener("change", (e) => { sort = e.target.value; render(); });
  $("wkClient").addEventListener("change", (e) => { clientF = Number(e.target.value) || 0; render(); });
  window.addEventListener("resize", () => slide(false));
  load();
})();
