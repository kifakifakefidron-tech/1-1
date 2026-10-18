/* Согласие на cookie (17.10, Артём: «нужно чёткое согласие, а не просто «Понятно», и возможность его отозвать»).
   Необходимые cookie (вход в аккаунт) работают всегда — без них сайт не работает. Необязательные — только после
   «Принять все»: статистика посещений (кто на сайте, просмотры подборок) и приглашение коллеги (?ref=).
   Выбор хранится в cookie ck (all | need) на год; сервер (POST /api/c/consent) ставит/удаляет необязательные cookie.
   Изменить выбор: ссылка с [data-cookie-settings] (подвал, политика, настройки) или window.cookieSettings(). */
(() => {
  "use strict";
  const isPick = location.pathname.startsWith("/c/");
  const embed = new URLSearchParams(location.search).has("embed");
  const read = () => (document.cookie.match(/(?:^|;\s*)ck=(all|need)/) || [])[1] || "";
  window.cookieChoice = read;
  // приглашение коллеги — до согласия только в памяти вкладки
  const ref = new URLSearchParams(location.search).get("ref");
  if (ref) { try { sessionStorage.setItem("refPending", ref.replace(/[^\w-]/g, "").slice(0, 20)); } catch { /* */ } }

  let bar = null;
  function build() {
    bar = document.createElement("div");
    bar.className = "ck-bar";
    bar.setAttribute("popover", "manual");   // верхний слой — поверх открытых окон
    bar.setAttribute("role", "dialog");
    bar.setAttribute("aria-label", "Согласие на cookie");
    bar.innerHTML = `<div class="ck-in">
      <div class="ck-txt"><b>Cookie на этом сайте</b>
        <p><span>Необходимые</span> — чтобы работал вход и сохранялись ваши настройки, без них сайт не работает.
          <span>По вашему согласию</span> — статистика посещений и запоминание приглашения коллеги. Рекламы и сторонних счётчиков нет.
          ${isPick ? "" : `<a href="/privacy#cookie">Подробнее</a>`}</p>
        <p class="ck-now" hidden></p></div>
      <div class="ck-btns">
        <button type="button" class="ck-b ck-all" data-ck="all">Принять все</button>
        <button type="button" class="ck-b ck-need" data-ck="need">Только необходимые</button>
      </div></div>`;
    document.body.appendChild(bar);
    bar.addEventListener("click", async (e) => {
      const b = e.target.closest("[data-ck]");
      if (!b) return;
      const choice = b.dataset.ck;
      let pending = "";
      try { pending = sessionStorage.getItem("refPending") || ""; } catch { /* */ }
      try {
        await fetch("/api/c/consent", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ choice, ref: choice === "all" ? pending : "" }) });
      } catch { /* нет связи — запомним в браузере, сервер узнает при следующем выборе */
        document.cookie = `ck=${choice}; max-age=${365 * 86400}; path=/; samesite=lax`;
      }
      if (choice === "need") {   // отзыв согласия: убираем и то, что хранилось в браузере
        try { localStorage.removeItem("pg"); sessionStorage.removeItem("refPending"); } catch { /* */ }
      }
      hide();
      document.dispatchEvent(new CustomEvent("oneplus:cookies", { detail: choice }));
    });
  }
  function show(again) {
    if (!bar) build();
    const now = bar.querySelector(".ck-now"), cur = read();
    now.hidden = !again || !cur;
    if (cur) now.textContent = cur === "all" ? "Сейчас: приняты все cookie. Можно отозвать согласие — «Только необходимые»."
      : "Сейчас: только необходимые cookie.";
    bar.querySelectorAll("[data-ck]").forEach((b) => b.classList.toggle("cur", b.dataset.ck === cur));
    bar.hidden = false;
    place();
    requestAnimationFrame(() => bar.classList.add("in"));
  }
  // открытое окно (<dialog>) делает всё вне себя неактивным — баннер кладём внутрь него, закрылось окно — обратно на страницу
  const topModal = () => [...document.querySelectorAll("dialog[open]")].filter((d) => { try { return d.matches(":modal"); } catch { return true; } }).pop();
  function place() {
    const host = topModal() || document.body;
    if (bar.parentElement !== host) {
      host.appendChild(bar);
      if (host !== document.body) host.addEventListener("close", () => { if (!bar.hidden) place(); }, { once: true });
    }
    try { bar.hidePopover(); bar.showPopover(); } catch { /* старый браузер — обычный слой */ }
  }
  new MutationObserver(() => { if (bar && !bar.hidden && (topModal() || document.body) !== bar.parentElement) place(); })
    .observe(document.documentElement, { subtree: true, attributes: true, attributeFilter: ["open"] });
  function hide() {
    if (!bar) return;
    bar.classList.remove("in");
    setTimeout(() => { try { bar.hidePopover(); } catch { /* */ } bar.hidden = true; }, 320);
  }
  window.cookieSettings = () => show(true);
  document.addEventListener("click", (e) => {
    const a = e.target.closest("[data-cookie-settings]");
    if (a) { e.preventDefault(); show(true); }
  });
  const start = () => { if (!read() && !embed) setTimeout(() => show(false), 600); };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
