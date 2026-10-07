// Выделили текст (адрес, ЖК) — рядом кнопка «🔎 В Яндекс Картах»: откроет поиск Яндекс Карт по Краснодару.
// Включается вызовом window.yaSelect(true) (на сайте — только у админа, на /fix — всегда).
(function () {
  "use strict";
  let on = false, btn = null, text = "";

  const url = (t) => {
    const q = /краснодар|край|станиц|посел|пос[её]лок|хутор|аул/i.test(t) ? t : `Краснодар, ${t}`;
    return `https://yandex.ru/maps/35/krasnodar/?text=${encodeURIComponent(q)}`;
  };

  function hide() { if (btn) btn.hidden = true; }

  function show() {
    if (!on) return;
    const sel = window.getSelection();
    const t = sel && !sel.isCollapsed ? sel.toString().replace(/\s+/g, " ").trim() : "";
    const node = sel && sel.anchorNode && (sel.anchorNode.nodeType === 1 ? sel.anchorNode : sel.anchorNode.parentElement);
    if (t.length < 3 || t.length > 200 || (node && node.closest("input, textarea, .ya-sel"))) { hide(); return; }
    text = t;
    if (!btn) {
      btn = document.createElement("a");
      btn.className = "ya-sel";
      btn.target = "_blank";
      btn.rel = "noopener";
      btn.textContent = "🔎 В Яндекс Картах";
      btn.addEventListener("mousedown", (e) => e.preventDefault());   // не снимать выделение
      btn.addEventListener("click", () => setTimeout(hide, 50));
    }
    // кнопку кладём в открытое окно (dialog — верхний слой), иначе в body
    const host = (node && node.closest("dialog[open]")) || document.body;
    if (btn.parentNode !== host) host.appendChild(btn);
    btn.href = url(text);
    const r = sel.getRangeAt(0).getBoundingClientRect();
    btn.hidden = false;
    const w = btn.offsetWidth || 160;
    btn.style.left = `${Math.max(8, Math.min(window.innerWidth - w - 8, r.left + r.width / 2 - w / 2))}px`;
    btn.style.top = `${r.top > 48 ? r.top - 40 : r.bottom + 8}px`;
  }

  let timer = 0;
  document.addEventListener("selectionchange", () => { clearTimeout(timer); timer = setTimeout(show, 250); });
  window.addEventListener("scroll", hide, true);
  window.yaSelect = (v) => { on = !!v; if (!on) hide(); };
  window.yaMapsUrl = url;
})();
