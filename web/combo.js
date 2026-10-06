/* 1+1 · красивый выпадающий список с поиском (вместо стандартного <datalist>).
   Combo(input, { options: () => [{value, meta?}], onPick(value), onNew?(text), newLabel?(text) })
   ↑/↓ — выбор, Enter — взять, Esc — закрыть. Совпадение подсвечивается, сначала — начинающиеся с введённого. */
(() => {
  "use strict";
  const norm = (s) => String(s || "").toLowerCase().replace(/ё/g, "е").trim();
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function mark(text, q) {
    if (!q) return esc(text);
    const i = norm(text).indexOf(q);
    if (i < 0) return esc(text);
    return esc(text.slice(0, i)) + "<mark>" + esc(text.slice(i, i + q.length)) + "</mark>" + esc(text.slice(i + q.length));
  }

  window.Combo = function Combo(input, opts) {
    input.removeAttribute("list");
    input.setAttribute("autocomplete", "off");
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-expanded", "false");
    const wrap = document.createElement("div");
    wrap.className = "combo";
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    const list = document.createElement("div");
    list.className = "combo-list";
    list.setAttribute("role", "listbox");
    list.hidden = true;
    wrap.appendChild(list);
    let items = [], active = -1;

    function render() {
      const q = norm(input.value);
      const all = (opts.options() || []).map((o) => (typeof o === "string" ? { value: o } : o));
      let found = all.filter((o) => !q || norm(o.value).includes(q));
      found.sort((a, b) => (norm(b.value).startsWith(q) - norm(a.value).startsWith(q)));
      found = found.slice(0, 80);
      items = found.map((o) => ({ ...o, kind: "pick" }));
      const exact = all.some((o) => norm(o.value) === q);
      if (opts.onNew && q.length >= 2 && !exact) items.push({ value: input.value.trim(), kind: "new" });
      active = items.length ? 0 : -1;
      list.innerHTML = items.length ? items.map((o, i) => `<div class="combo-item${o.kind === "new" ? " new" : ""}${i === active ? " on" : ""}"
          role="option" data-i="${i}">${o.kind === "new" ? esc(opts.newLabel ? opts.newLabel(o.value) : `+ Добавить «${o.value}»`)
          : `<span>${mark(o.value, q)}</span>${o.meta ? `<small>${esc(o.meta)}</small>` : ""}`}</div>`).join("")
        : `<div class="combo-empty">Ничего не нашлось</div>`;
    }
    function open() { render(); list.hidden = false; input.setAttribute("aria-expanded", "true"); }
    function close() { list.hidden = true; input.setAttribute("aria-expanded", "false"); }
    function move(d) {
      if (!items.length) return;
      active = (active + d + items.length) % items.length;
      list.querySelectorAll(".combo-item").forEach((el, i) => el.classList.toggle("on", i === active));
      const el = list.querySelector(".combo-item.on");
      if (el) el.scrollIntoView({ block: "nearest" });
    }
    function pick(i) {
      const o = items[i];
      if (!o) return;
      close();
      if (o.kind === "new") { opts.onNew(o.value); return; }
      input.value = o.value;
      if (opts.onPick) opts.onPick(o.value);
    }

    input.addEventListener("focus", open);
    input.addEventListener("click", () => { if (list.hidden) open(); });
    input.addEventListener("input", open);
    input.addEventListener("keydown", (e) => {
      if (list.hidden && (e.key === "ArrowDown" || e.key === "ArrowUp")) { open(); e.preventDefault(); return; }
      if (list.hidden) return;
      if (e.key === "ArrowDown") { move(1); e.preventDefault(); }
      else if (e.key === "ArrowUp") { move(-1); e.preventDefault(); }
      else if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); pick(active); }
      else if (e.key === "Escape") { e.stopPropagation(); close(); }
      else if (e.key === "Tab") close();
    });
    list.addEventListener("mousedown", (e) => {   // mousedown — раньше, чем поле потеряет фокус
      const el = e.target.closest(".combo-item");
      if (el) { e.preventDefault(); pick(Number(el.dataset.i)); }
    });
    input.addEventListener("blur", () => setTimeout(close, 120));
    return { open, close, refresh: () => { if (!list.hidden) render(); } };
  };
})();
