/* 1+1 · меню слева на всех страницах: на компьютере — стеклянный «рельс» со ВСЕМИ разделами и подсказкой
   (название + зачем) при наведении; на телефоне — нижняя панель из главных разделов + «Все разделы» шторкой
   (на главной её роль играет своя .tabbar).
   Не показывается только на странице подборки для клиента. */
(() => {
  "use strict";
  const path = location.pathname;
  // Приложение на экран телефона: помощник (sw.js) + запомнить предложение браузера «установить»
  if ("serviceWorker" in navigator && !path.startsWith("/c/") && location.protocol === "https:") {
    navigator.serviceWorker.register("/sw.js").catch(() => { /* без помощника сайт работает как обычно */ });
  }
  // «я на сайте» — раз в минуту, пока страница открыта и видна (для админки: кто сейчас на сайте)
  // метка браузера для статистики — только с согласием на необязательные cookie (cookies.js: ck=all); без согласия — анонимно
  const statsOk = () => /(?:^|;\s*)ck=all/.test(document.cookie);
  if (!path.startsWith("/c/") && !new URLSearchParams(location.search).has("embed")) {
    const gid = () => {   // метка браузера для статистики — заводится только после согласия
      let g = "";
      try { g = localStorage.getItem("pg") || ""; if (!g) { g = Math.random().toString(36).slice(2) + Date.now().toString(36); localStorage.setItem("pg", g); } } catch { /* */ }
      return g;
    };
    let lastPing = 0;   // не чаще раза в 30 с (вкладку часто прячут/показывают — без этого были пачки запросов)
    // 27.10: сигнал — от всех (гостей без согласия сервер считает анонимной меткой дня); метка браузера — только с согласием
    let first = 1;   // 27.10: первый сигнал страницы = просмотр (load); дальше раз в минуту — только «я ещё тут»
    const ping = () => { if (Date.now() - lastPing < 30000) return; if (!document.hidden) { lastPing = Date.now(); fetch("/api/ping", { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path, g: statsOk() ? gid() : "", load: first }), keepalive: true }).catch(() => {}); first = 0; } };
    setTimeout(ping, 1500);
    setInterval(ping, 60000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) ping(); });
  }
  window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); window.__installPrompt = e;
    document.dispatchEvent(new Event("oneplus:installable")); });
  if (path.startsWith("/c/") || new URLSearchParams(location.search).has("embed")) return;
  const ic = (n) => (window.ic ? window.ic(n) : "");
  const params = new URLSearchParams(location.search);
  // все разделы сайта: название + коротко, зачем (подсказка при наведении; на телефоне — меню «Ещё»)
  const ITEMS = [
    { href: "/", icon: "search", tip: "Поиск объектов", short: "Объекты", desc: "Все объекты из чатов, карта, районы", main: true,
      on: () => path === "/" && !params.has("fav") && !params.has("agent") },
    { href: "/cabinet", icon: "dashboard", tip: "Кабинет", desc: "Дела, ставки, курсы, новости", user: true },
    { href: "/my", icon: "home-edit", tip: "Мои объекты", desc: "Свои объявления и их статистика", user: true, also: ["/my/edit"] },
    { href: "/clients", icon: "users", tip: "Клиенты", desc: "Этапы, подборки, что кому показали", main: true, user: true },
    // 24.10d (Артём): на телефоне — 6-й пункт нижней панели, сразу после «Клиентов»
    { href: "/picks", icon: "stack", tip: "Подборки", desc: "Ссылка для клиента с вашим контактом", main: true, user: true },
    { href: "/requests", icon: "briefcase", tip: "Запросы покупателей", short: "Запросы", desc: "Кто что ищет и номер агента", main: true, user: true },
    { href: "/work", icon: "heart", tip: "Избранное", desc: "Сохранённые объекты и клиенты", main: true, user: true,
      count: (c) => c.work, countTip: (n) => `в избранном: ${n}` },
    { href: "/planner", icon: "calendar", tip: "Планер", desc: "Показы, звонки, встречи", user: true,
      count: (c) => c.plan_today + c.plan_overdue, countTip: (n) => `дел на сегодня и просроченных: ${n}`, hot: (c) => c.plan_overdue > 0 },
    { href: "/compare", icon: "scale", tip: "Сравнение", desc: "До 10 объектов рядом" },
    { href: "/market", icon: "chart", tip: "Аналитика рынка", short: "Аналитика", desc: "Цена метра по районам и ЖК" },
    { href: "/notifications", icon: "bell", tip: "Уведомления", desc: "Новые объекты и снижение цен", user: true, bell: true },
    { sep: true },
    { href: "/pay", icon: "card", tip: "Подписка", desc: "Тарифы и доступ к номерам" },
    { href: "/settings", icon: "gear", tip: "Настройки", desc: "Профиль, подборки, вход, почта", user: true },
    { href: "/how", icon: "help", tip: "Как это работает", desc: "Инструкция и ответы на вопросы" },
    // 24.10k (Артём): вопросы и ошибки — на номер поддержки (звонок и MAX)
    { href: "/contacts#support", icon: "chat", tip: "Поддержка", desc: "Вопросы и ошибки — +7 938 422-28-30" },
    { sep: true, admin: true },
    { href: "/learn", icon: "layers", tip: "Обучение и разбор", desc: "Очереди разбора и правила", admin: true, also: ["/fix", "/admin/map"] },
    { href: "/admin", icon: "sliders", tip: "Админка", desc: "Пользователи, чаты, статистика", admin: true },
  ];
  const isOn = (it) => (it.on ? it.on() : path === it.href || (it.also || []).includes(path) || (it.href !== "/" && path.startsWith(it.href + "/")));
  const tipHTML = (t, d) => `<span class="rail-tip" role="tooltip"><b>${t}</b>${d ? `<small>${d}</small>` : ""}</span>`;

  // 18.10: на невысоком экране (у админа пунктов больше) профиль внизу «вылезал» из меню — пункты становятся компактнее
  // ступенями (rail-t1…t3), пока всё не поместится; на телефоне меню — нижняя панель, там не нужно
  function fitRail(rail) {
    const fit = () => {
      rail.classList.remove("rail-t1", "rail-t2", "rail-t3");
      if (innerWidth <= 820) return;
      for (const c of ["rail-t1", "rail-t2", "rail-t3"]) {
        if (rail.scrollHeight <= rail.clientHeight + 1) return;
        rail.classList.add(c);
      }
    };
    fit();
    let t = 0;
    addEventListener("resize", () => { clearTimeout(t); t = setTimeout(fit, 120); });
  }
  function build(me) {
    const rail = document.createElement("nav");
    rail.className = "rail rail-g";
    // 18.10: меню «въезжает» только при первом заходе за сессию — при переходах между разделами оно стоит на месте
    try { if (!sessionStorage.getItem("railSeen")) { rail.classList.add("rail-first"); sessionStorage.setItem("railSeen", "1"); } }
    catch { rail.classList.add("rail-first"); }
    rail.setAttribute("aria-label", "Разделы");
    const items = ITEMS.filter((it) => (!it.user || me) && (!it.admin || (me && me.is_admin)));
    const counts = (me && me.counts) || {};
    const badge = (it) => {   // кружок с числом — как у уведомлений: что ждёт внимания
      const n = it.count ? it.count(counts) || 0 : 0;
      if (!n) return "";
      const hot = typeof it.hot === "function" ? it.hot(counts) : it.hot;
      return `<b class="rail-c${hot ? " hot" : ""}" title="${it.countTip ? it.countTip(n) : n}">${n > 99 ? "99+" : n}</b>`;
    };
    const who = me ? (me.name || me.email || "").replace(/[<>"]/g, "") : "";
    rail.innerHTML = `<a class="rail-logo" href="/" aria-label="1+1 — к поиску"><img src="/static/favicon.svg" alt="" width="38" height="38"><span class="rail-word"><b>1+1</b><small>поиск объектов Краснодара</small></span></a>`
      // компьютер — все разделы слева; телефон — Объекты, Клиенты, Запросы, Избранное и «Ещё» (остальное, сверху — Кабинет)
      + items.map((it) => it.sep ? `<span class="rail-sep"></span>`
        : `<a class="rail-a${isOn(it) ? " on" : ""}${it.main ? " main" : ""}" href="${it.href}" aria-label="${it.tip}">${ic(it.icon)}<span class="rail-lbl">${it.short || it.tip}</span>${it.bell ? `<b class="rail-n" hidden></b>` : badge(it)}${tipHTML(it.tip, it.desc)}</a>`).join("")
      + `<span class="rail-gap"></span>`
      + `<button type="button" class="rail-a main rail-more${items.some((it) => !it.main && !it.sep && isOn(it)) ? " on" : ""}" aria-label="Все разделы" aria-expanded="false">${ic("more")}<span class="rail-lbl">Ещё</span><b class="rail-n" hidden></b>${tipHTML("Ещё", "Кабинет, подборки, мои объекты, уведомления, настройки")}</button>`
      + (me ? `<a class="rail-ava${me.photo ? " has-photo" : ""}" href="/cabinet" aria-label="Профиль"${me.photo ? ` style="background-image:url('${encodeURI(String(me.photo)).replace(/['"()]/g, "")}')"` : ""}>${me.photo ? "" : ((me.name || me.email || "1").trim()[0] || "1").toUpperCase()}${tipHTML("Профиль и кабинет", who)}</a>`
        : `<a class="rail-a main" href="/?login=1" aria-label="Войти">${ic("user")}<span class="rail-lbl">Войти</span>${tipHTML("Войти", "14 дней бесплатно — без карты")}</a>`);
    document.body.prepend(rail);
    fitRail(rail);
    // 24.10: компьютер — навели на меню (0,12 с) → раскрывается целиком: все разделы с описанием; ушла мышь — сворачивается
    const wide = matchMedia("(hover: hover) and (min-width: 821px)");
    let openT = 0;
    rail.addEventListener("mouseenter", () => { if (!wide.matches) return; clearTimeout(openT); openT = setTimeout(() => rail.classList.add("rail-open"), 120); });
    rail.addEventListener("mouseleave", () => { clearTimeout(openT); rail.classList.remove("rail-open"); });
    document.body.classList.add("has-rail");
    if (document.querySelector(".tabbar")) document.body.classList.add("has-tabbar");
    if (window.__bellN) window.railBell(window.__bellN);

    // телефон: «Все разделы» — шторка со списком всех страниц и пояснениями
    const sheet = document.createElement("div");
    sheet.className = "rail-sheet";
    sheet.setAttribute("popover", "manual");   // сами закрываем: тап мимо не должен нажимать то, что под шторкой
    sheet.innerHTML = `<div class="rs-in"><span class="rs-grab" data-rs-close></span>
      <div class="rs-top"><b class="rs-h">Все разделы</b><button type="button" class="rs-x" data-rs-close aria-label="Закрыть">✕</button></div>`
      + (me ? "" : `<a class="rs-a" href="/?login=1">${ic("user")}<span><b>Войти</b><small>14 дней бесплатно — без карты</small></span></a>`)
      + items.filter((it) => !it.sep).map((it) => `<a class="rs-a${isOn(it) ? " on" : ""}" href="${it.href}"${it.main ? " data-main" : ""}>${ic(it.icon)}<span><b>${it.tip}</b><small>${it.desc || ""}</small></span>${badge(it)}</a>`).join("")
      + `</div>`;
    document.body.appendChild(sheet);
    const more = rail.querySelector(".rail-more");
    // 17.10: шторка на весь экран со своим затемнением: тап мимо попадает в неё и только закрывает (плавно);
    // крестик, полоса сверху, выбор пункта, свайп вниз и Esc — тоже закрывают
    const inner = sheet.querySelector(".rs-in");
    const isOpen = () => { try { return sheet.matches(":popover-open"); } catch { return sheet.classList.contains("open"); } };
    const hide = () => { sheet.classList.remove("closing", "dragging"); inner.style.transform = "";
      try { sheet.hidePopover(); } catch { sheet.classList.remove("open"); } more.setAttribute("aria-expanded", "false"); };
    const close = () => {
      if (!isOpen() || sheet.classList.contains("closing")) return;
      if (innerWidth > 820 || matchMedia("(prefers-reduced-motion: reduce)").matches) { hide(); return; }
      sheet.classList.add("closing");
      setTimeout(hide, 260);
    };
    const open = () => {
      // 24.10d (Артём): в «Ещё» — ВСЕ разделы, и те, что есть в нижней панели (одно место, где видно всё)
      try { sheet.showPopover(); } catch { sheet.classList.add("open"); }
      more.setAttribute("aria-expanded", "true");
    };
    more.addEventListener("click", () => { if (isOpen()) close(); else open(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && isOpen()) close(); });
    sheet.addEventListener("click", (e) => {
      if (e.target === sheet || e.target.closest("[data-rs-close]")) { e.preventDefault(); close(); return; }
      if (e.target.closest("a")) setTimeout(hide, 0);   // переход — шторку убираем без задержки
    });
    let y0 = null, dy = 0;   // свайп вниз по шторке — тянется за пальцем, отпустили ниже 90 px — закрыть
    sheet.addEventListener("touchstart", (e) => { y0 = inner.scrollTop <= 0 ? e.touches[0].clientY : null; dy = 0; }, { passive: true });
    sheet.addEventListener("touchmove", (e) => {
      if (y0 === null) return;
      dy = Math.max(0, e.touches[0].clientY - y0);
      if (dy > 6) { sheet.classList.add("dragging"); inner.style.transform = `translateY(${dy}px)`; }
    }, { passive: true });
    sheet.addEventListener("touchend", () => {
      if (y0 === null) return;
      y0 = null;
      sheet.classList.remove("dragging");
      if (dy > 90) close(); else inner.style.transform = "";
    });
  }

  async function init() {
    let me = null;
    try {
      const r = await fetch("/api/me", { credentials: "same-origin" });
      if (r.ok) me = (await r.json()).me;
    } catch { /* без входа — гостевое меню */ }
    build(me);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
  // число непрочитанных — от bell.js
  window.railBell = (n) => document.querySelectorAll(".rail-n").forEach((b) => { b.hidden = !n; b.textContent = n > 99 ? "99+" : n; });
})();
