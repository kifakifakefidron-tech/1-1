/* 1+1 · меню слева на всех страницах: на компьютере — стеклянный «рельс» со ВСЕМИ разделами и подсказкой
   (название + зачем) при наведении; на телефоне — нижняя панель из главных разделов + «Все разделы» шторкой
   (на главной её роль играет своя .tabbar).
   Не показывается на странице оплаты и на странице подборки для клиента. */
(() => {
  "use strict";
  const path = location.pathname;
  if (path === "/pay" || path.startsWith("/c/") || new URLSearchParams(location.search).has("embed")) return;
  const ic = (n) => (window.ic ? window.ic(n) : "");
  const params = new URLSearchParams(location.search);
  // все разделы сайта: название + коротко, зачем (подсказка при наведении; на телефоне — меню «Ещё»)
  const ITEMS = [
    { href: "/", icon: "search", tip: "Поиск объектов", desc: "Все объекты из чатов: фильтры, карта, районы", main: true,
      on: () => path === "/" && !params.has("fav") && !params.has("agent") },
    { href: "/cabinet", icon: "grid", tip: "Кабинет", desc: "Ваш день: задачи, ставки, погода, сводка", main: true, user: true },
    { href: "/my", icon: "home", tip: "Мои объекты", desc: "Свои объявления: добавить, изменить, снять", user: true, also: ["/my/edit"] },
    { href: "/clients", icon: "users", tip: "Клиенты", desc: "Клиенты по этапам сделки и подбор объектов", main: true, user: true },
    { href: "/work", icon: "heart", tip: "В работе", desc: "Избранное и объекты в работе: звонил, показ, думает", main: true, user: true,
      count: (c) => c.work, countTip: (n) => `в работе: ${n}` },
    { href: "/planner", icon: "calendar", tip: "Планер", desc: "Показы, звонки и встречи с напоминаниями", main: true, user: true,
      count: (c) => c.plan_today + c.plan_overdue, countTip: (n) => `дел на сегодня и просроченных: ${n}`, hot: (c) => c.plan_overdue > 0 },
    { href: "/picks", icon: "folder", tip: "Подборки", desc: "Ссылка с объектами для клиента — без чужих номеров", user: true },
    { href: "/market", icon: "chart", tip: "Аналитика рынка", desc: "Цена за метр по районам и ЖК, динамика" },
    { href: "/notifications", icon: "bell", tip: "Уведомления", desc: "Новые объекты по поискам, снижение цен", user: true, bell: true },
    { sep: true },
    { href: "/pay", icon: "card", tip: "Подписка", desc: "Доступ к номерам агентов и тарифы" },
    { href: "/settings", icon: "user", tip: "Настройки", desc: "Профиль, вид подборок для клиентов, вход", user: true },
    { href: "/how", icon: "help", tip: "Как это работает", desc: "Коротко о сервисе и ответы на вопросы" },
    { sep: true, admin: true },
    { href: "/requests", icon: "briefcase", tip: "Запросы покупателей", desc: "Кто что ищет — запросы агентов из чатов (видно только админу)", admin: true },
    { href: "/learn", icon: "layers", tip: "Обучение и разбор", desc: "Очереди разбора, что выучил сервис", admin: true, also: ["/fix", "/admin/map"] },
    { href: "/admin", icon: "sliders", tip: "Админка", desc: "Пользователи, чаты, номера, статистика", admin: true },
  ];
  const isOn = (it) => (it.on ? it.on() : path === it.href || (it.also || []).includes(path) || (it.href !== "/" && path.startsWith(it.href + "/")));
  const tipHTML = (t, d) => `<span class="rail-tip" role="tooltip"><b>${t}</b>${d ? `<small>${d}</small>` : ""}</span>`;

  function build(me) {
    const rail = document.createElement("nav");
    rail.className = "rail rail-g";
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
    rail.innerHTML = `<a class="rail-logo" href="/" aria-label="1+1 — к поиску"><img src="/static/favicon.svg" alt="" width="38" height="38"></a>`
      + items.map((it) => it.sep ? `<span class="rail-sep"></span>`
        : `<a class="rail-a${isOn(it) ? " on" : ""}${it.main ? " main" : ""}" href="${it.href}" aria-label="${it.tip}">${ic(it.icon)}${it.bell ? `<b class="rail-n" hidden></b>` : badge(it)}${tipHTML(it.tip, badge(it) ? `${it.desc} · ${it.countTip(it.count(counts))}` : it.desc)}</a>`).join("")
      + `<span class="rail-gap"></span>`
      + `<button type="button" class="rail-a main rail-more" aria-label="Все разделы" aria-expanded="false">${ic("grid")}</button>`
      + (me ? `<a class="rail-ava" href="/cabinet" aria-label="Кабинет">${((me.name || me.email || "1").trim()[0] || "1").toUpperCase()}${tipHTML("Кабинет", who)}</a>`
        : `<a class="rail-a main" href="/?login=1" aria-label="Войти">${ic("user")}${tipHTML("Войти", "Неделя бесплатно — без карты")}</a>`);
    document.body.prepend(rail);
    document.body.classList.add("has-rail");
    if (document.querySelector(".tabbar")) document.body.classList.add("has-tabbar");
    if (window.__bellN) window.railBell(window.__bellN);

    // телефон: «Все разделы» — шторка со списком всех страниц и пояснениями
    const sheet = document.createElement("div");
    sheet.className = "rail-sheet";
    sheet.setAttribute("popover", "auto");
    sheet.innerHTML = `<div class="rs-in"><span class="rs-grab"></span><b class="rs-h">Разделы</b>`
      + items.filter((it) => !it.sep).map((it) => `<a class="rs-a${isOn(it) ? " on" : ""}" href="${it.href}">${ic(it.icon)}<span><b>${it.tip}</b><small>${it.desc || ""}</small></span>${badge(it)}</a>`).join("")
      + `</div>`;
    document.body.appendChild(sheet);
    const more = rail.querySelector(".rail-more");
    more.addEventListener("click", () => { try { sheet.togglePopover(); } catch { sheet.classList.toggle("open"); } });
    sheet.addEventListener("toggle", (e) => more.setAttribute("aria-expanded", e.newState === "open"));
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
