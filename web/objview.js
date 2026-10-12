/* 1+1 · окно объекта поверх ЛЮБОЙ страницы (кабинет, клиенты, планер, подборки, уведомления…).
   Раньше ссылка «/?open=ID» уводила на главную, и после закрытия приходилось искать, откуда пришли.
   Теперь ссылка открывает то же окно объекта (главная в режиме ?embed=1 во встроенном фрейме) прямо здесь;
   закрыли — остались на своей странице. Ctrl/⌘-клик и средняя кнопка — как обычно, в новой вкладке. */
(() => {
  "use strict";
  if (location.pathname === "/") return;
  let box = null;

  function close() {
    if (!box) return;
    const b = box;
    box = null;
    b.classList.add("bye");
    setTimeout(() => { try { b.hidePopover(); } catch { /* */ } b.remove(); }, 220);
  }

  function open(id) {
    if (box) close();
    box = document.createElement("div");
    box.className = "ov-box";
    box.setAttribute("popover", "manual");   // верхний слой — поверх уже открытых окон страницы (dialog)
    box.innerHTML = `<span class="ov-spin" aria-hidden="true"></span>
      <iframe title="Объект" src="/?open=${id}&embed=1" allow="clipboard-write; web-share"></iframe>`;
    const host = [...document.querySelectorAll("dialog[open]")].filter((d) => { try { return d.matches(":modal"); } catch { return true; } }).pop() || document.body;   // ссылка внутри открытого окна (карточка клиента) — окно объекта кладём в него, иначе оно «за окном» и не нажимается
    host.appendChild(box);
    try { box.showPopover(); } catch { box.classList.add("open"); }
    const fr = box.querySelector("iframe");
    fr.addEventListener("load", () => fr.focus());   // показываем, когда окно объекта само скажет «готово»
    const me = box;
    setTimeout(() => { if (box === me && !me.classList.contains("ready")) me.classList.add("ready"); }, 6000);
  }

  function note(text) {
    const n = document.createElement("div");
    n.className = "ov-note";
    n.setAttribute("popover", "manual");
    n.textContent = text;
    document.body.appendChild(n);
    try { n.showPopover(); } catch { /* */ }
    setTimeout(() => n.remove(), 3500);
  }

  // окно объекта во фрейме сообщает: открылось / закрылось
  addEventListener("message", (e) => {
    if (e.origin !== location.origin || !e.data || !e.data.ov) return;
    if (e.data.ov === "close") close();
    if (e.data.ov === "gone") { close(); note("Объект не найден — возможно, его уже сняли с сайта."); }
    if (e.data.ov === "ready" && box) box.classList.add("ready");
  });
  addEventListener("keydown", (e) => { if (e.key === "Escape" && box) close(); });

  document.addEventListener("click", (e) => {
    if (e.defaultPrevented || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    const a = e.target.closest("a[href]");
    if (!a) return;
    let u;
    try { u = new URL(a.getAttribute("href"), location.href); } catch { return; }
    if (u.origin !== location.origin || u.pathname !== "/") return;
    const id = Number(u.searchParams.get("open"));
    if (!id || [...u.searchParams.keys()].some((k) => k !== "open")) return;   // служебные ссылки админки — как раньше
    e.preventDefault();
    open(id);
  }, true);

  window.ObjView = { open, close };
})();
