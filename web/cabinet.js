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
  const put = (id, html, i) => { const el = $(id); if (!el) return; el.innerHTML = html; if (i != null) el.style.setProperty("--i", i); };   // блока нет на странице — пропускаем

  // ─── шапка ─────────────────────────────────────────────────────────
  function header() {
    const now = new Date();
    const h = now.getHours();
    const hi = h < 5 ? "Доброй ночи" : h < 12 ? "Доброе утро" : h < 18 ? "Добрый день" : "Добрый вечер";
    const name = (me.name || "").trim().split(/\s+/)[0] || (me.email || "").split("@")[0] || "коллега";
    $("dHello").textContent = `${hi}, ${name}`;
    $("dDate").textContent = now.toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" });
    const letter = (name[0] || "1").toUpperCase();
    for (const id of ["dAva", "dAva2"]) {   // фото из настроек — фото аккаунта; нет фото — первая буква имени
      const el = $(id);
      el.textContent = me.photo ? "" : letter;
      el.style.backgroundImage = me.photo ? `url("${me.photo}")` : "";
      el.classList.toggle("has-photo", !!me.photo);
    }
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
  // Ипотека — только вторичка (решение Артёма 13.10: ставки новостроек не ведём)
  const PROG = { secondary: "Вторичка" };
  function mortgage(m, banks) {
    const all = (banks && banks.items) || [];
    const b = all.filter((x) => x.secondary != null).sort((x, y) => x.secondary - y.secondary);
    const d = m ? m.data : null;
    const edit = me.is_admin ? `<button type="button" class="wm-edit" id="banksEdit">${b.length ? "Изменить ставки банков" : "+ Внести ставки банков"}</button>` : "";
    const ma = banks && banks.avg;
    mortRates = { best: b.length ? b[0].secondary : null, avg: ma && ma.secondary != null ? ma.secondary : null, cb: d && d.secondary != null ? d.secondary : null,
      banks: b.map((x) => ({ bank: x.bank, rate: x.secondary })) };
    // средняя по рынку: из таблицы ДОМ.РФ, которую вставил админ; если её нет — средняя ЦБ по краю
    const avg = mortRates.avg != null
      ? `<p class="g-sub wm-avg">Средневзвешенная по рынку на ${new Date(ma.ts * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "long" })}: ${pct(ma.secondary)}</p>`
      : "";   // 17.10 (Артём): строку «В среднем по краю … (ЦБ)» не показываем вообще
    if (!b.length) {   // ставок банков ещё нет — средняя ЦБ по краю
      put("wMort", `${head("percent", "Ипотека · вторичка")}
        <p class="g-empty">Ставки банков на вторичку появятся, когда их внесут.</p>${edit}`, 2);
      return;
    }
    const upd = banks.ts ? new Date(banks.ts * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "short" }) : "";
    const age = banks.ts ? Math.floor((Date.now() / 1000 - banks.ts) / 86400) : 0;
    const lo = b[0].secondary, hi = b[b.length - 1].secondary;
    put("wMort", `${head("percent", "Ипотека · вторичка", upd ? `<span class="g-sub wm-upd${age >= 7 ? " old" : ""}" style="margin:0"
        title="${age >= 7 ? `Ставкам ${age} дн. — пора обновить` : "Обновлено"}">на ${upd}</span>` : "")}
      <div class="wm-best"><span>Лучшая — ${esc(b[0].bank)}</span><b>${pct(lo)}</b></div>
      <div class="wm-list">${b.map((x, i) => `<div class="wm-bank${i ? "" : " lead"}"><span title="${esc(x.bank)}">${esc(x.bank)}</span>
        <i style="--w:${hi > lo ? Math.round(15 + (x.secondary - lo) / (hi - lo) * 85) : 15}%"></i><b>${pct(x.secondary)}</b></div>`).join("")}</div>
      ${avg}${edit}`, 2);
  }

  // ─── курсы ЦБ и биржа: вкладки, чтобы блок не рос в высоту ─────────
  // мини-график за ~30 торговых дней (данные Мосбиржи с задержкой)
  function spark(hist, up) {
    const v = (hist || []).map((x) => x[1]).filter((x) => x != null);
    if (v.length < 2) return "";
    const W = 100, H = 30, lo = Math.min(...v), hi = Math.max(...v), k = hi - lo || 1;
    const pts = v.map((y, i) => `${(i / (v.length - 1) * W).toFixed(1)},${(H - 2 - (y - lo) / k * (H - 4)).toFixed(1)}`);
    return `<svg class="mspark ${up ? "up" : "down"}" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">
      <path class="a" d="M0,${H} L${pts.join(" L")} L${W},${H} Z"/><path class="l" d="M${pts.join(" L")}"/></svg>`;
  }
  const chg = (c) => (c == null ? "<span></span>" : `<span class="chg ${c >= 0 ? "up" : "down"}">${ic(c >= 0 ? "arrow-up" : "arrow-down")}${num(Math.abs(c), 2)}%</span>`);
  let fxTab = (() => { try { return localStorage.getItem("fxTab") || "fx"; } catch { return "fx"; } })();
  let fxData = null, moexData = null;
  function fx(c, m) {
    fxData = c; moexData = m ? m.data : null;
    const sym = { USD: "$", EUR: "€", CNY: "¥" };
    const tabs = [["fx", "Валюта"], ...(moexData && moexData.indices.length ? [["idx", "Индексы"]] : []),
      ...(moexData && moexData.developers.length ? [["dev", "Застройщики"]] : [])];
    if (!tabs.some(([k]) => k === fxTab)) fxTab = "fx";
    let body;
    if (fxTab === "fx") body = c ? c.data.items.map((x) => `<div class="wf-row"><span>${sym[x.code] || ""} ${x.code}</span><b>${num(x.value, 2)} ₽</b>
        ${x.change != null ? `<span class="chg ${x.change > 0 ? "up" : "down"}">${ic(x.change > 0 ? "arrow-up" : "arrow-down")}${num(Math.abs(x.change), 2)}</span>` : "<span></span>"}</div>`).join("")
        + `<p class="wf-src">ЦБ РФ на ${esc(c.data.date)}</p>` : `<p class="g-empty">Загрузятся через минуту.</p>`;
    else if (fxTab === "idx") body = moexData.indices.map((x) => `<div class="wf-q"><span class="wf-n"><b>${esc(x.name)}</b>${chg(x.change)}</span>
        <span class="wf-v">${num(x.value, 2)}</span>${spark(x.hist, (x.change || 0) >= 0)}</div>`).join("")
        + `<p class="wf-src">${esc(moexData.source)} · график за месяц</p>`;
    else body = moexData.developers.map((t) => { const x = moexData.shares[t];
        return `<div class="wf-q sm"><span class="wf-n"><b>${esc(x.name.replace(/ ао$/, ""))}</b>${chg(x.change)}</span>
          <span class="wf-v">${num(x.price, 2)} ₽</span>${spark(x.hist, (x.change || 0) >= 0)}</div>`; }).join("")
        + `<p class="wf-src">${esc(moexData.source)} · свою бумагу можно добавить блоком «Акция»</p>`;
    put("wFx", `${head("coins", "Курсы и биржа")}
      ${tabs.length > 1 ? `<div class="wf-tabs">${tabs.map(([k, t]) => `<button type="button" data-fx-tab="${k}" class="${k === fxTab ? "on" : ""}">${t}</button>`).join("")}</div>` : ""}
      <div class="wf-body">${body}</div>`, 3);
    const box = $("wFx");
    box.querySelectorAll("[data-fx-tab]").forEach((btn) => btn.addEventListener("click", () => {
      fxTab = btn.dataset.fxTab;
      try { localStorage.setItem("fxTab", fxTab); } catch { /* */ }
      fx(fxData, moexData ? { data: moexData } : null);
    }));
    document.querySelectorAll("[data-ub-stock]").forEach(fillStock);
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
      ${c.total ? `<div class="wc-pipe wc-cli">${c.stages.filter((s) => s.key !== "lost").map((s) => `<a class="wc-st" href="/clients"><b>${s.n}</b><span>${esc(s.label)}</span>
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
    put("wCrm", `${head("briefcase", "Избранное", `<a class="g-link" href="/work">Всё избранное →</a>`)}
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

  // ─── новости недвижимости: заголовок + первая фраза + ссылка на РБК; список листается внутри блока ─
  function rbc(n) {
    if (!n) { put("wRbc", `${head("news", "Новости · Краснодарский край")}<p class="g-empty">Загрузятся через несколько минут.</p>`, 8); return; }
    if (!n.data.items.length) { put("wRbc", `${head("news", "Новости · Краснодарский край")}<p class="g-empty">Свежих новостей недвижимости про Краснодар и край пока нет — покажем, как только появятся.</p>`, 8); return; }
    const d = n.data;
    put("wRbc", `${head("news", "Новости · Краснодарский край", `<a class="g-link" href="${esc(d.source_url)}" target="_blank" rel="noopener">${esc(d.source)} ${ic("external")}</a>`)}
      <div class="wr-list">${d.items.map((x) => `<a class="wr-it" href="${esc(x.url)}" target="_blank" rel="noopener">
        <span class="wr-meta">${x.tag ? `<i>${esc(x.tag)}</i>` : ""}<small>${ago(x.ts)}</small></span>
        <b>${esc(x.title)}</b>${x.lead ? `<p>${esc(x.lead)}</p>` : ""}</a>`).join("")}</div>
      <div class="wr-foot"><small>Источник: ${esc(d.source)}. Читать полностью — на сайте РБК.</small></div>`, 8);
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
  let rateSet = false;
  function calcHtml() {
    // по умолчанию — лучшая рыночная ставка банков на вторичку (средняя ЦБ включает льготные программы и занижает платёж)
    if (!rateSet && mortRates.best != null) { calc.rate = mortRates.best; rateSet = true; }
    // 17.10 (Артём): ставка — из списка банков (раскрывающийся список), без «средней ЦБ»
    const banks = mortRates.banks || [];
    const opts = banks.map((x, i) => `<option value="b${i}" data-rate="${x.rate}">${esc(x.bank)} — ${pct(x.rate)}</option>`).join("")
      + (mortRates.avg != null ? `<option value="avg" data-rate="${mortRates.avg}">Средняя по рынку — ${pct(mortRates.avg)}</option>` : "")
      + `<option value="own">Своя ставка — ползунком</option>`;
    const presets = banks.length || mortRates.avg != null
      ? `<select id="qBank" aria-label="Ставка банка" data-placeholder="Выберите банк">${opts}</select>` : "";
    put("wCalc", `${head("calc", "Ипотечный калькулятор")}
      <div class="wq-grid"><div class="wq-in">
        <div class="wq-f"><label>Цена объекта <b id="qPriceV"></b></label><input type="range" id="qPrice" min="1" max="40" step="0.1" value="${calc.price}"></div>
        <div class="wq-f"><label>Первый взнос <b id="qDownV"></b></label><input type="range" id="qDown" min="0" max="90" step="1" value="${calc.down}"></div>
        <div class="wq-f"><label>Ставка <b id="qRateV"></b></label><input type="range" id="qRate" min="1" max="30" step="0.01" value="${calc.rate}">
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
    const qb = $("qBank");
    if (qb) {   // выбранный банк — если ставка совпадает, иначе «своя»
      const hit = [...qb.options].find((o) => o.dataset.rate && Math.abs(Number(o.dataset.rate) - calc.rate) < 0.006);
      const val = hit ? hit.value : "own";
      if (qb.value !== val) qb.value = val;
    }
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
    else body = `<div class="wp-price"><small style="font-size:.45em;color:var(--ink-3)">от </small>${esc(meta.price_first || meta.price)} ₽</div>
      <p class="g-sub">${esc(meta.prices || "")} — номера агентов, планер, подборки, уведомления</p>
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
    const bk = dash.widgets.banks || {};
    renderBankRows(bk.items || []);
    const days = bk.ts ? Math.floor((Date.now() / 1000 - bk.ts) / 86400) : null;
    $("banksAge").textContent = days == null ? "Ставок ещё нет." : `Сейчас в таблице ставки от ${new Date(bk.ts * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "long" })}${days >= 7 ? ` — им уже ${days} дн., пора обновить` : ""}.`;
    $("banksParsed").hidden = true;
    $("banksText").value = "";
    $("banksDlg").showModal();
  }
  function renderBankRows(items) {
    $("banksRows").innerHTML = `<div class="bank-row head"><span>Банк</span>${Object.values(PROG).map((t) => `<span>${t}, %</span>`).join("")}<span></span></div>`
      + (items.length ? items : [{}, {}, {}, {}]).map(bankRow).join("");
  }
  const bankRowsNow = () => [...document.querySelectorAll(".bank-row:not(.head)")].map((row) => {
    const o = {};
    row.querySelectorAll("input").forEach((i) => { o[i.dataset.k] = i.value.trim(); });
    return o;
  }).filter((x) => x.bank);
  let parsedAvg = null;   // «Средневзвешенная ставка» из вставленной таблицы
  async function parseBanks() {
    const text = $("banksText").value.trim();
    if (!text) { toast("Сначала вставьте текст обзора"); return; }
    const btn = $("banksParse");
    btn.disabled = true; btn.textContent = "Разбираем…";
    const r = await call("/api/admin/banks", { text, program: $("banksProg").value });
    btn.disabled = false; btn.textContent = "Разобрать";
    const out = $("banksParsed");
    out.hidden = false;
    if (!r.ok || !r.data.items.length) { out.textContent = "Не нашли ставок банков в этом тексте — попробуйте скопировать таблицу целиком."; return; }
    // новые поверх прежних: найденные банки и программы заменяются, остальное остаётся
    const by = new Map(bankRowsNow().map((x) => [x.bank.toLowerCase(), x]));
    for (const x of r.data.items) {
      const cur = by.get(x.bank.toLowerCase()) || { bank: x.bank };
      for (const k of Object.keys(PROG)) if (x[k] != null) cur[k] = x[k];
      by.set(x.bank.toLowerCase(), cur);
    }
    const fresh = new Set(r.data.items.map((x) => x.bank.toLowerCase()));
    renderBankRows([...by.values()]);
    document.querySelectorAll(".bank-row:not(.head)").forEach((row) => {
      if (fresh.has(row.querySelector('[data-k="bank"]').value.trim().toLowerCase())) row.classList.add("fresh");
    });
    parsedAvg = r.data.avg || null;
    const a = parsedAvg && parsedAvg.secondary != null ? ` Средневзвешенная по рынку (вторичка): ${parsedAvg.secondary}%.` : "";
    out.textContent = `Нашли банков: ${r.data.items.length}${String(r.data.how).includes("ai") ? " (помогла нейросеть)" : ""} — они подсвечены.${a} Проверьте и нажмите «Сохранить».`;
  }

  async function load() {
    const [m, mt] = await Promise.all([call("/api/me"), call("/api/meta")]);
    me = m.data.me;
    meta = mt.data || {};
    if (!me) { $("dGuest").hidden = false; return; }
    $("dApp").hidden = false;
    header();
    attention();
    try { Object.assign(calc, JSON.parse(localStorage.getItem("calc") || "{}")); } catch { /* */ }
    quick(); plan(); referral(); saved();
    const r = await call("/api/dashboard");
    if (!r.ok) { toast("Не получилось загрузить кабинет — обновите страницу"); return; }
    dash = r.data;
    const w = dash.widgets;
    weather(w.weather); rate(w.keyrate); mortgage(w.mortgage, w.banks); fx(w.currency, w.moex); rbc(w.news);
    day(); clientsW(); crm(); news(); market(); calcHtml(); admin();
    layout();
    if (me.is_admin && new URLSearchParams(location.search).has("banks")) { openBanks(); history.replaceState(null, "", "/cabinet"); }
  }

  // ─── что ждёт внимания (те же счётчики, что в меню слева) ─────────
  function attention() {
    const c = me.counts || {};
    const chips = [
      [c.plan_overdue, "alert", `просрочено: ${c.plan_overdue}`, "/planner", "hot"],
      [c.plan_today, "calendar", `дел сегодня: ${c.plan_today}`, "/planner", ""],
      [c.work, "heart", `в избранном: ${c.work}`, "/work", ""],
      [me.unread, "bell", `новых уведомлений: ${me.unread}`, "/notifications", ""],
      // акт 14.10: путь к отметке «Проверенный агент» — подтвердить свой номер
      [!me.phone_confirmed, "check", "Подтвердите свой номер — отметка «Проверенный агент» у ваших объявлений", "/my", ""],
    ].filter(([n]) => n);
    $("dToday").innerHTML = chips.length
      ? chips.map(([, icon, text, href, hot]) => `<a class="d-att${hot ? " hot" : ""}" href="${href}">${ic(icon)}${text}</a>`).join("")
      : `<span class="d-att calm">${ic("check")}Всё под контролем — срочных дел нет</span>`;
  }


  // ─── свои блоки кабинета: заметка, дела, ссылки, мой поиск, цель, обратный отсчёт (только данные — без чужого кода) ───
  const BTYPES = {
    note: { t: "Заметка", ic: "note", d: "Короткий текст под рукой: скрипт звонка, реквизиты, важное" },
    todo: { t: "Список дел", ic: "check", d: "Галочки: что сделать сегодня или по сделке" },
    links: { t: "Ссылки", ic: "link", d: "Свои полезные сайты: Росреестр, банки, CRM агентства" },
    search: { t: "Мой поиск", ic: "search", d: "Сохранённый поиск: сколько объектов и сколько новых за сутки" },
    goal: { t: "Цель", ic: "trend", d: "Например, «Сделок в месяц: 2 из 5» — с кнопками +1 / −1" },
    countdown: { t: "Обратный отсчёт", ic: "hourglass", d: "Сколько дней до сделки, отпуска или конца месяца" },
    stock: { t: "Акция", ic: "trend", d: "Цена бумаги Мосбиржи с графиком за месяц: SBER, SMLT, PIKK, фонд TMOS…" },
  };
  const BCOLORS = ["gold", "teal", "sky", "violet", "coral", "rose", "lime", "slate"];
  let blocks = [], savedList = null;
  const newId = () => "u" + Math.random().toString(36).slice(2, 9);
  async function saveBlocks() {
    const r = await call("/api/settings", { blocks });
    if (r.ok) blocks = r.data.settings.blocks || [];
    return r.ok;
  }
  function blockHTML(b) {
    const T = BTYPES[b.type], d = b.data || {};
    const tools = `<span class="ub-tools"><button type="button" class="ub-btn" data-ub-edit="${b.id}" aria-label="Изменить">${ic("edit")}</button></span>`;
    let body = "";
    if (b.type === "note") body = d.text ? `<p class="ub-note">${esc(d.text)}</p>` : `<p class="g-empty">Пусто — нажмите ✎, чтобы написать.</p>`;
    if (b.type === "todo") {
      const items = d.items || [];
      const done = items.filter((x) => x.done).length;
      body = `${items.length ? `<div class="ub-bar"><i style="--w:${Math.round(done / items.length * 100)}%"></i></div>` : ""}
        <ul class="ub-todo">${items.map((x, i) => `<li class="${x.done ? "done" : ""}"><button type="button" data-ub-check="${b.id}:${i}" aria-label="Отметить">${ic("check")}</button><span>${esc(x.t)}</span></li>`).join("")}</ul>
        <form class="ub-add" data-ub-add="${b.id}"><input maxlength="200" placeholder="Новое дело…" aria-label="Новое дело"><button type="submit" class="ub-btn" aria-label="Добавить">${ic("plus")}</button></form>`;
    }
    if (b.type === "links") body = (d.items || []).length ? `<div class="ub-links">${d.items.map((x) => {
        let host = ""; try { host = new URL(x.u).hostname.replace(/^www\./, ""); } catch { /* */ }
        return `<a href="${esc(x.u)}" target="_blank" rel="noopener noreferrer"><b>${esc(x.t)}</b><small>${esc(host)}</small>${ic("external")}</a>`; }).join("")}</div>`
      : `<p class="g-empty">Добавьте ссылки — нажмите ✎.</p>`;
    if (b.type === "search") body = `<div class="ub-search" data-ub-search="${b.id}"><p class="g-empty">Считаем…</p></div>`;
    if (b.type === "goal") {
      const pct = Math.min(100, Math.round((d.n || 0) / (d.of || 1) * 100));
      body = `<div class="ub-goal"><svg viewBox="0 0 120 120" class="ub-ring"><circle cx="60" cy="60" r="50"/><circle cx="60" cy="60" r="50" class="v"
          style="stroke-dasharray:${(pct / 100 * 314).toFixed(1)} 314"/></svg><div class="ub-goal-n"><b>${d.n || 0}</b><small>из ${d.of || 5}</small></div></div>
        <div class="ub-goal-acts"><button type="button" class="pill light" data-ub-goal="${b.id}:-1">−1</button><button type="button" class="pill" data-ub-goal="${b.id}:1">+1</button></div>`;
    }
    if (b.type === "stock") body = `<div class="ub-stock" data-ub-stock="${esc(d.ticker || "")}"></div>`;
    if (b.type === "countdown") {
      if (!d.date) body = `<p class="g-empty">Укажите дату — нажмите ✎.</p>`;
      else {
        const days = Math.ceil((new Date(`${d.date}T00:00`) - new Date(new Date().toDateString())) / 86400000);
        body = `<div class="ub-cd"><b>${Math.abs(days)}</b><span>${days > 0 ? plural(days, "день", "дня", "дней") + " осталось" : days === 0 ? "сегодня!" : plural(-days, "день", "дня", "дней") + " назад"}</span>
          <small>${new Date(`${d.date}T12:00`).toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" })}</small></div>`;
      }
    }
    return `${head(T.ic, esc(b.title || T.t), tools)}${body}`;
  }
  function renderBlocks() {
    const box = $("bento");
    box.querySelectorAll(":scope > .g.ub").forEach((el) => { if (!blocks.some((b) => b.id === el.id)) el.remove(); });
    for (const b of blocks) {
      let el = document.getElementById(b.id);
      if (!el) {
        el = document.createElement("section");
        el.id = b.id;
        el.className = `g ub c-${b.color}`;
        box.insertBefore(el, $("wAdd"));
      }
      el.className = `g ub ubt-${b.type} c-${b.color}`;
      el.innerHTML = blockHTML(b);
      if (!el.querySelector(":scope > .g-grip")) addGrip(el);
    }
    fillSearchBlocks();
    document.querySelectorAll("[data-ub-stock]").forEach(fillStock);
  }
  function fillStock(el) {
    const t = el.dataset.ubStock;
    if (!t) { el.innerHTML = `<p class="g-empty">Укажите код бумаги — нажмите ✎.</p>`; return; }
    const x = moexData && moexData.shares[t];
    if (!x) {
      const asked = moexData && moexData.asked.includes(t);
      el.innerHTML = `<p class="g-empty">${asked ? `Бумагу ${esc(t)} не нашли на Мосбирже — проверьте код.` : `Загружаем ${esc(t)} — обычно пара минут.`}</p>`;
      return;
    }
    el.innerHTML = `<div class="ub-st-h"><span><b>${esc(x.name.replace(/ ао$/, ""))}</b><small>${esc(t)}</small></span>${chg(x.change)}</div>
      <div class="ub-st-v">${num(x.price, 2)} ₽</div>${spark(x.hist, (x.change || 0) >= 0)}
      <p class="wf-src">Мосбиржа, с задержкой · за месяц</p>`;
  }
  async function fillSearchBlocks() {
    const els = document.querySelectorAll("[data-ub-search]");
    if (!els.length) return;
    if (!savedList) { const r = await call("/api/saved"); savedList = (r.ok && r.data.items) || []; }
    for (const el of els) {
      const b = blocks.find((x) => x.id === el.dataset.ubSearch);
      const s = savedList.find((x) => x.id === (b && b.data.saved_id));
      if (!s) { el.innerHTML = `<p class="g-empty">Выберите сохранённый поиск — нажмите ✎. Сохранить поиск можно кнопкой «Следить» на главной.</p>`; continue; }
      let params;
      try { params = typeof s.params === "string" && s.params.trim().startsWith("{") ? new URLSearchParams(JSON.parse(s.params)) : new URLSearchParams(s.params || ""); }
      catch { params = new URLSearchParams(); }
      params.set("size", "1");
      const [all, fresh] = await Promise.all([call(`/api/listings?${params}`), call(`/api/listings?${params}&new_days=1`)]);
      el.innerHTML = `<a class="ub-sres" href="${esc(s.url || "/")}"><span><b>${num(all.ok ? all.data.total : 0)}</b><small>объектов</small></span>
        <span class="new"><b>+${num(fresh.ok ? fresh.data.total : 0)}</b><small>новых за сутки</small></span></a><p class="ub-stitle">${esc(s.title || "")}</p>`;
    }
  }
  function addGrip(g) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "g-grip"; b.title = "Перетащите, чтобы поменять порядок"; b.setAttribute("aria-label", "Переставить блок");
    b.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="9" cy="6" r="1.4"/><circle cx="15" cy="6" r="1.4"/><circle cx="9" cy="12" r="1.4"/><circle cx="15" cy="12" r="1.4"/><circle cx="9" cy="18" r="1.4"/><circle cx="15" cy="18" r="1.4"/></svg>';
    g.appendChild(b);
  }
  // окно: выбрать вид нового блока / изменить блок
  function blockDlg() {
    let d = document.getElementById("ubDlg");
    if (!d) { d = document.createElement("dialog"); d.id = "ubDlg"; d.className = "small-dlg ub-dlg"; document.body.appendChild(d); }
    return d;
  }
  function openAddBlock() {
    const d = blockDlg();
    d.innerHTML = `<div class="dlg-body"><div class="d-dlg-head"><h2>Свой блок</h2><button type="button" class="close" data-close aria-label="Закрыть">✕</button></div>
      <p class="note">Выберите, что добавить в кабинет. Блок можно переставить за ⠿ и изменить кнопкой ✎.</p>
      <div class="ub-types">${Object.entries(BTYPES).map(([k, v]) => `<button type="button" class="ub-type" data-ub-new="${k}">${ic(v.ic)}<b>${v.t}</b><small>${v.d}</small></button>`).join("")}</div></div>`;
    d.showModal();
  }
  async function openEditBlock(b, isNew) {
    const d = blockDlg(), T = BTYPES[b.type], x = b.data || {};
    if (b.type === "search" && !savedList) { const r = await call("/api/saved"); savedList = (r.ok && r.data.items) || []; }
    let fields = "";
    if (b.type === "note") fields = `<label class="ub-f"><span>Текст</span><textarea name="text" rows="6" maxlength="2000">${esc(x.text || "")}</textarea></label>`;
    if (b.type === "todo") fields = `<label class="ub-f"><span>Дела — по одному в строке</span><textarea name="items" rows="7" maxlength="6000">${esc((x.items || []).map((i) => (i.done ? "[x] " : "") + i.t).join("\n"))}</textarea></label>`;
    if (b.type === "links") fields = `<label class="ub-f"><span>Ссылки — по одной в строке: «Название — https://адрес»</span><textarea name="items" rows="6" maxlength="4000"
      placeholder="Росреестр — https://rosreestr.gov.ru">${esc((x.items || []).map((i) => `${i.t} — ${i.u}`).join("\n"))}</textarea></label>`;
    if (b.type === "search") fields = savedList.length ? `<label class="ub-f"><span>Сохранённый поиск</span><select name="saved_id">${savedList.map((s) =>
        `<option value="${s.id}"${s.id === x.saved_id ? " selected" : ""}>${esc(s.title || "Поиск")}</option>`).join("")}</select></label>`
      : `<p class="note">Сохранённых поисков пока нет. На главной настройте фильтры и нажмите «Следить» — поиск появится здесь.</p>`;
    if (b.type === "goal") fields = `<div class="ub-two"><label class="ub-f"><span>Сейчас</span><input name="n" type="number" min="0" value="${x.n || 0}"></label>
      <label class="ub-f"><span>Цель</span><input name="of" type="number" min="1" value="${x.of || 5}"></label></div>`;
    if (b.type === "stock") fields = `<label class="ub-f"><span>Код бумаги на Мосбирже</span><input name="ticker" maxlength="12" value="${esc(x.ticker || "")}"
      placeholder="Например, SBER" autocapitalize="characters" style="text-transform:uppercase"></label>
      <p class="note">Застройщики: SMLT (Самолёт), PIKK (ПИК), LSRG (ЛСР), ETLN (Эталон). Банки: SBER, VTBR. Фонды: TMOS, SBMX.</p>`;
    if (b.type === "countdown") fields = `<label class="ub-f"><span>Дата</span><input name="date" type="date" value="${esc(x.date || "")}"></label>`;
    d.innerHTML = `<div class="dlg-body"><div class="d-dlg-head"><h2>${isNew ? "Новый блок" : "Изменить блок"} · ${T.t}</h2><button type="button" class="close" data-close aria-label="Закрыть">✕</button></div>
      <form class="ub-form" id="ubForm">
        <label class="ub-f"><span>Название</span><input name="title" maxlength="60" value="${esc(b.title || "")}" placeholder="${T.t}"></label>
        ${fields}
        <div class="ub-f"><span>Цвет</span><div class="ub-colors">${BCOLORS.map((c) => `<button type="button" class="ub-color c-${c}${c === (b.color || "gold") ? " on" : ""}" data-ub-color="${c}" aria-label="${c}"></button>`).join("")}</div></div>
        <div class="d-dlg-acts">${isNew ? "" : `<button type="button" class="chip danger" data-ub-del="${b.id}">Удалить блок</button>`}
          <button type="submit" class="pill">${isNew ? "Добавить в кабинет" : "Сохранить"}</button></div>
      </form></div>`;
    d.dataset.color = b.color || "gold";
    if (!d.open) d.showModal();
    if (window.Pick) Pick.auto(d);
    $("ubForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = e.target, data = {};
      if (b.type === "note") data.text = f.text.value;
      if (b.type === "todo") data.items = f.items.value.split("\n").map((s) => s.trim()).filter(Boolean).map((s) => ({ t: s.replace(/^\[x\]\s*/i, ""), done: /^\[x\]/i.test(s) }));
      if (b.type === "links") data.items = f.items.value.split("\n").map((s) => s.trim()).filter(Boolean).map((s) => {
        const m = s.match(/^(.*?)\s*[—–-]\s*(https?:\/\/\S+)$/) || s.match(/^()(https?:\/\/\S+)$/);
        return m ? { t: m[1] || m[2], u: m[2] } : null;
      }).filter(Boolean);
      if (b.type === "search") data.saved_id = f.saved_id ? Number(f.saved_id.value) : null;
      if (b.type === "goal") { data.n = Number(f.n.value); data.of = Number(f.of.value); }
      if (b.type === "countdown") data.date = f.date.value;
      if (b.type === "stock") data.ticker = f.ticker.value.trim().toUpperCase();
      const nb = { ...b, title: f.title.value.trim(), color: d.dataset.color, data };
      blocks = isNew ? [...blocks, nb] : blocks.map((x) => (x.id === b.id ? nb : x));
      if (!(await saveBlocks())) { toast("Не сохранилось — попробуйте ещё раз"); return; }
      d.close(); renderBlocks();
      toast(isNew ? "Блок добавлен в кабинет" : "Сохранено");
      if (isNew) document.getElementById(nb.id)?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  }
  document.addEventListener("click", async (e) => {
    const t = e.target.closest("button");
    if (!t) return;
    if (t.id === "ubAdd") { openAddBlock(); return; }
    if (t.dataset.ubNew) { openEditBlock({ id: newId(), type: t.dataset.ubNew, title: "", color: "gold", data: {} }, true); return; }
    if (t.dataset.ubEdit) { openEditBlock(blocks.find((x) => x.id === t.dataset.ubEdit), false); return; }
    if (t.dataset.ubColor) { blockDlg().dataset.color = t.dataset.ubColor; t.parentElement.querySelectorAll(".ub-color").forEach((x) => x.classList.toggle("on", x === t)); return; }
    if (t.dataset.ubDel) {
      if (!confirm("Удалить этот блок из кабинета?")) return;
      blocks = blocks.filter((x) => x.id !== t.dataset.ubDel);
      await saveBlocks(); blockDlg().close(); renderBlocks(); toast("Блок удалён"); return;
    }
    if (t.dataset.ubCheck) {
      const [id, i] = t.dataset.ubCheck.split(":");
      const b = blocks.find((x) => x.id === id);
      b.data.items[Number(i)].done = !b.data.items[Number(i)].done;
      renderBlocks(); saveBlocks(); return;
    }
    if (t.dataset.ubGoal) {
      const [id, dv] = t.dataset.ubGoal.split(":");
      const b = blocks.find((x) => x.id === id);
      b.data.n = Math.max(0, (b.data.n || 0) + Number(dv));
      renderBlocks(); saveBlocks();
      if (Number(dv) > 0 && b.data.n === b.data.of) toast("Цель достигнута — поздравляем!", 3000);
    }
  });
  document.addEventListener("submit", async (e) => {
    const f = e.target.closest("[data-ub-add]");
    if (!f) return;
    e.preventDefault();
    const v = f.querySelector("input").value.trim();
    if (!v) return;
    const b = blocks.find((x) => x.id === f.dataset.ubAdd);
    b.data.items = [...(b.data.items || []), { t: v, done: false }];
    renderBlocks(); saveBlocks();
    document.querySelector(`[data-ub-add="${b.id}"] input`)?.focus();
  });

  // ─── порядок блоков: перетащите блок за ⠿ — сохраняется в аккаунте ───
  async function layout() {
    const box = $("bento");
    const sections = () => [...box.querySelectorAll(":scope > .g")];
    sections().forEach((g) => {
      if (g.querySelector(":scope > .g-grip")) return;
      const b = document.createElement("button");
      b.type = "button"; b.className = "g-grip"; b.title = "Перетащите, чтобы поменять порядок"; b.setAttribute("aria-label", "Переставить блок");
      b.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="9" cy="6" r="1.4"/><circle cx="15" cy="6" r="1.4"/><circle cx="9" cy="12" r="1.4"/><circle cx="15" cy="12" r="1.4"/><circle cx="9" cy="18" r="1.4"/><circle cx="15" cy="18" r="1.4"/></svg>';
      g.appendChild(b);
    });
    const r = await call("/api/settings");
    blocks = (r.ok && r.data.settings.blocks) || [];
    renderBlocks();   // свои блоки — до сортировки, чтобы встали на сохранённые места
    const order = (r.ok && r.data.settings.cabinet_order) || [];
    if (order.length) {   // сохранённый порядок; новые блоки, которых в нём нет, — в конце
      const byId = Object.fromEntries(sections().map((g) => [g.id, g]));
      order.forEach((id) => { if (byId[id]) box.appendChild(byId[id]); });
      sections().filter((g) => !order.includes(g.id) && g.id !== "wAdmin" && g.id !== "wAdd").forEach((g) => box.appendChild(g));
      if (byId.wAdmin) box.appendChild(byId.wAdmin);
    }
    box.appendChild($("wAdd"));   // «+ Свой блок» — всегда последним
  }
  let drag = null;
  function flip(box, move) {   // плавно: соседи переезжают, а не прыгают
    const before = new Map([...box.children].map((el) => [el, el.getBoundingClientRect()]));
    move();
    for (const [el, a] of before) {
      if (el === drag?.el) continue;
      const b = el.getBoundingClientRect();
      const dx = a.left - b.left, dy = a.top - b.top;
      if (dx || dy) el.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }], { duration: 260, easing: "cubic-bezier(.22,1,.36,1)" });
    }
  }
  document.addEventListener("pointerdown", (e) => {
    const grip = e.target.closest(".g-grip");
    if (!grip || e.button > 0) return;
    e.preventDefault();
    const el = grip.closest(".g");
    drag = { el, moved: false };
    el.classList.add("lifting");
    document.body.classList.add("d-arranging");
    grip.setPointerCapture(e.pointerId);
  });
  document.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const box = $("bento");
    const under = document.elementFromPoint(e.clientX, e.clientY);
    const target = under && under.closest("#bento > .g");
    if (!target || target === drag.el || target.id === "wAdmin" || target.id === "wAdd") return;
    const r = target.getBoundingClientRect();
    const after = e.clientY > r.top + r.height / 2 || (e.clientX > r.left + r.width / 2 && Math.abs(e.clientY - (r.top + r.height / 2)) < r.height / 3);
    const ref = after ? target.nextElementSibling : target;
    if (ref === drag.el || (ref && ref.previousElementSibling === drag.el && !after)) return;
    drag.moved = true;
    flip(box, () => box.insertBefore(drag.el, ref));
  });
  document.addEventListener("pointerup", async () => {
    if (!drag) return;
    const { el, moved } = drag;
    drag = null;
    el.classList.remove("lifting");
    document.body.classList.remove("d-arranging");
    if (!moved) { toast("Потяните блок за ⠿, чтобы переставить"); return; }
    const order = [...$("bento").querySelectorAll(":scope > .g")].map((g) => g.id).filter((id) => id && id !== "wAdmin" && id !== "wAdd");
    const r = await call("/api/settings", { cabinet_order: order });
    toast(r.ok ? "Порядок блоков сохранён" : "Не сохранилось — попробуйте ещё раз");
  });

  // ─── события ─────────────────────────────────────────────────────
  document.addEventListener("input", (e) => { if (e.target.closest("#wCalc input[type=range]")) recalc(); });
  document.addEventListener("change", (e) => {   // банк из списка → его ставка в калькулятор
    if (e.target.id !== "qBank") return;
    const opt = e.target.selectedOptions[0];
    if (opt && opt.dataset.rate) { $("qRate").value = opt.dataset.rate; recalc(); }
  });
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
    else if (b.id === "banksParse") parseBanks();
    else if (b.id === "banksSave") {
      const items = bankRowsNow();
      const r = await call("/api/admin/banks", { items, avg: parsedAvg });
      parsedAvg = null;
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
