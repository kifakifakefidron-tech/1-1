// Выделили текст (адрес, ЖК) — снизу появляется плашка «В Яндекс Картах»: откроет поиск Яндекс Карт по Краснодару.
// Включается вызовом window.yaSelect(true) (на сайте — только у админа, на /fix — всегда).
(function () {
  "use strict";
  let on = false, bar = null, closed = "";

  const url = (t) => {
    const q = /краснодар|край|станиц|посел|пос[её]лок|хутор|аул/i.test(t) ? t : `Краснодар, ${t}`;
    return `https://yandex.ru/maps/35/krasnodar/?text=${encodeURIComponent(q)}`;
  };
  const icon = (n) => (window.ic ? window.ic(n) : "");

  function hide() { if (bar) bar.classList.remove("in"); }

  function show() {
    if (!on) return;
    const sel = window.getSelection();
    const t = sel && !sel.isCollapsed ? sel.toString().replace(/\s+/g, " ").trim() : "";
    const node = sel && sel.anchorNode && (sel.anchorNode.nodeType === 1 ? sel.anchorNode : sel.anchorNode.parentElement);
    if (t.length < 3 || t.length > 200 || t === closed || (node && node.closest("input, textarea, .ya-bar"))) { if (!t) hide(); return; }
    if (!bar) {
      bar = document.createElement("div");
      bar.className = "ya-bar";
      bar.innerHTML = `<span class="ya-bar-ic">${icon("search")}</span><span class="ya-bar-q"></span>
        <a class="ya-bar-go" target="_blank" rel="noopener">В Яндекс Картах ${icon("external")}</a>
        <button type="button" class="ya-bar-x" aria-label="Закрыть">${icon("x")}</button>`;
      bar.addEventListener("mousedown", (e) => { if (!e.target.closest("a, button")) e.preventDefault(); });   // не снимать выделение
      bar.querySelector(".ya-bar-x").addEventListener("click", () => { closed = bar.querySelector(".ya-bar-q").textContent.slice(1, -1); hide(); });
      bar.querySelector(".ya-bar-go").addEventListener("click", () => setTimeout(hide, 300));
    }
    // плашку кладём в открытое окно (dialog — верхний слой), иначе в body
    const host = (node && node.closest("dialog[open]")) || document.body;
    if (bar.parentNode !== host) host.appendChild(bar);
    bar.querySelector(".ya-bar-q").textContent = `«${t.length > 60 ? t.slice(0, 60) + "…" : t}»`;
    bar.querySelector(".ya-bar-go").href = url(t);
    closed = "";
    requestAnimationFrame(() => bar.classList.add("in"));
  }

  let timer = 0;
  document.addEventListener("selectionchange", () => { clearTimeout(timer); timer = setTimeout(show, 300); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") hide(); });
  window.yaSelect = (v) => { on = !!v; if (!on) hide(); };
  window.yaMapsUrl = url;
})();
