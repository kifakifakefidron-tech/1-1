/* Админка 1+1: обзор, пользователи (продлить/сбросить/блок/админ), промокоды,
   скрытые номера агентов, жалобы. Все действия — через /api/admin/*. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const day = (ts) => (ts ? new Date(ts * 1000).toLocaleDateString("ru-RU") : "—");
  const now = () => Date.now() / 1000;
  // 02.11 (Артём: «у правок должно быть время»): «сегодня, 14:05» / «вчера, 09:12» / «28 окт., 18:40» (другой год — с годом)
  const hm = (d) => d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  const dayKey = (d) => d.toDateString();
  function when(ts) {
    if (!ts) return "—";
    const d = new Date(ts * 1000), t = new Date(), y = new Date(Date.now() - 864e5);
    if (dayKey(d) === dayKey(t)) return `сегодня, ${hm(d)}`;
    if (dayKey(d) === dayKey(y)) return `вчера, ${hm(d)}`;
    return d.toLocaleDateString("ru-RU", { day: "numeric", month: "short", ...(d.getFullYear() !== t.getFullYear() ? { year: "numeric" } : {}) }) + `, ${hm(d)}`;
  }
  function dayTitle(ts) {
    const d = new Date(ts * 1000), t = new Date(), y = new Date(Date.now() - 864e5);
    if (dayKey(d) === dayKey(t)) return "Сегодня";
    if (dayKey(d) === dayKey(y)) return "Вчера";
    return d.toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" });
  }
  const ago = (ts) => {
    if (!ts) return "—";
    const m = Math.round((now() - ts) / 60);
    return m < 2 ? "сейчас" : m < 60 ? `${m} мин назад` : m < 1440 ? `${Math.round(m / 60)} ч назад` : when(ts);
  };
  const icn = (n) => (window.ic ? window.ic(n) : "");

  async function call(url, body, method) {
    const opts = { credentials: "same-origin", method: method || (body !== undefined ? "POST" : "GET") };
    if (body !== undefined) { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(body); }
    // База на секунду занята фоновой работой (503) — тихо повторяем, а не показываем ошибку
    let r = await fetch(url, opts);
    for (let i = 0; i < 4 && r.status === 503; i++) {
      await new Promise((res) => setTimeout(res, 1500 * (i + 1)));
      r = await fetch(url, opts);
    }
    let data = {};
    try { data = await r.json(); } catch { /* пусто */ }
    if (!r.ok) throw new Error(data.detail || `Ошибка ${r.status}`);
    return data;
  }
  const fail = (e) => alert(e.message);

  // ─── обзор ──────────────────────────────────────────────────────────────
  async function overview() {
    const o = await call("/api/admin/overview");
    // 24.10g (Артём: «всё съехавшее — сделай удобнее и в стиле прошлых страниц»): карточки по смысловым группам,
    // подпись сверху, крупная цифра снизу; карточка-ссылка — со стрелкой; тревожное — красным
    const card = (n, label, opt = {}) => {
      const inner = `<span>${esc(label)}</span><b>${esc(n)}</b>${opt.extra || ""}${opt.href ? `<i class="adm-go">→</i>` : ""}`;
      const cls = `adm-card${opt.warn ? " warn" : ""}${opt.ok ? " ok" : ""}`;
      return opt.href ? `<a class="${cls}" href="${opt.href}" target="_blank">${inner}</a>` : `<div class="${cls}">${inner}</div>`;
    };
    const sec = (title, cards) => `<section class="adm2-sec"><h3>${title}</h3><div class="adm-cards">${cards.filter(Boolean).join("")}</div></section>`;
    $("overview").innerHTML = [
      sec("Объекты", [
        card(o.listings, "на сайте"), card(o.listings_feed, "из них — СТРЕЛЫ (фид)"),
        card(o.listings_no_district, "без района — не находятся фильтром", { warn: o.listings_no_district > 0, href: "/fix?kind=nodistrict" }),
        card(o.listings_no_map, "без точки на карте", { warn: o.listings_no_map > 0, href: "/fix?kind=nogeo" }),
        card(o.listings_no_place, "скрыты: нет адреса, ЖК и района"),
        ...(o.repair ? [card(o.repair.yes, "с ремонтом — проверить", { href: "/?rep=yes" }), card(o.repair.no, "без ремонта — проверить", { href: "/?rep=no" }),
          card(o.repair.none, "ремонт не указан")] : []),
        card(o.queue, "сообщений ждут разбора", { warn: o.queue > 500, extra: o.queue ? `<span class="adm-q-btns">
          <button type="button" class="chip" data-queue="6">Пропустить старше 6 ч</button>
          <button type="button" class="chip" data-queue="all">Сбросить очередь</button></span>` : "" }),
      ]),
      sec("Люди и доступ", [
        card(o.users, "пользователей"), card(o.users_access, "с открытым доступом", { ok: true }), card(o.users_paid, "оплатили подписку", { ok: o.users_paid > 0 }),
        ...(o.agent_consent ? [card(`${o.agent_consent.verified} из ${o.agent_consent.agents}`, "агентов подтвердили номер"),
          card(o.agent_consent.published, "дали согласие на показ номера"), card(o.agent_consent.hidden, "агентов скрыли номер")] : []),
        card(o.views_24h, "открытий номеров за сутки"), card(o.complaints_new, "новых жалоб", { warn: o.complaints_new > 0 }),
        card(o.optouts, "скрытых номеров"),
      ]),
      sec("Сервис", [
        card(o.wappi_enabled ? "включён" : "на паузе", "сбор из чатов (Wappi)", { ok: o.wappi_enabled, warn: !o.wappi_enabled }),
        card(o.payments ? "включена" : "выключена", `оплата (${o.price} ₽ / ${o.period_days} дн.)`, { ok: o.payments }),
        card(day(o.feed_synced), "фид СТРЕЛ обновлён"),
      ]),
      sec("Автоматические проверки", [
        ...(o.p0_report ? [card(o.p0_report.phones, "номеров вырезано из описаний"),
          card(o.p0_report.prices, "цена в карточке ≠ цене в тексте", { warn: o.p0_report.prices > 0 })] : []),
        ...(o.cx_report ? [card(o.cx_report.listings, `объектов: одно название ЖК (групп ${o.cx_report.groups})`)] : []),
        ...(o.geo_daily ? [card((o.geo_daily.rules || 0), "ошибочных правил отключено сегодня")] : []),
        ...(o.geo_cleanup ? [card((o.geo_cleanup.rules || 0) + (o.geo_cleanup.aliases || 0), "плохих правил отключено (уборка)"),
          card(o.geo_cleanup.junk || 0, "мусорных «ЖК» убрано"), card(o.geo_cleanup.listings || 0, "объектов поправлено уборкой")] : []),
        ...(o.price_report ? [card(o.price_report.scale, "цен «в тысячах» исправлено"),
          card(o.price_report.text, "цен вернули к цене из текста"), card(o.price_report.doubt, "цен «под вопросом»")] : []),
      ]),
    ].join("") + (o.p0_report && o.p0_report.price_examples.length ? `<details class="adm-p0"><summary>Цена в карточке ≠ цене в тексте —
      ${o.p0_report.prices} (проверьте и поправьте цену в окне объекта)</summary><ul>${o.p0_report.price_examples.map((e) =>
        `<li><a href="/?open=${e.id}" target="_blank">${esc(e.title)}</a> — в карточке ${esc(Number(e.card).toLocaleString("ru-RU"))} ₽,
        в тексте ${esc(Number(e.text).toLocaleString("ru-RU"))} ₽</li>`).join("")}</ul></details>` : "")
      + (o.price_report && o.price_report.examples.length ? `<details class="adm-p0"><summary>Проверка цен: что поменяли
      (каждую правку можно откатить в окне объекта → «История правок»)</summary><ul>${o.price_report.examples.map((e) =>
        `<li><a href="/?open=${e.id}" target="_blank">${esc(e.title)}</a> — было ${esc(Number(e.before).toLocaleString("ru-RU"))} ₽,
        стало ${esc(Number(e.after).toLocaleString("ru-RU"))} ₽ (${e.why === "text" ? "цена из текста объявления" : "цена была в тысячах"})</li>`).join("")}</ul></details>` : "");
    if (o.billing) {   // 18.10: оплаты сервисов — напоминание за 2 дня и в день оплаты (колокольчик + письмо)
      const d = (ts) => (ts ? new Date(ts * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "long" }) : "—");
      $("overview").insertAdjacentHTML("beforeend", `<details class="adm-p0 adm-bill" open><summary>Оплаты сервисов — напомню за 2 дня до срока</summary>
        <table class="adm-bill-t"><tr><th>Сервис</th><th>Оплачен</th><th>Следующая</th><th></th></tr>${o.billing.map((s) => `<tr class="${s.left == null || s.left <= 2 ? "due" : ""}">
          <td><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.name)}</a><small>каждые ${s.period} дн.</small></td>
          <td>${d(s.paid)}</td><td>${s.due ? `${d(s.due)}${s.left < 0 ? ` <b class="bad">просрочено ${-s.left} дн.</b>` : s.left <= 2 ? ` <b class="bad">через ${s.left} дн.</b>` : ""}` : "<b class='bad'>отметьте дату</b>"}</td>
          <td><button type="button" class="chip" data-bill="${esc(s.key)}">Оплатил сегодня</button></td></tr>`).join("")}</table></details>`);
    }
    if (o.llm_on != null) {
      $("overview").insertAdjacentHTML("beforeend", `<p class="note">Нейросеть DeepSeek: <b class="${o.llm_on ? "" : "bad"}">${o.llm_on ? "включена — новые сообщения разбирает она + правила" : "ВЫКЛЮЧЕНА — разбор только правилами (нет DEEPSEEK_API_KEY в .env)"}</b>${o.rules_fix ? ` · исправлено по новым правилам: ${Object.entries(o.rules_fix).map(([k, v]) => `${esc(k)} — ${v}`).join(", ")}` : ""}${o.media_strict && o.media_strict.checked ? ` · фото из чатов проверены (только подпись к фото): оставлено <b>${o.media_strict.kept}</b>, удалено <b>${o.media_strict.removed}</b> у ${o.media_strict.listings} объектов` : ""}${o.datafix ? ` · проверка 10.11: аренда вместо «продажи за миллионы» — <b>${o.datafix.rent}</b>, «Цена уточняется» — <b>${o.datafix.price_doubt}</b>, этаж из «50/50» убран — <b>${o.datafix.floor}</b>, не Краснодар (Адлер, Сочи, Москва, у моря) — <b>${o.datafix.foreign}</b>${(o.datafix.examples || []).length ? ` (${o.datafix.examples.slice(0, 6).map(e => `<a href="/?open=${e.id}" target="_blank">${esc(e.what)}</a>`).join(", ")})` : ""}` : ""}${o.rent_leak && o.rent_leak.moved ? ` · из аренды в продажу (продажа рядом с «сдам +КУ» в одном сообщении): <b>${o.rent_leak.moved}</b> — ${o.rent_leak.examples.slice(0, 5).map(e => `<a href="/?open=${e.id}" target="_blank">${esc(e.title || "#" + e.id)}</a>`).join(", ")}` : ""}</p>`);
    }
    if (o.own_match) {   // 18.10: свои объекты — одна карточка
      const m = o.own_match;
      $("overview").insertAdjacentHTML("beforeend", `<details class="adm-p0"><summary>Ваши объекты (номер СТРЕЛ): копий из чатов приклеено к сайту —
        ${m.to_feed || 0}, дублей из чатов объединено — ${m.merged || 0}${m.nopair ? `, постов с фото без пары на сайте скрыто — ${m.nopair} (проверьте ниже)` : ""}</summary>
        <p class="note">Объявление из рассылки (портянка, пост без артикула) с вашим номером — тот же объект, что на сайте: оно приклеивается к карточке
          с сайта (там все фото). Совпадение строгое: тип, комнаты, площадь ±1,5 м², этаж, улица и дом или ЖК, цена ±15 %.</p>
        <ul>${(m.examples || []).map((e) => `<li><a href="/?open=${e.id}" target="_blank">${esc(e.title)}</a> → ${e.to ? `<a href="/?open=${e.to}" target="_blank">карточка ${e.to}</a>` : "—"} (${esc(e.why)})</li>`).join("")}</ul></details>`);
    }
    if (o.life) {   // 17.10: жизнь объектов — рамки и что где
      const L = o.life, a = L.active_by_age;
      $("overview").insertAdjacentHTML("beforeend", `<details class="adm-p0" open><summary>Жизнь объектов: на сайте ${L.rules.stale_days} дн. без повтора
        → в архиве ${L.rules.archive_days} дн. → удаление</summary>
        <p>На сайте <b>${esc(L.active)}</b> · без повтора в чатах: 0–3 дн. — ${a["0-3"]}, 4–7 — ${a["4-7"]}, 8–14 — ${a["8-14"]}, 15+ — ${a["15+"]}
          (сегодня уйдут в архив).</p>
        <p>В архиве <b>${esc(L.archive)}</b>: ${L.archive_by_reason.map((x) => `${esc(x.label)} — ${x.n}`).join("; ") || "пусто"}.</p>
        <p>Снято «раньше срока»: <b class="${L.archived_early ? "bad" : ""}">${L.archived_early}</b> (должно быть 0) ·
          удалится за 7 дней: ${L.delete_within_7d} · удалено всего: ${L.deleted_total}${L.restored_once != null ? ` · после обновления вернули на сайт: ${L.restored_once}` : ""}.</p>
        <p class="note">Срок считается от последнего сообщения в чатах, но не раньше, чем от появления объекта на 1+1. Повтор в чатах сразу
          возвращает объект из архива. Удалённые вами и «всё от номера» не стираются никогда.</p></details>`);
    }
    online();
    stats();
  }

  // ─── кто сейчас на сайте (обновляется сам раз в 30 с, пока открыт «Обзор») ───
  const PAGE_NAMES = { "/": "поиск", "/cabinet": "кабинет", "/planner": "планер", "/clients": "клиенты", "/work": "избранное",
    "/my": "мои объекты", "/my/edit": "правка объекта", "/picks": "подборки", "/market": "аналитика", "/notifications": "уведомления",
    "/settings": "настройки", "/pay": "оплата", "/admin": "админка", "/learn": "обучение", "/fix": "разбор", "/how": "как это работает" };
  // ─── 27.10: график посещений за 30 дней (одна ось — все ряды в людях/заходах; просмотры — в подсказке и таблице) ───
  const VC_SERIES = [
    { k: "sessions", label: "Сессии", hint: "заходов на сайт (перерыв 30 мин — новая)" },
    { k: "total", label: "Посетители", hint: "разных людей" },
    { k: "users", label: "Вошли", hint: "с аккаунтом" },
    { k: "guests", label: "Гости", hint: "без входа" },
    { k: "mobile", label: "С телефона", hint: "посетителей с телефона" },
  ];
  const vcOn = new Set(["sessions", "total"]);   // что показано — переживает обновление блока раз в 30 с
  let vcDays = [];
  function dayLabel(d, full) {
    return new Date(d + "T12:00:00").toLocaleDateString("ru-RU", full ? { day: "numeric", month: "short", weekday: "short" }
      : { day: "numeric", month: "short" }).replace(".", "");
  }
  function niceMax(v) {
    if (v <= 4) return 4;
    const p = Math.pow(10, Math.floor(Math.log10(v))), m = v / p;
    return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10) * p;
  }
  function visitsChart(days) {
    vcDays = days.slice().reverse();   // старые слева
    const box = $("visitsChart");
    if (!box) return;
    const sum = (k) => vcDays.reduce((a, d) => a + (d[k] || 0), 0);
    const today = vcDays[vcDays.length - 1] || {};
    box.innerHTML = `<div class="vc-head"><div class="vc-kpi"><span>Сессии за 30 дней</span><b>${sum("sessions").toLocaleString("ru-RU")}</b></div>
        <div class="vc-kpi"><span>Сегодня</span><b>${(today.sessions || 0).toLocaleString("ru-RU")}</b>
          <small>${today.total || 0} чел. · ${today.views || 0} просм.</small></div></div>
      <div class="vc-legend" role="group" aria-label="Что показать на графике">${VC_SERIES.map((s, i) => `<button type="button"
          class="vc-lg" data-vc="${s.k}" aria-pressed="${vcOn.has(s.k)}" style="--c: var(--vc-${i + 1})" title="${esc(s.hint)}">
          <i></i>${esc(s.label)}</button>`).join("")}</div>
      <div class="vc-plot"><svg role="img" aria-label="Посещения за 30 дней"></svg><div class="vc-tip" hidden></div></div>`;
    drawVisits();
  }
  function drawVisits() {
    const box = $("visitsChart");
    if (!box || !vcDays.length) return;
    const plot = box.querySelector(".vc-plot"), svg = plot.querySelector("svg"), tip = plot.querySelector(".vc-tip");
    const W = Math.max(280, plot.clientWidth), H = W < 520 ? 200 : 240;
    const L = 34, R = 10, T = 12, B = 26, iw = W - L - R, ih = H - T - B;
    const shown = VC_SERIES.map((s, i) => ({ ...s, i })).filter((s) => vcOn.has(s.k));
    const max = niceMax(Math.max(1, ...vcDays.flatMap((d) => shown.map((s) => d[s.k] || 0))));
    const n = vcDays.length, x = (i) => L + (n === 1 ? iw / 2 : (i * iw) / (n - 1)), y = (v) => T + ih - (v / max) * ih;
    const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => Math.round(max * f));
    const step = W < 520 ? 7 : 5;
    let s = `<g class="vc-grid">${ticks.map((t) => `<line x1="${L}" x2="${W - R}" y1="${y(t)}" y2="${y(t)}"/>
      <text x="${L - 6}" y="${y(t) + 4}" text-anchor="end">${t}</text>`).join("")}</g>
      <g class="vc-x">${vcDays.map((d, i) => ((n - 1 - i) % step === 0 ? `<text x="${x(i)}" y="${H - 6}" text-anchor="middle">${esc(dayLabel(d.day))}</text>` : "")).join("")}</g>`;
    for (const sr of shown.slice().reverse()) {   // первый ряд — сверху
      const pts = vcDays.map((d, i) => `${x(i).toFixed(1)},${y(d[sr.k] || 0).toFixed(1)}`).join(" ");
      if (sr.k === "sessions") s += `<polygon class="vc-area" points="${x(0)},${y(0)} ${pts} ${x(n - 1)},${y(0)}" style="fill: var(--vc-${sr.i + 1})"/>`;
      s += `<polyline class="vc-line" points="${pts}" style="stroke: var(--vc-${sr.i + 1})"/>`;
    }
    s += `<line class="vc-cross" x1="0" x2="0" y1="${T}" y2="${T + ih}" visibility="hidden"/><g class="vc-dots"></g>
      <rect class="vc-hit" x="${L}" y="${T}" width="${iw}" height="${ih}"/>`;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("width", W);
    svg.setAttribute("height", H);
    svg.innerHTML = s;
    const cross = svg.querySelector(".vc-cross"), dots = svg.querySelector(".vc-dots");
    const show = (clientX) => {
      const r = svg.getBoundingClientRect();
      const i = Math.max(0, Math.min(n - 1, Math.round(((clientX - r.left) * (W / r.width) - L) / (iw / Math.max(1, n - 1)))));
      const d = vcDays[i];
      cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
      dots.innerHTML = shown.map((sr) => `<circle cx="${x(i)}" cy="${y(d[sr.k] || 0)}" r="4.5" style="fill: var(--vc-${sr.i + 1})"/>`).join("");
      tip.innerHTML = `<b>${esc(dayLabel(d.day, true))}</b>${VC_SERIES.map((sr, j) => `<span class="${vcOn.has(sr.k) ? "" : "off"}">
          <i style="background: var(--vc-${j + 1})"></i>${esc(sr.label)}<em>${d[sr.k] || 0}</em></span>`).join("")}
        <span class="views">Просмотры<em>${d.views || 0}</em></span>`;
      tip.hidden = false;
      const px = (x(i) / W) * r.width;
      tip.style.left = `${Math.min(Math.max(px - tip.offsetWidth / 2, 0), r.width - tip.offsetWidth)}px`;
    };
    const hide = () => { cross.setAttribute("visibility", "hidden"); dots.innerHTML = ""; tip.hidden = true; };
    const hit = svg.querySelector(".vc-hit");
    hit.addEventListener("pointermove", (e) => show(e.clientX));
    hit.addEventListener("pointerdown", (e) => show(e.clientX));
    hit.addEventListener("pointerleave", (e) => { if (e.pointerType === "mouse") hide(); });
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-vc]");
    if (!b) return;
    const k = b.dataset.vc;
    if (vcOn.has(k)) { if (vcOn.size > 1) vcOn.delete(k); } else vcOn.add(k);   // хотя бы одна линия остаётся
    document.querySelectorAll("[data-vc]").forEach((x) => x.setAttribute("aria-pressed", vcOn.has(x.dataset.vc)));
    drawVisits();
  });
  let vcResize = 0;
  window.addEventListener("resize", () => { clearTimeout(vcResize); vcResize = setTimeout(drawVisits, 150); });

  async function online() {
    const o = await call("/api/admin/online");
    if (!o || o.now == null) return;
    let box = $("onlineBox");
    if (!box) {
      box = document.createElement("div");
      box.id = "onlineBox";
      box.className = "adm-online";
      $("overview").before(box);
    }
    const ago = (s) => (s < 60 ? "только что" : `${Math.round(s / 60)} мин назад`);
    box.innerHTML = `<div class="ao-head"><span class="ao-dot"></span><b>${o.now}</b><span>${o.now === 1 ? "человек" : "человек"} на сайте сейчас</span>
        <small>вошли: ${o.users} · гости: ${o.guests} · с телефона: ${o.mobile} · за час: ${o.hour} · за сутки: ${o.day}</small></div>
      ${o.people.length ? `<ul class="ao-list">${o.people.map((p) => `<li><b>${esc(p.name)}</b>${p.admin ? ' <span class="note">(админ)</span>' : ""}
        <span>${esc(PAGE_NAMES[p.path] || p.path)}</span><small>${p.mobile ? "телефон" : "компьютер"} · ${ago(p.ago)}</small></li>`).join("")}</ul>` : ""}
      ${o.pages.length ? `<p class="note">Где сейчас: ${o.pages.map((x) => `${esc(PAGE_NAMES[x.path] || x.path)} — ${x.n}`).join(", ")}</p>` : ""}
      ${(o.days || []).length ? `<div class="vc" id="visitsChart"></div>
        <details class="ao-days"><summary>Таблица по дням</summary>
        <div class="ao-tbl"><table><thead><tr><th>День</th><th>Сессии</th><th>Посетители</th><th>Вошли</th><th>Гости</th><th>С телефона</th><th>Просмотры</th></tr></thead>
        <tbody>${o.days.map((d) => `<tr><td>${esc(dayLabel(d.day, true))}</td>
          <td><b>${d.sessions}</b></td><td>${d.total}</td><td>${d.users}</td><td>${d.guests}</td><td>${d.mobile}</td><td>${d.views}</td></tr>`).join("")}</tbody></table></div>
        <p class="note">Сессия — один заход: обновления и переходы по страницам в течение 30 минут — та же сессия, вернулся позже —
          новая (как в статистике Тильды). Посетители — разные люди за день. Гостей без согласия на cookie считаем анонимно: метка
          меняется каждые сутки, адрес не храним; один человек на телефоне и компьютере — два посетителя.</p></details>` : ""}`;
    if ((o.days || []).length) visitsChart(o.days);
    clearTimeout(online.t);
    online.t = setTimeout(() => { if (!document.hidden && $("onlineBox")) online(); }, 30000);
  }

  // ─── подробная статистика ───────────────────────────────────────────────
  const n0 = (x) => (x == null ? "—" : Number(x).toLocaleString("ru-RU"));
  const money = (p, deal) => (p == null ? "—" : deal === "rent" ? `${n0(Math.round(p / 1000))} тыс. ₽/мес`
    : p >= 1e6 ? `${(p / 1e6).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} млн ₽` : `${n0(p)} ₽`);
  const box =(title, html, wide) => `<section class="st-box${wide ? " wide" : ""}"><h3>${title}</h3>${html}</section>`;
  const tbl = (head, rows) => `<table class="st-t"><thead><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
  const kv = (pairs) => `<dl class="st-kv">${pairs.map(([k, v, hint]) =>
    `<div${hint ? ` title="${esc(hint)}"` : ""}><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</dl>`;
  const objLink = (o, val) => `<a href="/?open=${o.id}" target="_blank">${esc(o.title)}</a> · ${esc(money(o.price, o.deal))}${val}`;

  async function stats() {
    let s;
    try { s = await call("/api/admin/stats"); } catch (e) { $("stats").innerHTML = `<p class="note">${esc(e.message)}</p>`; return; }
    const L = s.listings, U = s.users, M = s.messages, Q = s.quality;
    const per = (o) => [n0(o.today), n0(o.yesterday), n0(o.d7), n0(o.d30)];
    const maxDay = Math.max(1, ...s.days.map((d) => Math.max(d.added, d.removed)));
    const bars = s.days.map((d) => {
      const lbl = new Date(d.day * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
      return `<div class="st-day" title="${lbl}: добавлено ${d.added}, снято ${d.removed}, сообщений ${d.messages}">
        <div class="st-bars"><i class="add" style="height:${(d.added / maxDay) * 100}%"></i><i class="rem" style="height:${(d.removed / maxDay) * 100}%"></i></div>
        <b>${d.added ? "+" + d.added : ""}</b><small>${d.removed ? "−" + d.removed : ""}</small><span>${lbl}</span></div>`;
    }).join("");
    const kinds = { listing: "объявления", request: "запросы «куплю/ищу»", other: "не про недвижимость", skipped: "пропущены (старые)", error: "ошибки разбора" };
    const src = { wa: "WhatsApp", tg: "Telegram", max: "MAX", manual: "вручную", chat: "из чатов", feed: "СТРЕЛЫ (фид)", own: "свои объявления агентов" };
    const fix = (kind, mode) => `<a href="/fix?mode=${mode}&kind=${kind}" target="_blank">разобрать →</a>`;
    const learnLink = `<p class="note">Всё про разбор и обучение — на одной странице: <a href="/learn">Обучение и разбор →</a></p>`;
    $("stats").innerHTML = [
      box("Объявления: добавлено и снято", tbl(["", "Сегодня", "Вчера", "7 дней", "30 дней"], [
        ["➕ Добавлено новых", ...per(L.added)],
        ["➖ Снято с сайта", ...per(L.removed)],
        ["🔁 Прислали повторно (объект ещё продаётся)", ...per(L.reposted)],
      ]) + `<p class="note">Сейчас на сайте: <b>${n0(L.visible)}</b>. За 7 дней цена снизилась у ${n0(L.price_down_d7)}, выросла у ${n0(L.price_up_d7)}.</p>`, true),
      box("По дням (14 дней)", `<div class="st-days">${bars}</div>
        <p class="note"><i class="st-dot add"></i> добавлено <i class="st-dot rem"></i> снято · наведите на столбик — подробности</p>`, true),
      box("Почему сняли", L.removed_reasons.length ? tbl(["Причина", "Сегодня", "7 дней"],
        L.removed_reasons.map((r) => [esc(r.reason), n0(r.today), n0(r.d7)])) : `<p class="note">За 7 дней ничего не снято.</p>`),
      box("Что на сайте", tbl(["", "Продажа", "Аренда"], Object.entries(s.mix).map(([t, v]) => [esc(t), n0(v.sale), n0(v.rent)]))
        + kv(Object.entries(L.by_source).map(([k, v]) => [src[k] || k, n0(v)]))),
      box("Цены (медиана)", kv([["Квартира, продажа", money(s.prices.flat_sale_median, "sale")],
        ["Цена м² квартиры", s.prices.flat_m2_median ? `${n0(s.prices.flat_m2_median)} ₽` : "—"],
        ["Квартира, аренда", money(s.prices.rent_median, "rent")]])),
      box("Качество разбора", kv([
        ["Без района", `${n0(Q.no_district)} ${fix("nodistrict", "place")}`, "не находятся фильтром по району"],
        ["Квартиры без ЖК", n0(Q.flats_no_complex), "это нормально: не у всех домов есть ЖК"],
        ["Нет точки на карте", `${n0(Q.no_map)} ${fix("none", "geo")}`],
        ["Точка примерная", `${n0(Q.approx_map)} ${fix("approx", "geo")}`],
        ["Без цены", n0(Q.no_price)], ["Без площади", n0(Q.no_area)], ["Без фото", n0(Q.no_photo)],
        ["Скрыты: нет адреса, ЖК и района", n0(Q.hidden_no_place)],
        ["Поправлено вами", n0(Q.admin_fixed)], ["Выучено правил", n0(Q.learned_rules)], ["Выучено точек", n0(Q.learned_points)],
        ["Сервис сам поставил точек", `${n0(Q.auto_points)} ${fix("auto", "geo")}`, "по фиду СТРЕЛ, контурам ЖК и районов"],
        ["Адресов в справочнике OSM", n0(Q.osm_addr), "обновляется раз в месяц, © OpenStreetMap"],
        ["Сервис сам поставил районов", `${n0(Q.auto_districts)} ${fix("auto", "place")}`, "по карте районов и соседним объявлениям"],
      ])),
      box("Обучение и разбор", learnLink),
      box("Сообщения из чатов", kv([
        ["Сегодня", n0(M.today)], ["За 7 дней", n0(M.d7)], ["Ждут разбора", n0(M.queue)],
        ["Ошибок за 7 дней", n0(M.errors_d7)], ["Активных чатов за 7 дней", n0(M.chats_active_d7)], ["Отключено чатов", n0(M.chats_blocked)],
        ...Object.entries(M.kinds_d7).map(([k, v]) => [`— ${kinds[k] || k}`, n0(v)]),
        ...Object.entries(M.by_source_d7).map(([k, v]) => [`— ${src[k] || k}`, n0(v)]),
      ])),
      box("Самые полезные чаты (7 дней)", s.top_chats.length ? tbl(["Чат", "Сообщений", "Новых объектов"],
        s.top_chats.map((c) => [esc(c.name || "—") + ` <small>${esc(src[c.source] || c.source)}</small>`, n0(c.messages), n0(c.new_objects)]))
        : `<p class="note">Сообщений за 7 дней нет${s.system.wappi_enabled ? "" : " — сбор из чатов на паузе"}.</p>`, true),
      box("Районы", tbl(["Район", "Объектов"], s.top_districts.map((d) => [esc(d.name), n0(d.n)]))),
      box("ЖК", tbl(["ЖК", "Объектов"], s.top_complexes.map((d) => [esc(d.name), n0(d.n)]))),
      box("Пользователи", tbl(["", "Сегодня", "7 дней", "30 дней"], [
        ["Новых", n0(U.new.today), n0(U.new.d7), n0(U.new.d30)],
        ["Заходили", n0(U.active.today), n0(U.active.d7), ""],
        ["Открыли номеров", n0(U.phone_views.today), n0(U.phone_views.d7), ""],
      ]) + kv([["Всего", n0(U.total)], ["На пробной неделе", n0(U.trial)], ["Оплатили", n0(U.paid)],
        ["Доступ кончится за 3 дня", n0(U.access_ends_3d)], ["Доступ кончился", n0(U.expired)],
        ["Открывали номера за 7 дней (людей)", n0(U.phone_viewers_d7)],
        ["В избранном (за 7 дней)", `${n0(U.favorites)} (${n0(U.favorites_d7)})`], ["Следят за поисками", n0(U.saved_searches)],
        ["Уведомлений за 7 дней", n0(U.notices_d7)], ["Заметок", n0(U.notes)],
        ["Агентов с подтверждённым номером", n0(U.agents_verified)], ["Своих объявлений агентов", n0(U.own_listings)],
        ["Правок агентов за 7 дней", n0(U.agent_edits_d7)], ["Новых жалоб", n0(U.complaints_new)], ["Скрытых номеров", n0(U.optouts)]]), true),
      box("Чаще открывают номер (7 дней)", s.most_viewed.length
        ? `<ol class="st-list">${s.most_viewed.map((o) => `<li>${objLink(o, ` · <b>${o.views}</b>`)}</li>`).join("")}</ol>` : `<p class="note">Пока нет.</p>`),
      box("Чаще в избранном", s.most_fav.length
        ? `<ol class="st-list">${s.most_fav.map((o) => `<li>${objLink(o, ` · <b>${o.n}</b> ♥`)}</li>`).join("")}</ol>` : `<p class="note">Пока нет.</p>`),
      box("Фоновая работа", kv([
        ["Последний цикл", `${ago(s.system.worker_tick)}${s.system.worker_ok === false ? " ⚠️ давно — проверьте worker" : ""}`],
        ["Фид СТРЕЛ обновлён", ago(s.system.feed_synced)], ["Сбор из чатов", s.system.wappi_enabled ? "включён" : "на паузе"]])),
    ].join("");
  }

  // ─── пользователи ───────────────────────────────────────────────────────
  // 24.10: все пользователи — последний заход, регистрация, как входит, номера, доступ, оплаты и что делает на сайте
  let userPage = 1, userF = "";
  // 02.11 (Артём: «пользователи — удобнее»): плитки сверху — это фильтры; список карточками; редкие действия — в меню «⋯»
  const U_TILES = [["", "Все", "total"], ["seen1", "Заходили за сутки", "seen_1d"], ["seen7", "Заходили за неделю", "seen_7d"],
    ["new7", "Новые за неделю", "new_7d"], ["access", "С доступом", "access"], ["paid", "Оплатили", "paid"],
    ["verified", "Подтвердили номер", "verified"], ["noaccess", "Доступ кончился", "noaccess"], ["admin", "Админы", "admin"],
    ["blocked", "Заблокированы", "blocked"]];
  async function users() {
    const d = await call(`/api/admin/users?q=${encodeURIComponent($("userQ").value)}&sort=${$("userSort").value}&page=${userPage}&f=${userF}`);
    const s = d.summary || {};
    $("userSum").innerHTML = U_TILES.filter(([f, , k]) => !f || s[k] || userF === f).map(([f, t, k]) =>
      `<button type="button" class="us-tile${userF === f ? " on" : ""}" data-uf="${f}"><b>${(s[k] ?? 0).toLocaleString("ru-RU")}</b><span>${t}</span></button>`).join("");
    const t = now();
    $("users").innerHTML = d.items.length ? d.items.map((u) => {
      const until = Math.max(u.trial_until, u.paid_until);
      const [stCls, stTxt, stSub] = u.blocked ? ["bad", "Заблокирован", ""]
        : u.is_admin ? ["adm", "Администратор", ""]
        : until > t ? ["ok", u.paid_until > t ? "Оплачен" : "Пробный", `до ${day(until)} · ${Math.ceil((until - t) / 86400)} дн.`]
        : ["off", "Без доступа", until ? `кончился ${day(until)}` : ""];
      const name = u.name || u.email || u.phone || (u.tg_username ? "@" + u.tg_username : `Пользователь №${u.id}`);
      const contacts = [u.email, u.phone, u.tg_username ? "@" + u.tg_username : ""].filter(Boolean).filter((x) => x !== name);
      const work = [[u.n_clients, "клиентов"], [u.n_picks, "подборок"], [u.n_fav, "избранное"], [u.n_saved, "поисков"],
        [u.n_events, "в планере"], [u.n_own, "своих объектов"]].filter(([n]) => n);
      const fresh = u.last_seen && t - u.last_seen < 600;
      return `<article class="us-row${u.blocked ? " blocked" : ""}">
        <span class="us-ava${fresh ? " live" : ""}" aria-hidden="true">${esc((name.replace(/^[@+\d\s]+/, "") || "№")[0].toUpperCase())}</span>
        <div class="us-who"><b>${esc(name)}</b><small class="us-id">№${u.id}</small>
          ${contacts.length ? `<span class="us-c">${contacts.map(esc).join(" · ")}</span>` : ""}
          <span class="us-tags"><em>вход: ${esc(u.login)}</em>${u.verified_phones ? `<em class="ok">${icn("check")}номер ${esc(u.verified_phones)}</em>` : ""}
            ${u.consent_at ? "" : `<em class="warn">без согласия</em>`}${u.mail_off ? `<em>письма выкл.</em>` : ""}${u.ref_email ? `<em>от ${esc(u.ref_email)}</em>` : ""}</span></div>
        <div class="us-col"><small>Последний заход</small><b>${ago(u.last_seen)}</b><span>${u.n_sessions || 0} устр.</span></div>
        <div class="us-col"><small>Регистрация</small><b>${when(u.created)}</b></div>
        <div class="us-col"><small>Доступ</small><b class="us-st ${stCls}">${stTxt}</b><span>${stSub}${u.paid_sum ? ` · ${u.paid_sum.toLocaleString("ru-RU")} ₽` : ""}</span></div>
        <div class="us-col us-work"><small>На сайте</small>${work.length ? work.map(([n, w]) => `<span><b>${n}</b> ${w}</span>`).join("") : "<span>—</span>"}</div>
        <div class="us-acts">
          <button type="button" class="us-b" data-u="${u.id}" data-a="extend" data-days="7">+7 дн.</button>
          <details class="us-menu"><summary aria-label="Ещё действия">⋯</summary><div class="us-pop">
            <button type="button" data-u="${u.id}" data-a="extend" data-days="30">${icn("plus")}Продлить на 30 дней</button>
            <button type="button" data-u="${u.id}" data-a="reset">${icn("x-circle")}Сбросить доступ</button>
            <button type="button" data-u="${u.id}" data-a="${u.is_admin ? "unadmin" : "admin"}">${icn("user")}${u.is_admin ? "Снять права админа" : "Сделать админом"}</button>
            <button type="button" class="danger" data-u="${u.id}" data-a="${u.blocked ? "unblock" : "block"}">${icn("ban")}${u.blocked ? "Разблокировать" : "Заблокировать"}</button>
          </div></details></div></article>`;
    }).join("") : `<p class="ad-empty">Никого не нашлось — попробуйте другой фильтр или поиск.</p>`;
    $("userPager").innerHTML = d.pages > 1 ? `<button class="chip" data-upage="-1"${userPage <= 1 ? " disabled" : ""}>← Назад</button>
      <span>стр. ${userPage} из ${d.pages} · найдено ${d.total}</span>
      <button class="chip" data-upage="1"${userPage >= d.pages ? " disabled" : ""}>Дальше →</button>` : `<span>найдено ${d.total}</span>`;
  }
  document.addEventListener("click", (e) => {
    const f = e.target.closest("[data-uf]");
    if (f) { userF = f.dataset.uf; userPage = 1; users().catch(fail); return; }
    const b = e.target.closest("[data-upage]");
    if (b) { userPage = Math.max(1, userPage + Number(b.dataset.upage)); users().catch(fail); return; }
    // меню «⋯»: открыто одно, клик мимо — закрыть
    document.querySelectorAll("details.us-menu[open]").forEach((m) => { if (!m.contains(e.target)) m.open = false; });
  });
  document.addEventListener("change", (e) => { if (e.target.id === "userSort") { userPage = 1; users(); } });

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
  // 02.11 (Артём): жалоба сама ничего не снимает — объект на сайте, пока админ не решит; решения — кнопками в карточке жалобы
  const CM_REASON = { sold: "Продан или неактуален", wrong: "Неверные данные", my_phone: "Это мой номер", photo: "Чужие фото", other: "Другое" };
  const CM_STATUS = { new: "Новая", kept: "Оставили как есть", done: "Исправлено", hidden: "Сняли с сайта", deleted: "Удалили навсегда",
    restored: "Вернули на сайт" };
  const CM_OBJ = { active: ["ok", "на сайте"], hidden: ["warn", "снят вами"], deleted: ["bad", "удалён навсегда"], gone: ["off", "снят (продан / срок)"] };
  let cmShow = "new";
  function complaintCard(c, compact) {
    // 29.10: кто подал жалобу — номер (звонок в одно нажатие), имя, почта
    const w = c.who;
    const who = !w ? `<span class="cm-who">гость</span>` : `<span class="cm-who">${w.phones.length
      ? w.phones.map((p, i) => `<a class="cmp-tel" href="tel:${esc(w.tel[i])}">${esc(p)}</a>`).join(", ") : "номера нет"}${
      [w.name, w.email].filter(Boolean).map((x) => ` · ${esc(x)}`).join("")}</span>`;
    const own = (() => { try { return JSON.parse(c.photos || "[]"); } catch { return []; } })();
    const ph = [...(c.text || "").matchAll(/^Фото: (\S+)$/gm)].map((m) => m[1]);
    const text = (c.text || "").replace(/^Фото: .*$/gm, "").trim();
    const [oc, ot] = CM_OBJ[c.listing_state] || ["off", "объекта нет"];
    const photo = own[0];
    const acts = c.status === "new" ? `
        <button type="button" class="cm-b" data-cm-act="keep" data-c="${c.id}" title="Жалоба не подтвердилась — объект остаётся">${icn("check")}Оставить</button>
        <button type="button" class="cm-b" data-cm-act="done" data-c="${c.id}" title="Данные поправили — объект остаётся">${icn("edit")}Исправлено</button>
        ${c.listing_state === "active" ? `<button type="button" class="cm-b warn" data-cm-act="hide" data-c="${c.id}" title="Убрать с сайта; повтор поста не вернёт">${icn("eye")}Снять с сайта</button>
        <button type="button" class="cm-b bad" data-cm-act="delete" data-c="${c.id}" title="Навсегда: тот же текст и объект больше не появятся">${icn("trash")}Удалить навсегда</button>` : ""}`
      : `<span class="cm-done">${esc(CM_STATUS[c.status] || c.status)}${c.decided_at ? ` · ${when(c.decided_at)}` : ""}</span>
        ${["hidden", "deleted"].includes(c.listing_state) ? `<button type="button" class="cm-b" data-cm-act="restore" data-c="${c.id}">${icn("history")}Вернуть на сайт</button>` : ""}
        <button type="button" class="cm-b ghost" data-cm-act="reopen" data-c="${c.id}">Снова в новые</button>`;
    return `<article class="cm-card${c.status === "new" ? " new" : ""}">
      ${compact ? "" : `<a class="cm-obj" href="/?open=${c.listing_id}" target="_blank" rel="noopener">${photo ? `<img src="${esc(photo)}" alt="" loading="lazy">` : `<span class="cm-noph">${icn("home")}</span>`}
        <span><b>${esc(c.title || "Объект №" + c.listing_id)}</b><em class="cm-st ${oc}">${ot}</em>${c.n_same > 1 ? `<em class="cm-st warn">жалоб на объект: ${c.n_same}</em>` : ""}</span></a>`}
      <div class="cm-body">
        <p class="cm-meta"><span class="cm-reason">${esc(CM_REASON[c.reason] || c.reason || "Без причины")}</span><time>${when(c.ts)}</time>${who}</p>
        ${text ? `<p class="cm-text">${esc(text)}</p>` : `<p class="cm-text none">Без комментария.</p>`}
        ${ph.length ? `<div class="cmp-ph">${ph.map((u) => own.includes(u)
          ? `<span><img src="${esc(u)}" alt=""><button type="button" data-cph="${esc(u)}" data-lid="${c.listing_id}">Удалить фото</button></span>`
          : `<span class="gone"><img src="${esc(u)}" alt=""><small>уже удалено</small></span>`).join("")}</div>` : ""}
        <div class="cm-acts">${acts}</div></div></article>`;
  }
  async function complaints() {
    const { items, counts } = await call(`/api/admin/complaints?status=${cmShow}`);
    $("cmN").textContent = counts.new || "";
    $("cmD").textContent = counts.done || "";
    document.querySelectorAll("#cmTabs [data-cm]").forEach((b) => b.classList.toggle("on", b.dataset.cm === cmShow));
    $("complaints").innerHTML = items.length ? items.map((c) => complaintCard(c)).join("")
      : `<p class="ad-empty">${cmShow === "new" ? "Новых жалоб нет — всё разобрано." : "Пока пусто."}</p>`;
  }
  async function complaintAct(b) {
    const a = b.dataset.cmAct;
    const ask = { hide: "Снять объект с сайта? Повтор этого поста его не вернёт. Вернуть можно здесь же — «Вернуть на сайт».",
      delete: "Удалить объект навсегда? Тот же текст и объект больше не появятся. Вернуть можно в «Удалённые»." }[a];
    if (ask && !confirm(ask)) return null;
    return call("/api/admin/complaints", { id: Number(b.dataset.c), action: a });
  }

  // 02.11 (Артём: «у правок агентов — время, когда совершена; меню удобнее»): кто правил, поиск, по дням, дата и время
  let edWho = "agent", edPage = 1;
  const ED_NAMES = { price: "Цена", description: "Описание", status: "Статус", create: "Добавил объект", confirm: "Ответ на письмо",
    "photo+": "Добавил фото", "photo-": "Удалил фото", type: "Тип", deal: "Сделка", rooms: "Комнаты", area: "Площадь",
    land: "Участок", floor: "Этаж", floors: "Этажность", district: "Район", extra_districts: "Второй район", complex: "ЖК",
    street: "Улица", house: "Дом", point: "Точка на карте", geo: "Точка на карте", repair: "Ремонт", settlement: "Посёлок", layout: "Планировка" };
  const ED_REVERT = new Set(["price", "description", "type", "deal", "rooms", "area", "land", "floor", "floors",
    "district", "complex", "street", "house", "point", "extra_districts"]);
  const ED_HINT = { agent: "Что агенты меняли в своих объявлениях. Неверная правка — «Откатить» вернёт прежнее значение.",
    admin: "Ваши правки и правки других админов — из окна объекта и экрана разбора.",
    service: "Что сервис поменял сам: перепроверка районов и точек, чистка правил, проверка цены и разбора.", all: "Все правки подряд." };
  function edVal(v, field, deal) {
    let x = v;
    try { x = JSON.parse(v); } catch { /* строка */ }
    if (x == null || x === "") return "—";
    if (Array.isArray(x) && x.length >= 2 && typeof x[0] !== "string" && typeof x[1] !== "string")   // точка: [lat, lon, …]
      return x[0] == null ? "без точки" : `${(+x[0]).toFixed(5)}, ${(+x[1]).toFixed(5)}`;
    if (Array.isArray(x)) return x.join(", ") || "—";
    if (field === "price" && typeof x === "number") return money(x, deal);
    if (field === "area" && typeof x === "number") return `${x.toLocaleString("ru-RU")} м²`;
    return String(x);
  }
  async function edits() {
    const d = await call(`/api/admin/edits?who=${edWho}&q=${encodeURIComponent($("edQ").value.trim())}&page=${edPage}`);
    const c = d.counts || {};
    document.querySelectorAll("#edWho [data-ed]").forEach((b) => b.classList.toggle("on", b.dataset.ed === edWho));
    document.querySelectorAll("#edWho [data-edn]").forEach((b) => { b.textContent = c[b.dataset.edn] ? c[b.dataset.edn].toLocaleString("ru-RU") : ""; });
    $("edHint").innerHTML = `${ED_HINT[edWho]}${edWho === "agent" && c.agent_1d ? ` <b>За сутки: ${c.agent_1d}.</b>` : ""}`;
    let last = "";
    $("edits").innerHTML = d.items.length ? d.items.map((e) => {
      const dt = new Date(e.ts * 1000).toDateString();
      const head = dt !== last ? `<h4 class="ed-day">${esc(dayTitle(e.ts))}</h4>` : "";
      last = dt;
      const who = e.who_kind === "service" ? `<span class="ed-who svc">${icn("sparkle")}Сервис${e.src ? ` · ${esc(e.src)}` : ""}</span>`
        : `<span class="ed-who ${e.who_kind}">${icn(e.who_kind === "admin" ? "gear" : "user")}${esc(e.name || e.email || e.u_phone || "№" + e.user_id)}${e.who_kind === "admin" ? " · админ" : ""}</span>`;
      const long = e.field === "description";
      const o = edVal(e.old, e.field, e.deal), n = edVal(e.new, e.field, e.deal);
      return `${head}<article class="ed-row${e.reverted ? " off" : ""}">
        <time class="ed-time" title="${esc(new Date(e.ts * 1000).toLocaleString("ru-RU"))}">${hm(new Date(e.ts * 1000))}</time>
        <a class="ed-obj" href="/?open=${e.listing_id}" target="_blank" rel="noopener">${e.photo ? `<img src="${esc(e.photo)}" alt="" loading="lazy">` : `<span class="cm-noph">${icn("home")}</span>`}</a>
        <div class="ed-main">
          <p class="ed-l1"><b class="ed-f">${esc(ED_NAMES[e.field] || e.field)}</b><a href="/?open=${e.listing_id}" target="_blank" rel="noopener">${esc(e.title || "Объект №" + e.listing_id)}</a>${e.l_active === 0 ? `<em class="cm-st off">снят</em>` : ""}</p>
          ${["create", "confirm", "photo+", "photo-"].includes(e.field) ? "" : long
            ? `<details class="ed-long"><summary>Показать, что изменилось</summary><p><small>Было</small>${esc(o)}</p><p><small>Стало</small>${esc(n)}</p></details>`
            : `<p class="ed-diff"><s>${esc(o.slice(0, 140))}</s><i>→</i><b>${esc(n.slice(0, 140))}</b></p>`}
          ${who}</div>
        <div class="ed-act">${e.reverted ? `<em class="cm-st off">откачено</em>` : ED_REVERT.has(e.field) ? `<button type="button" class="cm-b" data-revert="${e.id}">${icn("history")}Откатить</button>` : ""}</div>
      </article>`;
    }).join("") : `<p class="ad-empty">Правок нет${$("edQ").value.trim() ? " по такому поиску" : ""}.</p>`;
    $("edPager").innerHTML = d.pages > 1 ? `<button class="chip" data-edpage="-1"${edPage <= 1 ? " disabled" : ""}>← Новее</button>
      <span>стр. ${edPage} из ${d.pages} · всего ${d.total.toLocaleString("ru-RU")}</span>
      <button class="chip" data-edpage="1"${edPage >= d.pages ? " disabled" : ""}>Старее →</button>` : d.total ? `<span>всего ${d.total}</span>` : "";
  }
  document.addEventListener("click", async (e) => {
    const w = e.target.closest("[data-ed]");
    if (w) { edWho = w.dataset.ed; edPage = 1; edits().catch(fail); return; }
    const p = e.target.closest("[data-edpage]");
    if (p) { edPage = Math.max(1, edPage + Number(p.dataset.edpage)); edits().catch(fail); $("edits").scrollIntoView({ block: "start" }); return; }
    const t = e.target.closest("#cmTabs [data-cm]");
    if (t) { cmShow = t.dataset.cm; complaints().catch(fail); return; }
    const a = e.target.closest("[data-cm-act]");
    if (a) {
      try { const r = await complaintAct(a); if (r) complaints().catch(fail); } catch (err) { fail(err); }
    }
  });

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
    const all = spItems.filter((x) => !q || x.title.toLowerCase().replace(/ё/g, "е").includes(q));
    const list = all.slice(0, 600);
    $("spList").innerHTML = list.map((x) => `<li><button type="button" class="sp-item${x.name === spOpen ? " on" : ""}${x.special ? " special" : ""}"
        data-sp-open="${esc(x.name)}"><span class="sp-name">${esc(x.title)}${x.custom ? ' <small>свой</small>' : ""}
        ${x.districts && x.districts.length ? `<small>${esc(x.districts.join(", "))}</small>` : ""}</span><b>${x.count}</b></button></li>`).join("")
      || `<li class="note sp-none">Ничего не нашлось</li>`;
    if (all.length > list.length) $("spList").insertAdjacentHTML("beforeend", `<li class="note sp-none">Показаны ${list.length} из ${all.length} — уточните поиск</li>`);
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
      spAsk(`Новое название района «${esc(name)}». Старое продолжит узнаваться в объявлениях.`, [], (to) => {
        if (!confirm(`Переименовать район «${name}» → «${to}»?
Название поменяется у всех его объектов, в фильтрах и подписках.`)) return;
        spDo({ action: "rename_district", name, to }, (r) => `«${name}» → «${r.name || to}» · объектов: ${r.listings}`);
      }, (v) => `Назвать «${v}»`);
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

  // ─── удалённые навсегда ─────────────────────────────────────────────────
  async function banned() {
    const d = await call("/api/admin/banned");
    const phones = (d.phones || []).length ? `<thead><tr><th>Номер (всё от него)</th><th>Удалён</th><th></th></tr></thead><tbody>${d.phones.map((p) =>
      `<tr><td><b>${esc(p.phone)}</b><br><span class="note">скрыто объектов: ${p.listings}</span></td><td>${day(p.ts)}</td>
        <td class="acts"><button data-unban-phone="${esc(p.phone)}">Вернуть</button></td></tr>`).join("")}</tbody>` : "";
    $("bannedPhones").innerHTML = phones;
    $("bannedTable").innerHTML = d.items.length ? `<thead><tr><th>Объект</th><th>Удалён</th><th></th></tr></thead><tbody>${d.items.map((o) =>
      `<tr><td><a href="/?open=${o.id}" target="_blank">${esc(o.title)}</a><br><span class="note">${esc([o.complex && "ЖК " + o.complex, o.district].filter(Boolean).join(" · "))}</span></td>
        <td>${day(o.ts)}</td><td class="acts"><button data-unban="${o.id}">Восстановить</button></td></tr>`).join("")}</tbody>`
      : `<tr><td class="note">Пока ничего не удалено. Удалить объект — в его окне: «⚙ Админ» → «🗑 Удалить навсегда».</td></tr>`;
  }
  document.addEventListener("click", async (e) => {   // 18.10: сброс очереди сообщений (после перерыва Wappi — ночной хвост)
    const b = e.target.closest("[data-queue]");
    if (!b) return;
    const all = b.dataset.queue === "all";
    if (!confirm(all ? "Не разбирать ВСЕ сообщения, которые ждут в очереди? Объявления из них на сайт не попадут."
      : "Не разбирать сообщения старше 6 часов? Свежие разберутся как обычно.")) return;
    b.disabled = true;
    try {
      const r = await call("/api/admin/queue", all ? { all: true } : { older_h: Number(b.dataset.queue) });
      alert(`Пропущено сообщений: ${r.skipped}. Осталось в очереди: ${r.left}.`);
      overview();
    } catch (err) { fail(err); b.disabled = false; }
  });
  document.addEventListener("click", async (e) => {   // 18.10: «Оплатил сегодня» — следующее напоминание через период
    const b = e.target.closest("[data-bill]");
    if (!b) return;
    b.disabled = true;
    try { await call("/api/admin/billing", { key: b.dataset.bill }); b.textContent = "Отмечено ✓"; overview(); } catch (err) { fail(err); b.disabled = false; }
  });
  document.addEventListener("click", async (e) => {
    const p = e.target.closest("[data-unban-phone]");
    if (p) { try { await call("/api/admin/phones/ban", { phone: p.dataset.unbanPhone, action: "unban" }); banned(); } catch (err) { fail(err); } return; }
    const b = e.target.closest("[data-unban]");
    if (!b) return;
    try { await call(`/api/admin/listings/${b.dataset.unban}/action`, { action: "restore" }); banned(); } catch (err) { fail(err); }
  });

  // ─── база номеров ───────────────────────────────────────────────────────
  const phMoney = (p, deal) => (!p ? "—" : deal === "rent" ? `${Math.round(p / 1000).toLocaleString("ru-RU")} тыс./мес`
    : `${(p / 1e6).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} млн`);
  async function phones() {
    const q = $("phoneQ").value.trim();
    const d = await call(`/api/admin/phones${q ? `?q=${encodeURIComponent(q)}` : ""}`);
    $("phonesTable").innerHTML = `<thead><tr><th>Номер</th><th>Объектов</th><th>На сайте</th><th>Последний</th></tr></thead><tbody>${d.items.map((p) =>
      `<tr class="ph-row${p.banned ? " banned" : ""}" data-phone="${esc(p.phone)}"><td><b>${esc(p.phone)}</b>${p.banned ? ` <span class="note">удалён</span>` : ""}</td>
        <td>${p.n}</td><td>${p.active || 0}</td><td>${day(p.last_seen)}</td></tr>`).join("")}</tbody>`;
  }
  async function phoneDetail(phone) {
    document.querySelectorAll(".ph-row").forEach((r) => r.classList.toggle("on", r.dataset.phone === phone));
    const d = await call(`/api/admin/phones?phone=${encodeURIComponent(phone)}`);
    $("phoneDetail").innerHTML = `<h3>${esc(d.phone)}</h3>
      <p class="note">Объектов: ${d.listings.length} · запросов: ${d.requests.length} · сообщений: ${d.messages}</p>
      <div class="ph-acts">${d.banned || d.wiped ? `<button data-ph-unban="${esc(d.phone)}">Вернуть номер</button>`
        : `<button class="danger" data-ph-wipe="${esc(d.phone)}">Удалить объекты</button>
           <button class="danger" data-ph-ban="${esc(d.phone)}">Удалить и не принимать новые</button>`}
        <button type="button" class="chip" data-max="${esc(d.phone)}">MAX</button></div>
      <ul class="ph-list">${d.listings.map((o) => `<li class="${o.is_active ? "" : "off"}">
        <div><a href="/?open=${o.id}" target="_blank">${esc(o.title)}</a><span class="note">${esc(phMoney(o.price, o.deal))} ·
          ${esc([o.complex && "ЖК " + o.complex, o.district].filter(Boolean).join(" · ") || "без места")}${o.is_active ? "" : " · скрыт"}</span></div>
        <div class="ph-btns"><a href="/fix?mode=place&id=${o.id}" target="_blank">Разобрать</a>
          ${o.is_active ? `<button class="danger" data-ph-del="${o.id}">Удалить</button>` : `<button data-ph-restore="${o.id}">Вернуть</button>`}</div></li>`).join("")}</ul>`;
    $("phoneDetail").dataset.phone = d.phone;
  }
  $("phoneSearch").addEventListener("submit", (e) => { e.preventDefault(); phones(); });
  // 01.11: «Удалённые номера» — кто удалён, сколько ещё в сроке, «Вернуть»
  async function phonesDeleted() {
    const t = $("phonesDeleted");
    let d;
    try { d = await call("/api/admin/phones?deleted=1"); } catch (err) { t.innerHTML = `<tr><td>${esc(err.message)}</td></tr>`; return; }
    const days = d.stale_days || 15;
    t.innerHTML = d.items.length ? `<tr><th>Номер</th><th>Как удалён</th><th>Когда</th><th>Объектов</th>
        <th>Ещё в сроке (${days} дн.)</th><th>Сообщений</th><th></th></tr>` + d.items.map((r) => `<tr>
        <td><a href="#" data-ph-open="${esc(r.phone)}">${esc(r.phone)}</a></td>
        <td>${r.blocked ? "удалён и не принимаем" : "удалены объекты"}</td>
        <td>${new Date(r.ts * 1000).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</td>
        <td>${r.listings}</td><td>${r.fresh}</td>
        <td>${r.blocked ? `${r.messages}${r.messages_fresh ? ` (свежих ${r.messages_fresh})` : ""}` : "—"}</td>
        <td><button class="pill light" data-ph-unban="${esc(r.phone)}">Вернуть</button></td></tr>`).join("")
      : `<tr><td class="note">Удалённых номеров нет.</td></tr>`;
  }
  $("phonesDeleted").addEventListener("click", (e) => {
    const a = e.target.closest("[data-ph-open]");
    if (a) { e.preventDefault(); phoneDetail(a.dataset.phOpen); }
  });
  // 01.11: «Удалить всё от номера» — номер вписать целиком, два действия
  $("phoneKill").addEventListener("submit", async (e) => {
    e.preventDefault();
    const phone = $("phoneKillQ").value.trim();
    const ban = (e.submitter && e.submitter.dataset.kill) === "ban";
    if (!phone || !confirm(ban ? `Удалить всё от номера ${phone} и не принимать новые объявления от него?`
      : `Удалить с сайта все объекты от номера ${phone}? Новые объявления будут приниматься.`)) return;
    try {
      const r = await call("/api/admin/phones/ban", { phone, action: ban ? "ban" : "delete" });
      alert(`Готово: ${r.phone} — объектов убрано: ${r.listings}${ban ? `, запросов: ${r.requests}, сообщений отложено: ${r.messages}` : ""}.`);
      $("phoneKillQ").value = "";
      phones(); phonesDeleted(); phoneDetail(r.phone);
    } catch (err) { fail(err); }
  });
  document.addEventListener("click", async (e) => {
    const row = e.target.closest(".ph-row");
    if (row) { phoneDetail(row.dataset.phone); return; }
    const b = e.target.closest("[data-ph-ban], [data-ph-wipe], [data-ph-unban], [data-ph-del], [data-ph-restore]");
    if (!b) return;
    const cur = $("phoneDetail").dataset.phone;
    try {
      if (b.dataset.phBan) { if (!confirm(`Удалить всё от номера ${b.dataset.phBan} и не принимать новые объявления от него?`)) return; await call("/api/admin/phones/ban", { phone: b.dataset.phBan }); }
      else if (b.dataset.phWipe) { if (!confirm(`Удалить с сайта все объекты от номера ${b.dataset.phWipe}? Новые объявления будут приниматься.`)) return; await call("/api/admin/phones/ban", { phone: b.dataset.phWipe, action: "delete" }); }
      else if (b.dataset.phUnban) {
        if (!confirm(`Вернуть номер ${b.dataset.phUnban}? Объекты в сроке появятся на сайте, свежие сообщения разберутся заново.`)) return;
        const r = await call("/api/admin/phones/ban", { phone: b.dataset.phUnban, action: "unban" });
        alert(`Номер ${r.phone} возвращён: на сайте — ${r.active}, в архив — ${r.archive}, сообщений в разбор — ${r.messages}.`);
        phones(); phonesDeleted(); phoneDetail(r.phone); return;
      }
      else if (b.dataset.phDel) { if (!confirm("Удалить объект навсегда?")) return; await call(`/api/admin/listings/${b.dataset.phDel}/action`, { action: "delete" }); }
      else if (b.dataset.phRestore) await call(`/api/admin/listings/${b.dataset.phRestore}/action`, { action: "restore" });
      phoneDetail(cur); phones(); phonesDeleted();
    } catch (err) { fail(err); }
  });

  const loaders = { overview, users, promos, optouts, complaints, edits, chats, districts, banned, phones: () => Promise.all([phones(), phonesDeleted()]) };

  // 24.10g: разделы — слева списком с иконками (на телефоне — полосой)
  const TAB_IC = { overview: "dashboard", users: "users", promos: "ticket", optouts: "ban", complaints: "alert", edits: "edit", chats: "chat",
    districts: "map", phones: "phone", banned: "trash" };
  document.querySelectorAll(".adm-tabs [data-tab]").forEach((b) => { if (window.ic && TAB_IC[b.dataset.tab]) b.insertAdjacentHTML("afterbegin", window.ic(TAB_IC[b.dataset.tab])); });
  const lrn = document.querySelector(".adm-tabs .seg-link");
  if (lrn && window.ic) lrn.insertAdjacentHTML("afterbegin", window.ic("layers"));
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
        if (b.dataset.a === "reset" && !confirm("Сбросить доступ? Номера агентов и инструменты закроются до оплаты.")) return;
        if (b.dataset.a === "admin" && !confirm("Сделать администратором? Он увидит админку и сможет всё править.")) return;
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
      } else if (b.dataset.cph) {   // удалить фото из жалобы
        if (!confirm("Удалить это фото у объекта?")) return;
        await call(`/api/admin/listings/${b.dataset.lid}/action`, { action: "photo", url: b.dataset.cph });
        complaints();
      }
    } catch (err) { fail(err); }
  });

  $("userSearch").addEventListener("submit", (e) => { e.preventDefault(); userPage = 1; users().catch(fail); });
  $("edSearch").addEventListener("submit", (e) => { e.preventDefault(); edPage = 1; edits().catch(fail); });
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

  function adminLogin() {
    let step = "email";
    const err = (t) => { $("admLoginErr").textContent = t; $("admLoginErr").hidden = !t; };
    $("admLoginForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      err("");
      const email = $("admEmail").value.trim();
      $("admLoginBtn").disabled = true;
      try {
        if (step === "email") {
          await call("/api/auth/email/start", { email, consent: true, terms: true });   // администратор — оператор сервиса
          step = "code";
          $("admCode").hidden = false; $("admCode").focus();
          $("admLoginNote").textContent = `Код отправлен на ${email}. Нет письма — проверьте «Спам».`;
          $("admLoginBtn").textContent = "Войти";
        } else {
          const r = await call("/api/auth/email/verify", { email, code: $("admCode").value.trim(), consent: true, terms: true });
          if (!r.me || !r.me.is_admin) { err("Вход выполнен, но у этой почты нет прав администратора."); return; }
          location.reload();
        }
      } catch (ex) { err(ex.message); } finally { $("admLoginBtn").disabled = false; }
    });
  }

  (async () => {
    try {
      await call("/api/admin/overview");
      $("app").hidden = false; $("admHead").hidden = false;
      const tab = new URLSearchParams(location.search).get("tab");   // /admin?tab=districts — сразу нужная вкладка
      if (tab === "geo" || tab === "place") { location.replace("/learn"); return; }   // старые вкладки — теперь «Обучение и разбор»
      show(tab && loaders[tab] ? tab : "overview");
    } catch {
      $("denied").hidden = false;   // отдельный вход администратора — прямо здесь (почта → код)
      adminLogin();
    }
  })();
  // «Проверить почту»: тестовое письмо и ответ почтового сервера
  document.addEventListener("submit", async (e) => {
    if (e.target.id !== "mailTest") return;
    e.preventDefault();
    $("mailRes").textContent = "Отправляем…";
    try {
      const r = await call("/api/admin/mailtest", { to: $("mailTo").value });
      $("mailRes").textContent = r.ok
        ? `Отправлено от ${r.sender} через ${r.host}. Нет письма за 2 минуты — посмотрите «Спам» и «Промоакции»; если и там нет — получатель отклоняет письма с этого ящика (нужна почта на своём домене).`
        : /SPAM|5\.7\.1/i.test(r.error || "")
          ? `Почтовый сервер не выпустил письмо, решил, что это спам: ${r.error}. Что сделать: 1) в Яндекс ID этого ящика `
            + `привязать телефон и войти в почту через браузер; 2) подождать сутки — новым ящикам Яндекс ограничивает письма от программ; `
            + `3) надёжнее — почта на своём домене (Яндекс 360 для 1plus1assistant.ru с записями SPF и DKIM в reg.ru) или сервис рассылки `
            + `(Unisender Go, SendPulse) — тогда в .env меняются только SMTP_HOST / SMTP_USER / SMTP_PASSWORD / SMTP_FROM.`
          : `Не отправилось: ${r.error || "ошибка"}`;
    } catch (err) { $("mailRes").textContent = err.message; }
  });
})();
