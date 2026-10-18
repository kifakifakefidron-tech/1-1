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
    const card = (n, label) => `<div class="adm-card"><b>${esc(n)}</b><span>${esc(label)}</span></div>`;
    $("overview").innerHTML = [
      card(o.listings, "объектов на сайте"), card(o.listings_feed, "из них — СТРЕЛЫ (фид)"),
      card(o.listings_no_district, "без района (не находятся фильтром)"), card(o.listings_no_map, "без точки на карте"),
      card(o.listings_no_place, "скрыты: нет адреса, ЖК и района"),
      ...(o.repair ? [`<a class="adm-card" href="/?rep=yes" target="_blank"><b>${esc(o.repair.yes)}</b><span>с ремонтом (квартиры и дома) — проверить</span></a>`,
        `<a class="adm-card" href="/?rep=no" target="_blank"><b>${esc(o.repair.no)}</b><span>без ремонта: ПЧО, черновая, под ремонт — проверить</span></a>`,
        card(o.repair.none, "состояние в тексте не указано")] : []),
      `<div class="adm-card"><b>${esc(o.queue)}</b><span>сообщений ждут разбора</span>${o.queue ? `<span class="adm-q-btns">
        <button type="button" class="pill light" data-queue="6">Пропустить старше 6 ч</button>
        <button type="button" class="pill light" data-queue="all">Сбросить всю очередь</button></span>` : ""}</div>`,
      card(o.users, "пользователей"),
      card(o.users_access, "с открытым доступом"), card(o.users_paid, "оплатили подписку"),
      card(o.views_24h, "открытий номеров за сутки"), card(o.complaints_new, "новых жалоб"),
      card(o.optouts, "скрытых номеров"),
      ...(o.p0_report ? [card(o.p0_report.phones, "номеров вырезано из описаний"),
        card(o.p0_report.prices, "цена в карточке ≠ цене в тексте")] : []),
      ...(o.cx_report ? [card(o.cx_report.listings, `объектов: одно название ЖК (групп ${o.cx_report.groups})`)] : []),
      ...(o.geo_daily ? [card((o.geo_daily.rules || 0), "ошибочных правил отключено сегодня (уборка раз в сутки)")] : []),
      ...(o.geo_cleanup ? [card((o.geo_cleanup.rules || 0) + (o.geo_cleanup.aliases || 0), "плохих правил отключено (уборка)"),
        card(o.geo_cleanup.junk || 0, "мусорных «ЖК» убрано"), card(o.geo_cleanup.listings || 0, "объектов поправлено уборкой")] : []),
      ...(o.price_report ? [card(o.price_report.scale, "цен «в тысячах» исправлено"),
        card(o.price_report.text, "цен вернули к цене из текста"), card(o.price_report.doubt, "цен «под вопросом»")] : []),
      card(o.wappi_enabled ? "включён" : "на паузе", "сбор из чатов (Wappi)"),
      card(o.payments ? "включена" : "выключена", `оплата (${o.price} ₽ / ${o.period_days} дн.)`),
      card(day(o.feed_synced), "фид СТРЕЛ обновлён"),
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
      $("overview").insertAdjacentHTML("beforeend", `<p class="note">Нейросеть DeepSeek: <b class="${o.llm_on ? "" : "bad"}">${o.llm_on ? "включена — новые сообщения разбирает она + правила" : "ВЫКЛЮЧЕНА — разбор только правилами (нет DEEPSEEK_API_KEY в .env)"}</b>${o.rules_fix ? ` · исправлено по новым правилам: ${Object.entries(o.rules_fix).map(([k, v]) => `${esc(k)} — ${v}`).join(", ")}` : ""}${o.media_strict && o.media_strict.checked ? ` · фото из чатов проверены (только подпись к фото): оставлено <b>${o.media_strict.kept}</b>, удалено <b>${o.media_strict.removed}</b> у ${o.media_strict.listings} объектов` : ""}${o.rent_leak && o.rent_leak.moved ? ` · из аренды в продажу (продажа рядом с «сдам +КУ» в одном сообщении): <b>${o.rent_leak.moved}</b> — ${o.rent_leak.examples.slice(0, 5).map(e => `<a href="/?open=${e.id}" target="_blank">${esc(e.title || "#" + e.id)}</a>`).join(", ")}` : ""}</p>`);
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
      ${o.pages.length ? `<p class="note">Где сейчас: ${o.pages.map((x) => `${esc(PAGE_NAMES[x.path] || x.path)} — ${x.n}`).join(", ")}</p>` : ""}`;
    clearTimeout(online.t);
    online.t = setTimeout(() => { if (!document.hidden && $("onlineBox")) online(); }, 30000);
  }

  // ─── подробная статистика ───────────────────────────────────────────────
  const n0 = (x) => (x == null ? "—" : Number(x).toLocaleString("ru-RU"));
  const money = (p, deal) => (p == null ? "—" : deal === "rent" ? `${n0(Math.round(p / 1000))} тыс. ₽/мес`
    : p >= 1e6 ? `${(p / 1e6).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} млн ₽` : `${n0(p)} ₽`);
  const ago = (ts) => {
    if (!ts) return "—";
    const m = Math.round((now() - ts) / 60);
    return m < 1 ? "только что" : m < 60 ? `${m} мин назад` : m < 1440 ? `${Math.round(m / 60)} ч назад` : day(ts);
  };
  const box = (title, html, wide) => `<section class="st-box${wide ? " wide" : ""}"><h3>${title}</h3>${html}</section>`;
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
    const reason = { sold: "продан/неактуален", wrong: "неверные данные", my_phone: "это мой номер", photo: "чужие фото", other: "другое" };
    $("complaints").innerHTML = `<tr><th>Когда</th><th>Объект</th><th>Причина</th><th>Текст</th><th>Статус</th><th></th></tr>` +
      items.map((c) => `<tr><td>${day(c.ts)}</td>
        <td>${c.listing_id ? `<a href="/?open=${c.listing_id}" target="_blank">${esc(c.title || "#" + c.listing_id)}</a>` : "—"}</td>
        <td>${esc(reason[c.reason] || c.reason)}</td><td>${esc((c.text || "").replace(/^Фото: .*$/gm, "").trim())}
          ${(() => { const own = (() => { try { return JSON.parse(c.photos || "[]"); } catch { return []; } })();
            const ph = [...(c.text || "").matchAll(/^Фото: (\S+)$/gm)].map((m) => m[1]);
            return ph.length ? `<div class="cmp-ph">${ph.map((u) => own.includes(u)
              ? `<span><img src="${esc(u)}" alt=""><button data-cph="${esc(u)}" data-lid="${c.listing_id}">Удалить фото</button></span>`
              : `<span class="gone"><img src="${esc(u)}" alt=""><small>уже удалено</small></span>`).join("")}</div>` : ""; })()}</td>
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
      items.map((e) => `<tr><td>${day(e.ts)}</td><td>${e.user_id === 0 ? "Сервис (перепроверка)" : esc(e.email || e.name || "#" + e.user_id)}</td>
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
      <div class="ph-acts">${d.banned ? `<button data-ph-unban="${esc(d.phone)}">Вернуть номер</button>`
        : `<button class="danger" data-ph-ban="${esc(d.phone)}">Удалить всё от номера</button>`}
        <button type="button" class="chip" data-max="${esc(d.phone)}">MAX</button></div>
      <ul class="ph-list">${d.listings.map((o) => `<li class="${o.is_active ? "" : "off"}">
        <div><a href="/?open=${o.id}" target="_blank">${esc(o.title)}</a><span class="note">${esc(phMoney(o.price, o.deal))} ·
          ${esc([o.complex && "ЖК " + o.complex, o.district].filter(Boolean).join(" · ") || "без места")}${o.is_active ? "" : " · скрыт"}</span></div>
        <div class="ph-btns"><a href="/fix?mode=place&id=${o.id}" target="_blank">Разобрать</a>
          ${o.is_active ? `<button class="danger" data-ph-del="${o.id}">Удалить</button>` : `<button data-ph-restore="${o.id}">Вернуть</button>`}</div></li>`).join("")}</ul>`;
    $("phoneDetail").dataset.phone = d.phone;
  }
  $("phoneSearch").addEventListener("submit", (e) => { e.preventDefault(); phones(); });
  document.addEventListener("click", async (e) => {
    const row = e.target.closest(".ph-row");
    if (row) { phoneDetail(row.dataset.phone); return; }
    const b = e.target.closest("[data-ph-ban], [data-ph-unban], [data-ph-del], [data-ph-restore]");
    if (!b) return;
    const cur = $("phoneDetail").dataset.phone;
    try {
      if (b.dataset.phBan) { if (!confirm(`Удалить всё от номера ${b.dataset.phBan}?`)) return; await call("/api/admin/phones/ban", { phone: b.dataset.phBan }); }
      else if (b.dataset.phUnban) await call("/api/admin/phones/ban", { phone: b.dataset.phUnban, action: "unban" });
      else if (b.dataset.phDel) { if (!confirm("Удалить объект навсегда?")) return; await call(`/api/admin/listings/${b.dataset.phDel}/action`, { action: "delete" }); }
      else if (b.dataset.phRestore) await call(`/api/admin/listings/${b.dataset.phRestore}/action`, { action: "restore" });
      phoneDetail(cur); phones();
    } catch (err) { fail(err); }
  });

  const loaders = { overview, users, promos, optouts, complaints, edits, chats, districts, banned, phones };

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
          await call("/api/auth/email/start", { email, consent: true });   // администратор — оператор сервиса
          step = "code";
          $("admCode").hidden = false; $("admCode").focus();
          $("admLoginNote").textContent = `Код отправлен на ${email}. Нет письма — проверьте «Спам».`;
          $("admLoginBtn").textContent = "Войти";
        } else {
          const r = await call("/api/auth/email/verify", { email, code: $("admCode").value.trim(), consent: true });
          if (!r.me || !r.me.is_admin) { err("Вход выполнен, но у этой почты нет прав администратора."); return; }
          location.reload();
        }
      } catch (ex) { err(ex.message); } finally { $("admLoginBtn").disabled = false; }
    });
  }

  (async () => {
    try {
      await call("/api/admin/overview");
      $("app").hidden = false;
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
