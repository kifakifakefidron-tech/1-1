/* «В работе»: избранное и объекты со статусом — вкладки по статусам, статус одним нажатием, заметка сохраняется сама,
   видно снижение цены и «снят с сайта». API: /api/favorites, /api/notes/{id} {status|text}, /api/favorites/{id} (снять ♡).
   Ссылка: /work?status=call|show|think|deal|refuse|none */
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
    deal: { t: "Сделка", c: "lime", help: "Успешные сделки." },
    refuse: { t: "Отказ", c: "coral", help: "Не подошло — останется для истории, можно вернуть." },
  };
  let items = [], tab = new URLSearchParams(location.search).get("status") || "all", q = "", sort = "touched";
  if (tab !== "all" && !ST[tab]) tab = "all";

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
  const st = (o) => o.status || "none";
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
    let list = items.filter((o) => (tab === "all" || st(o) === tab)
      && words.every((w) => [o.title, o.complex, o.district, o.street, o.note].filter(Boolean).join(" ").toLowerCase().includes(w)));
    const by = { touched: (a, b) => b.touched - a.touched, price_asc: (a, b) => (a.price || 9e15) - (b.price || 9e15),
      price_desc: (a, b) => (b.price || 0) - (a.price || 0), drop: (a, b) => drop(a) - drop(b) };
    return list.sort(by[sort] || by.touched);
  }

  function renderTabs() {
    const n = (k) => items.filter((o) => k === "all" || st(o) === k).length;
    const keys = ["all", "none", "call", "show", "think", "deal", "refuse"];
    const nav = $("wkTabs");
    nav.innerHTML = `<i class="wk-ind" aria-hidden="true"></i>` + keys.map((k) => `<button type="button" class="wk-tab${k === tab ? " on" : ""} ${k === "all" ? "" : "c-" + ST[k].c}"
      data-tab="${k}" aria-pressed="${k === tab}">${k === "all" ? "" : "<i></i>"}${k === "all" ? "Все" : ST[k].t}<b>${n(k)}</b></button>`).join("");
    requestAnimationFrame(slide);
    $("wkHelp").textContent = tab === "all" ? "Статус меняется одним нажатием прямо на карточке. Заметка сохраняется сама." : ST[tab].help;
    const work = items.filter((o) => ["call", "show", "think"].includes(o.status)).length;
    $("wkSub").textContent = items.length ? `${items.length} в списке · ${work} в работе` : "Избранное и объекты, с которыми вы работаете";
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
    const d = drop(o), s = st(o);
    const photo = (o.photos || [])[0];
    return `<article class="wk-card c-${ST[s].c}${o.is_active ? "" : " gone"}" data-id="${o.id}">
      <a class="wk-photo" href="/?open=${o.id}">${photo ? `<img src="${esc(photo)}" alt="" loading="lazy">` : `<span>${ic("home")}<small>Фото у агента</small></span>`}
        ${o.is_active ? "" : `<em class="wk-gone">Снят с сайта</em>`}
        ${d ? `<em class="wk-drop ${d < 0 ? "down" : "up"}">${ic(d < 0 ? "trend-down" : "trend")}${d < 0 ? "−" : "+"}${short(Math.abs(d))}</em>` : ""}</a>
      <div class="wk-body">
        <div class="wk-top"><a class="wk-title" href="/?open=${o.id}">${esc(head(o))}</a>
          <button type="button" class="wk-x" data-remove="${o.id}" title="Убрать из списка" aria-label="Убрать из списка">${ic("x")}</button></div>
        <b class="wk-price">${esc(money(o.price, o.deal))}</b>
        <p class="wk-what">${esc(o.title)}${place(o) ? ` · ${esc(place(o))}` : ""}</p>
        <div class="wk-st" role="radiogroup" aria-label="Статус">${Object.entries(ST).filter(([k]) => k !== "none").map(([k, v]) =>
          `<button type="button" role="radio" aria-checked="${k === s}" class="wk-s c-${v.c}${k === s ? " on" : ""}" data-status="${k}" data-id="${o.id}">${v.t}</button>`).join("")}</div>
        <textarea class="wk-note" data-note="${o.id}" rows="2" maxlength="2000" placeholder="Заметка: о чём договорились, что важно клиенту…">${esc(o.note)}</textarea>
        <div class="wk-foot">
          <span class="wk-when">${ic("clock")}${ago(o.touched)}</span>
          <a class="wk-btn" href="/planner?add=show&listing=${o.id}">${ic("calendar")}Запланировать</a>
          <a class="wk-btn" href="/?open=${o.id}">${ic("external")}Открыть</a>
        </div>
      </div></article>`;
  }
  function render() {
    renderTabs();
    const list = visible();
    $("wkList").innerHTML = list.map(card).join("") || `<div class="wk-empty">${ic(items.length ? "search" : "heart")}
      <b>${items.length ? "Здесь пока пусто" : "Список пуст"}</b>
      ${items.length ? "В этой вкладке нет объектов." : "Нажмите ♡ на объекте в поиске — он появится здесь. Потом ставьте статусы: звонил, показ, думает."}
      ${items.length ? "" : `<a class="pill" href="/">Перейти к поиску</a>`}</div>`;
  }

  async function load() {
    const r = await call("/api/favorites");
    if (r.status === 401) { $("wkGuest").hidden = false; $("wkTabs").hidden = true; return; }
    items = r.data.items || [];
    render();
  }

  document.addEventListener("click", async (e) => {
    const t = e.target.closest("button");
    if (!t) return;
    if (t.dataset.tab) {
      tab = t.dataset.tab;
      history.replaceState(null, "", tab === "all" ? "/work" : `/work?status=${tab}`);
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
      if (!confirm("Убрать объект из списка? Статус и заметка тоже удалятся.")) return;
      if (o.favorite) await call(`/api/favorites/${o.id}`, {});
      if (o.status || o.note) await call(`/api/notes/${o.id}`, { status: "", text: "" });
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
  window.addEventListener("resize", () => slide(false));
  load();
})();
