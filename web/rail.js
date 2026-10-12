/* 1+1 · меню слева на всех страницах (как в кабинете): на компьютере — стеклянный «рельс» иконок с подсказками,
   на телефоне — нижняя панель из главных разделов (на главной её роль играет своя .tabbar).
   Не показывается на странице оплаты и на странице подборки для клиента. */
(() => {
  "use strict";
  const path = location.pathname;
  if (path === "/pay" || path.startsWith("/c/")) return;
  const ic = (n) => (window.ic ? window.ic(n) : "");
  const params = new URLSearchParams(location.search);
  const ITEMS = [
    { href: "/", icon: "search", tip: "Поиск объектов", main: true, on: () => path === "/" && !params.has("fav") },
    { href: "/cabinet", icon: "grid", tip: "Кабинет", main: true, user: true },
    { href: "/clients", icon: "users", tip: "Клиенты", main: true, user: true },
    { href: "/?fav=1", icon: "heart", tip: "Избранное и статусы", main: true, user: true, on: () => path === "/" && params.has("fav") },
    { href: "/planner", icon: "calendar", tip: "Планер", main: true, user: true },
    { href: "/picks", icon: "folder", tip: "Подборки клиентам", user: true },
    { href: "/requests", icon: "briefcase", tip: "Запросы покупателей" },
    { href: "/market", icon: "chart", tip: "Аналитика рынка" },
    { href: "/notifications", icon: "bell", tip: "Уведомления", user: true, bell: true },
    { sep: true, admin: true },
    { href: "/learn", icon: "layers", tip: "Обучение и разбор", admin: true, also: ["/fix", "/admin/map"] },
    { href: "/admin", icon: "sliders", tip: "Админка", admin: true },
  ];
  const isOn = (it) => (it.on ? it.on() : path === it.href || (it.also || []).includes(path) || (it.href !== "/" && path.startsWith(it.href + "/")));

  function build(me) {
    const rail = document.createElement("nav");
    rail.className = "rail rail-g";
    rail.setAttribute("aria-label", "Разделы");
    const items = ITEMS.filter((it) => (!it.user || me) && (!it.admin || (me && me.is_admin)));
    rail.innerHTML = `<a class="rail-logo" href="/" aria-label="1+1 — к поиску"><img src="/static/favicon.svg" alt="" width="38" height="38"></a>`
      + items.map((it) => it.sep ? `<span class="rail-sep"></span>`
        : `<a class="rail-a${isOn(it) ? " on" : ""}${it.main ? " main" : ""}" href="${it.href}" data-tip="${it.tip}" aria-label="${it.tip}">${ic(it.icon)}${it.bell ? `<b class="rail-n" hidden></b>` : ""}</a>`).join("")
      + `<span class="rail-gap"></span>`
      + (me ? `<a class="rail-ava" href="/cabinet" data-tip="Кабинет · ${(me.name || me.email || "").replace(/"/g, "")}">${((me.name || me.email || "1").trim()[0] || "1").toUpperCase()}</a>`
        : `<a class="rail-a main" href="/?login=1" data-tip="Войти" aria-label="Войти">${ic("user")}</a>`);
    document.body.prepend(rail);
    document.body.classList.add("has-rail");
    if (document.querySelector(".tabbar")) document.body.classList.add("has-tabbar");
    if (window.__bellN) window.railBell(window.__bellN);
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
