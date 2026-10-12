/* Кабинет риелтора (/cabinet): стеклянный дашборд — день, объекты в работе, уведомления, рынок,
   погода, ключевая ставка, ипотека, курсы, калькулятор, доступ, поиски, приглашение, сводка админа.
   Данные — один запрос /api/dashboard (+ /api/me, /api/meta, /api/saved). */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ic = (n) => (window.ic ? window.ic(n) : "");
  const num = (x, d = 0) => Number(x || 0).toLocaleString("ru-RU", { maximumFractionDigits: d, minimumFractionDigits: d });
  const pct = (x) => `${num(x, 2).replace(/,?0+$/, "")}%`;
  const plural = (n, a, b, c) => { const m10 = n % 10, m100 = n % 100; return m10 === 1 && m100 !== 11 ? a : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? b : c; };
  const dayLong = (ts) => new Date(ts * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
  const hhmm = (ts) => new Date(ts * 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  const ago = (ts) => {
    const m = Math.round((Date.now() / 1000 - ts) / 60);
    return m < 1 ? "только что" : m < 60 ? `${m} мин назад` : m < 1440 ? `${Math.round(m / 60)} ч назад` : dayLong(ts);
  };
  const money = (p) => (p >= 1e6 ? `${num(p / 1e6, p >= 1e7 ? 1 : 2).replace(/,?0+$/, "")} млн ₽` : `${num(p)} ₽`);
  let me = null, meta = {}, dash = null;

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
  const head = (icon, title, link) => `<div class="g-h"><p class="g-t">${ic(icon)}${title}</p>${link || ""}</div>`;
  const put = (id, html, i) => { const el = $(id); el.innerHTML = html; if (i != null) el.style.setProperty("--i", i); };

  // ─── шапка ─────────────────────────────────────────────────────────
  function header() {
    const now = new Date();
    const h = now.getHours();
    const hi = h < 5 ? "Доброй ночи" : h < 12 ? "Доброе утро" : h < 18 ? "Добрый день" : "Добрый вечер";
    const name = (me.name || "").trim().split(/\s+/)[0] || (me.email || "").split("@")[0] || "коллега";
    $("dHello").textContent = `${hi}, ${name}`;
    $("dDate").textContent = now.toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" });
    const letter = (name[0] || "1").toUpperCase();
    $("dAva").textContent = letter;
    $("dAva2").textContent = letter;
    $("dName").textContent = me.name || me.email || "Коллега";
    $("dMail").textContent = [me.name && me.email, me.tg_username && "@" + me.tg_username].filter(Boolean).join(" · ");
    const left = me.access_until ? Math.max(0, Math.ceil((me.access_until - Date.now() / 1000) / 86400)) : 0;
    const acc = $("dAccess");
    if (me.is_admin) { acc.className = "d-access gold"; acc.textContent = "Администратор"; }
    else if (me.access) { acc.className = "d-access ok"; acc.textContent = `Доступ · ещё ${left} ${plural(left, "день", "дня", "дней")}`; }
    else { acc.className = "d-access off"; acc.textContent = "Без доступа к номерам"; }
  }

  // ─── погода ────────────────────────────────────────────────────────
  function weather(w) {
    if (!w) { put("wWeather", `${head("sun", "Погода · Краснодар")}<p class="g-empty">Прогноз загрузится через минуту.</p>`, 0); return; }
    const c = w.data.current;
    const days = (w.data.days || []).slice(0, 5).map((d, i) => {
      const dt = new Date(`${d.date}T12:00:00`);
      const lbl = i === 0 ? "Сегодня" : dt.toLocaleDateString("ru-RU", { weekday: "short" });
      return `<div title="${esc(d.label)}${d.rain >= 1 ? `, осадки ${num(d.rain, 1)} мм` : ""}"><span>${lbl}</span>${ic(d.icon)}<b>${d.max}°</b><i>${d.min}°</i></div>`;
    }).join("");
    put("wWeather", `${head("pin", "Погода · Краснодар")}
      <div class="ww-now"><div><div class="g-big">${c.temp}<small>°C</small></div><p class="g-sub">${esc(c.label)}</p></div>
        <span class="ww-ic ${c.icon}">${ic(c.icon)}</span></div>
      <div class="ww-meta"><span>${ic("wind")}${c.wind} м/с</span><span>${ic("drop")}${c.humidity}%</span></div>
      <div class="ww-days">${days}</div>`, 0);
  }

  // ─── ключевая ставка (с графиком за год) ──────────────────────────
  function spark(hist) {
    if (!hist || hist.length < 2) return "";
    const W = 300, H = 52, vs = hist.map((p) => p.rate), lo = Math.min(...vs) - .5, hi = Math.max(...vs) + .5;
    const pts = hist.map((p, i) => [i / (hist.length - 1) * W, H - (p.rate - lo) / (hi - lo) * H]);
    const d = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" ");
    return `<svg class="wr-spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">
      <defs><linearGradient id="sparkFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#e3cb8f" stop-opacity=".28"/><stop offset="1" stop-color="#e3cb8f" stop-opacity="0"/></linearGradient></defs>
      <path class="a" d="${d} L${W} ${H} L0 ${H} Z"/><path class="l" pathLength="1" d="${d}"/></svg>`;
  }
  function rate(k) {
    if (!k) { put("wRate", `${head("bank", "Ключевая ставка ЦБ")}<p class="g-empty">Данные Банка России загрузятся через минуту.</p>`, 1); return; }
    const r = k.data, diff = r.prev != null ? r.rate - r.prev : 0;
    const since = new Date(`${r.since}T12:00:00`).toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
    put("wRate", `${head("bank", "Ключевая ставка ЦБ")}
      <div class="g-big">${num(r.rate, 2).replace(/,?0+$/, "")}<small>%</small></div>
      <p class="g-sub">с ${since}${r.prev != null ? ` · было ${pct(r.prev)} <span class="chg ${diff < 0 ? "down" : "up"}">${ic(diff < 0 ? "arrow-down" : "arrow-up")}${num(Math.abs(diff), 2).replace(/,?0+$/, "")}</span>` : ""}</p>
      ${spark(r.history)}`, 1);
  }

  // ─── ипотека в крае (ЦБ) + ставки банков ──────────────────────────
  let mortRates = {};
  const PROG = { secondary: "Вторичка", new: "Новостр.", family: "Семейная", ihc: "ИЖС" };
  function mortgage(m, banks) {
    const b = (banks && banks.items) || [];
    const d = m ? m.data : null;
    const edit = me.is_admin ? `<button type="button" class="wm-edit" id="banksEdit">${b.length ? "Изменить ставки банков" : "+ Внести ставки банков"}</button>` : "";
    const best = (k) => { const v = b.map((x) => x[k]).filter((x) => x != null); return v.length ? Math.min(...v) : null; };
    mortRates = { sec: best("secondary") ?? (d && d.secondary), new: best("new") ?? (d && d.new), family: best("family") };
    const avg = d ? `<p class="g-sub wm-avg">В среднем по краю за ${esc((d.month || "").toLowerCase())} (ЦБ): вторичка ${d.secondary != null ? pct(d.secondary) : "—"},
      новостройки ${pct(d.new)}</p>` : "";
    if (!b.length) {   // ставок банков ещё нет — показываем средние ЦБ
      put("wMort", `${head("percent", "Ипотека · Краснодарский край")}
        ${d ? `<div class="wm-rows">${d.secondary != null ? `<div class="wm-row main"><span>Вторичка</span><b>${pct(d.secondary)}</b></div>` : ""}
          <div class="wm-row"><span>Новостройки</span><b>${pct(d.new)}</b></div></div>
          <p class="g-sub">Средние ставки по выданным кредитам за ${esc((d.month || "").toLowerCase())} (ЦБ). Ставки конкретных банков появятся, когда их внесут.</p>`
          : `<p class="g-empty">Данные загрузятся позже.</p>`}${edit}`, 2);
      return;
    }
    const cols = Object.keys(PROG).filter((k) => b.some((x) => x[k] != null));
    const bs = best("secondary");
    const upd = banks.ts ? new Date(banks.ts * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "short" }) : "";
    put("wMort", `${head("percent", "Ипотека · ставки банков", upd ? `<span class="g-sub" style="margin:0">на ${upd}</span>` : "")}
      ${bs != null ? `<div class="wm-best"><span>Вторичка — от</span><b>${pct(bs)}</b></div>` : ""}
      <table class="wm-t"><thead><tr><th>Банк</th>${cols.map((k) => `<th>${PROG[k]}</th>`).join("")}</tr></thead>
        <tbody>${b.slice(0, 8).map((x) => `<tr><td>${esc(x.bank)}</td>${cols.map((k) => `<td${x[k] != null && x[k] === best(k) ? ' class="top"' : ""}>${x[k] != null ? pct(x[k]) : "—"}</td>`).join("")}</tr>`).join("")}</tbody></table>
      ${avg}${edit}`, 2);
  }

  // ─── курсы ЦБ ─────────────────────────────────────────────────────
  function fx(c) {
    if (!c) { put("wFx", `${head("coins", "Курсы ЦБ")}<p class="g-empty">Загрузятся через минуту.</p>`, 3); return; }
    const sym = { USD: "$", EUR: "€", CNY: "¥" };
    put("wFx", `${head("coins", "Курсы ЦБ", `<span class="g-sub" style="margin:0">на ${esc(c.data.date)}</span>`)}
      ${c.data.items.map((x) => `<div class="wf-row"><span>${sym[x.code] || ""} ${x.code}</span><b>${num(x.value, 2)} ₽</b>
        ${x.change != null ? `<span class="chg ${x.change > 0 ? "up" : "down"}">${ic(x.change > 0 ? "arrow-up" : "arrow-down")}${num(Math.abs(x.change), 2)}</span>` : "<span></span>"}</div>`).join("")}`, 3);
  }

  // ─── мой день ─────────────────────────────────────────────────────
  const KIND = { show: ["home", "Показ"], call: ["phone", "Созвон"], meet: ["users", "Встреча"], task: ["check", "Задача"] };
  function day() {
    const t0 = dash.today, t1 = t0 + 86400;
    const evRow = (e) => {
      const [icon, label] = KIND[e.kind] || ["calendar", "Событие"];
      const what = e.title || label;
      const sub = [e.listing && (e.listing.complex ? `ЖК ${e.listing.complex}` : e.listing.title), e.contact_name, e.place].filter(Boolean).join(" · ");
      return `<a class="wd-ev${e.done ? " done" : ""}" href="/planner?event=${e.id}"><span class="wd-time">${hhmm(e.ts)}</span>
        <span class="wd-kind">${ic(icon)}</span><span class="wd-what"><b>${esc(what)}</b><small>${esc(sub || label)}</small></span>
        <span>${e.done ? ic("check") : ""}</span></a>`;
    };
    const today = dash.agenda.filter((e) => e.ts < t1), tomorrow = dash.agenda.filter((e) => e.ts >= t1);
    const group = (t, list) => list.length ? `<div class="wd-group"><p class="wd-gt">${t}</p>${list.map(evRow).join("")}</div>` : "";
    const body = today.length || tomorrow.length ? group("Сегодня", today) + group("Завтра", tomorrow)
      : `<p class="g-empty">На сегодня и завтра ничего не запланировано. Добавьте показ или созвон — напомним заранее на сайте и почтой.</p>`;
    put("wDay", `${head("calendar", "Мой день", dash.overdue ? `<a class="wd-over" href="/planner">${ic("alert")}просрочено: ${dash.overdue}</a>` : `<a class="g-link" href="/planner">Планер →</a>`)}
      ${body}
      <div class="wd-acts"><a class="pill light" href="/planner?add=show">${ic("plus")}Показ</a><a class="pill light" href="/planner?add=call">${ic("plus")}Созвон</a>
        <a class="pill light" href="/planner?add=task">${ic("plus")}Задача</a></div>`, 4);
  }

  // ─── клиенты (CRM) ───────────────────────────────────────────────
  const CL_C = { new: "#b8c8e4", search: "#8fd1cb", shows: "#e3cb8f", talks: "#d9b3e6", deposit: "#f0b77a", deal: "#a7d1b6", lost: "#e2a596" };
  function clientsW() {
    const c = dash.clients || { stages: [], next: [], total: 0 };
    const max = Math.max(1, ...c.stages.map((s) => s.n));
    const nxt = c.next.map((x) => {
      const over = x.next_ts * 1000 < Date.now();
      return `<a class="wn-it${over ? " unread" : ""}" href="/clients?id=${x.id}"><b>${esc(x.name)} — ${esc(x.next_text || "связаться")}</b>
        <small${over ? ' style="color:var(--bad)"' : ""}>${new Date(x.next_ts * 1000).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</small></a>`;
    }).join("");
    put("wClients", `${head("users", "Клиенты", `<a class="g-link" href="/clients">Все клиенты →</a>`)}
      ${c.total ? `<div class="wc-pipe cl">${c.stages.filter((s) => s.key !== "lost").map((s) => `<a class="wc-st" href="/clients"><b>${s.n}</b><span>${esc(s.label)}</span>
        <div class="wc-bar"><i style="--w:${Math.round(s.n / max * 100)}%;--c:${CL_C[s.key]}"></i></div></a>`).join("")}</div>
        ${nxt ? `<p class="wd-gt" style="margin-top:14px">Следующие шаги</p>${nxt}` : ""}`
        : `<p class="g-empty">Ведите покупателей и продавцов: этапы сделки, подобранные объекты, история и напоминания — в одном месте.</p>`}
      <div class="wd-acts"><a class="pill light" href="/clients?new=1">${ic("plus")}Клиент</a></div>`, 5);
  }

  // ─── объекты в работе (статусы) ──────────────────────────────────
  const ST_COLOR = { call: "#b8c8e4", show: "#e3cb8f", think: "#d9b3e6", deal: "#a7d1b6", refuse: "#e2a596" };
  function crm() {
    const max = Math.max(1, ...dash.crm.map((s) => s.n));
    const total = dash.crm.reduce((a, s) => a + s.n, 0);
    put("wCrm", `${head("briefcase", "Объекты в работе", `<a class="g-link" href="/work">Рабочий список →</a>`)}
      <div class="wc-pipe">${dash.crm.map((s) => `<a class="wc-st" href="/work?status=${s.key}"><b>${s.n}</b><span>${esc(s.label)}</span>
        <div class="wc-bar"><i style="--w:${Math.round(s.n / max * 100)}%;--c:${ST_COLOR[s.key]}"></i></div></a>`).join("")}</div>
      <div class="wc-foot"><span>В избранном: <b>${dash.favorites}</b></span><span>Со статусом: <b>${total}</b></span>
        <span>Подборок: <b>${dash.picks.n}</b>${dash.picks.views ? ` · просмотров клиентами: <b>${dash.picks.views}</b>` : ""}</span>
        ${dash.mine ? `<span>Моих объявлений: <b>${dash.mine}</b></span>` : ""}</div>`, 5);
  }

  // ─── уведомления ──────────────────────────────────────────────────
  function news() {
    $("dBellN").hidden = !dash.unread;
    $("dBellN").textContent = dash.unread > 99 ? "99+" : dash.unread;
    put("wNews", `${head("bell", "Уведомления", `<a class="g-link" href="/notifications">Все →</a>`)}
      ${dash.notices.length ? dash.notices.map((n) => `<a class="wn-it${n.read ? "" : " unread"}" href="${esc(n.url || "/notifications")}">
        <b>${esc(n.title)}</b><small>${ago(n.ts)}</small></a>`).join("")
        : `<p class="g-empty">Пока тихо. Нажмите «Следить» у поиска — сообщим о новых объектах и снижении цены.</p>`}`, 6);
  }

  // ─── рынок ────────────────────────────────────────────────────────
  function market() {
    const m = dash.market || {}, c = m.city || {};
    const ds = m.districts || [];
    const max = Math.max(1, ...ds.map((d) => d.median));
    put("wMarket", `${head("chart", "Рынок · квартиры", `<a class="g-link" href="/market">Аналитика →</a>`)}
      ${c.median ? `<div class="g-big" style="font-size:40px">${num(Math.round(c.median / 1000))}<small> тыс ₽/м²</small></div>
        <p class="g-sub">медиана по ${num(c.n)} объектам · средний чек ${money(c.price)}</p>` : `<p class="g-empty">Данных пока мало.</p>`}
      ${ds.map((d) => `<div class="wk-d"><span>${esc(d.name)}</span><b>${num(Math.round(d.median / 1000))} тыс</b>
        <div class="wc-bar"><i style="--w:${Math.round(d.median / max * 100)}%"></i></div></div>`).join("")}`, 7);
  }

  // ─── ипотечный калькулятор ───────────────────────────────────────
  const calc = { price: 6, down: 20, rate: 11, years: 20 };
  function calcHtml() {
    const presets = [["sec", "Вторичка"], ["new", "Новостройки"], ["family", "Семейная"]].filter(([k]) => mortRates[k] != null)
      .map(([k, t]) => `<button type="button" data-rate="${mortRates[k]}">${t} ${pct(mortRates[k])}</button>`).join("")
      + (mortRates.family == null ? `<button type="button" data-rate="6">Семейная 6%</button>` : "");
    put("wCalc", `${head("calc", "Ипотечный калькулятор")}
      <div class="wq-grid"><div class="wq-in">
        <div class="wq-f"><label>Цена объекта <b id="qPriceV"></b></label><input type="range" id="qPrice" min="1" max="40" step="0.1" value="${calc.price}"></div>
        <div class="wq-f"><label>Первый взнос <b id="qDownV"></b></label><input type="range" id="qDown" min="0" max="90" step="1" value="${calc.down}"></div>
        <div class="wq-f"><label>Ставка <b id="qRateV"></b></label><input type="range" id="qRate" min="1" max="30" step="0.1" value="${calc.rate}">
          <div class="wq-presets" id="qPresets">${presets}</div></div>
        <div class="wq-f"><label>Срок <b id="qYearsV"></b></label><input type="range" id="qYears" min="1" max="30" step="1" value="${calc.years}"></div>
      </div>
      <div class="wq-out"><span class="g-t">Платёж в месяц</span><div class="g-big" id="qPay"></div>
        <div class="wq-kv"><span>Сумма кредита</span><b id="qLoan"></b></div>
        <div class="wq-kv"><span>Переплата</span><b id="qOver"></b></div>
        <div class="wq-kv"><span>Нужен доход от</span><b id="qIncome"></b></div></div></div>`, 8);
    recalc();
  }
  function recalc() {
    const v = (id) => Number($(id).value);
    calc.price = v("qPrice"); calc.down = v("qDown"); calc.rate = v("qRate"); calc.years = v("qYears");
    const price = calc.price * 1e6, loan = price * (1 - calc.down / 100), n = calc.years * 12, r = calc.rate / 1200;
    const pay = r ? loan * r / (1 - Math.pow(1 + r, -n)) : loan / n;
    $("qPriceV").textContent = money(price);
    $("qDownV").textContent = `${calc.down}% · ${money(price * calc.down / 100)}`;
    $("qRateV").textContent = pct(calc.rate);
    $("qYearsV").textContent = `${calc.years} ${plural(calc.years, "год", "года", "лет")}`;
    $("qPay").innerHTML = `${num(Math.round(pay))}<small> ₽</small>`;
    $("qLoan").textContent = money(loan);
    $("qOver").textContent = money(Math.max(0, pay * n - loan));
    $("qIncome").textContent = `${num(Math.round(pay * 2 / 1000))} тыс ₽/мес`;
    document.querySelectorAll("#qPresets button").forEach((b) => b.classList.toggle("on", Number(b.dataset.rate) === calc.rate));
    document.querySelectorAll("#wCalc input[type=range]").forEach((r) => {   // золотая заливка до бегунка
      r.style.setProperty("--p", `${(r.value - r.min) / (r.max - r.min) * 100}%`);
    });
    try { localStorage.setItem("calc", JSON.stringify(calc)); } catch { /* */ }
  }

  // ─── доступ, действия, поиски, приглашение ──────────────────────
  function plan() {
    const left = me.access_until ? Math.max(0, Math.ceil((me.access_until - Date.now() / 1000) / 86400)) : 0;
    let body;
    if (me.is_admin) body = `<p class="g-sub">Администратор — доступ ко всему без ограничений.</p><a class="pill light" href="/pay">Посмотреть страницу оплаты</a>`;
    else if (me.access) body = `<div class="wp-price">${left}<small style="font-size:.45em;color:var(--ink-3)"> ${plural(left, "день", "дня", "дней")}</small></div>
      <div class="wp-meter"><i style="--w:${Math.min(100, left / (meta.period_days || 7) * 100)}%"></i></div>
      <p class="g-sub">Доступ до ${dayLong(me.access_until)}: номера агентов и все инструменты</p>
      <a class="pill light" href="/pay">${ic("card")}Продлить</a>`;
    else body = `<div class="wp-price">${esc(meta.price)} ₽</div><p class="g-sub">за ${esc(meta.period_days)} ${plural(Number(meta.period_days) || 0, "день", "дня", "дней")} — номера агентов, планер, подборки, уведомления</p>
      <a class="pill" href="/pay">${ic("card")}Оформить доступ</a>`;
    put("wPlan", `${head("shield", "Доступ")}${body}`, 9);
  }
  function quick() {
    const a = (href, icon, t) => `<a class="wa-a" href="${href}">${ic(icon)}<span>${t}</span></a>`;
    put("wQuick", `${head("sparkle", "Быстро")}<div class="wa-grid">
      ${a("/", "search", "Найти объект")}${a("/planner?add=show", "calendar", "Запланировать показ")}
      ${a("/picks", "folder", "Подборка клиенту")}${a("/?agent=1", "tag", "Моё объявление")}
      ${a("/clients?new=1", "users", "Новый клиент")}${a("/market", "chart", "Цены по районам")}</div>`, 10);
  }
  async function saved() {
    const r = await call("/api/saved");
    const items = r.ok ? r.data.items || [] : [];
    put("wSaved", `${head("search", "Слежу за поисками", items.length ? `<span class="g-sub" style="margin:0">${items.length}</span>` : "")}
      ${items.length ? items.slice(0, 6).map((x) => `<div class="ws-it">${ic("bell")}<a href="${esc(x.url)}">${esc(x.title)}</a>
        <button type="button" data-unsave="${x.id}" aria-label="Не следить">${ic("x")}</button></div>`).join("")
        : `<p class="g-empty">Настройте фильтры в поиске и нажмите «Следить» — новые объекты придут сюда и на почту.</p>`}`, 11);
  }
  function referral() {
    const r = me.ref;
    if (!r) { $("wRef").hidden = true; return; }
    const link = `${location.origin}/?ref=${r.code}`;
    const text = `Пользуюсь 1+1 — все объекты Краснодара из риелторских чатов в одном поиске: ${link}`;
    put("wRef", `${head("gift", "Приведи коллегу")}
      <p class="g-sub" style="margin:0">Коллега подтвердит номер агента — вам <b>+${r.days} дней</b> доступа.${r.invited ? ` Пришли: ${r.invited}, подтвердили: ${r.rewarded}.` : ""}</p>
      <div class="wr-link"><input readonly value="${esc(link)}" aria-label="Ваша ссылка"><button type="button" class="pill light" id="refCopy">${ic("link")}</button>
        <button type="button" class="pill light" data-share-text="${esc(text)}" aria-label="Поделиться">${ic("share")}</button></div>`, 12);
  }
  async function admin() {
    if (!me.is_admin) return;
    const w = $("wAdmin");
    w.hidden = false;
    const r = await call("/api/admin/overview");
    if (!r.ok) return;
    const o = r.data;
    const s = (n, l, warn) => `<div class="${warn ? "warn" : ""}"><b>${num(n)}</b><span>${l}</span></div>`;
    put("wAdmin", `${head("sliders", "Сервис")}
      <div class="wad-grid">${[s(o.listings, "объектов на сайте"), s(dash.new_today, "новых сегодня"), s(o.users, "пользователей"),
        s(o.users_access, "с доступом"), s(o.queue, "ждут разбора"), s(o.complaints_new, "новых жалоб", o.complaints_new > 0)].join("")}</div>
      <div class="wad-acts"><a class="pill light" href="/learn">${ic("layers")}Обучение и разбор</a><a class="pill light" href="/admin">${ic("sliders")}Админка</a>
        <a class="pill light" href="/admin/map">${ic("map")}Карта районов</a></div>`, 13);
  }

  // ─── ставки банков (админ) ────────────────────────────────────────
  function bankRow(x = {}) {
    const v = (k) => (x[k] != null ? esc(String(x[k]).replace(".", ",")) : "");
    return `<div class="bank-row"><input data-k="bank" placeholder="Банк" value="${esc(x.bank || "")}">
      ${Object.keys(PROG).map((k) => `<input data-k="${k}" placeholder="—" inputmode="decimal" value="${v(k)}" aria-label="${PROG[k]}">`).join("")}
      <button type="button" data-bank-del aria-label="Убрать">✕</button></div>`;
  }
  function openBanks() {
    const items = (dash.widgets.banks && dash.widgets.banks.items) || [];
    $("banksRows").innerHTML = `<div class="bank-row head"><span>Банк</span>${Object.values(PROG).map((t) => `<span>${t}, %</span>`).join("")}<span></span></div>`
      + (items.length ? items : [{}, {}, {}, {}]).map(bankRow).join("");
    $("banksDlg").showModal();
  }

  async function load() {
    const [m, mt] = await Promise.all([call("/api/me"), call("/api/meta")]);
    me = m.data.me;
    meta = mt.data || {};
    if (!me) { $("dGuest").hidden = false; return; }
    $("dApp").hidden = false;
    header();
    try { Object.assign(calc, JSON.parse(localStorage.getItem("calc") || "{}")); } catch { /* */ }
    quick(); plan(); referral(); saved();
    const r = await call("/api/dashboard");
    if (!r.ok) { toast("Не получилось загрузить кабинет — обновите страницу"); return; }
    dash = r.data;
    const w = dash.widgets;
    weather(w.weather); rate(w.keyrate); mortgage(w.mortgage, w.banks); fx(w.currency);
    day(); clientsW(); crm(); news(); market(); calcHtml(); admin();
  }

  // ─── события ─────────────────────────────────────────────────────
  document.addEventListener("input", (e) => { if (e.target.closest("#wCalc input[type=range]")) recalc(); });
  document.addEventListener("click", async (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.rate) { $("qRate").value = b.dataset.rate; recalc(); }
    else if (b.dataset.unsave) { b.closest(".ws-it").style.opacity = ".3"; await call("/api/saved", { id: Number(b.dataset.unsave) }, "DELETE"); saved(); }
    else if (b.id === "dLogout") { await call("/api/auth/logout", {}); location.href = "/"; }
    else if (b.id === "refCopy") {
      const v = b.parentElement.querySelector("input").value;
      try { await navigator.clipboard.writeText(v); toast("Ссылка скопирована"); } catch { prompt("Скопируйте ссылку:", v); }
    } else if (b.id === "banksEdit") openBanks();
    else if (b.id === "banksAdd") $("banksRows").insertAdjacentHTML("beforeend", bankRow());
    else if (b.hasAttribute("data-bank-del")) b.closest(".bank-row").remove();
    else if (b.id === "banksSave") {
      const items = [...document.querySelectorAll(".bank-row:not(.head)")].map((row) => {
        const o = {};
        row.querySelectorAll("input").forEach((i) => { o[i.dataset.k] = i.value.trim(); });
        return o;
      }).filter((x) => x.bank);
      const r = await call("/api/admin/banks", { items });
      if (!r.ok) { toast(r.data.detail || "Не получилось сохранить"); return; }
      dash.widgets.banks = r.data;
      mortgage(dash.widgets.mortgage, r.data);
      $("banksDlg").close();
      toast("Ставки банков сохранены");
    } else if (b.hasAttribute("data-close")) b.closest("dialog").close();
  });
  $("dPromo").addEventListener("submit", async (e) => {
    e.preventDefault();
    const code = $("dPromoInput").value.trim();
    if (!code) return;
    const r = await call("/api/promo", { code });
    $("dPromoMsg").hidden = false;
    $("dPromoMsg").textContent = r.data.text || r.data.detail || "Не получилось";
    if (r.ok && r.data.me) { me = r.data.me; header(); plan(); }
  });
  // иконки рельса
  document.querySelectorAll("[data-ic]").forEach((el) => { el.outerHTML = ic(el.dataset.ic); });
  load();
})();
