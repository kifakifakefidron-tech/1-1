/* 1+1 · свои элементы выбора вместо браузерных (в нашем стиле):
   Pick.date(input)   — <input type=date|hidden> → кнопка «Пн, 12 окт» + календарь месяца и «Сегодня / Завтра / …»;
   Pick.time(input)   — <input type=time> → кнопка «10:30» + часы и минуты плитками;
   Pick.select(sel)   — <select> → своя кнопка и список (поиск, если вариантов много);
   Pick.chips(sel)    — <select> с несколькими вариантами → ряд кнопок-переключателей.
   Значения пишутся в исходные поля (их формат тот же) + событие change, поэтому остальной код не меняется.
   Панели — popover (верхний слой): работают и внутри открытого окна <dialog>. */
(() => {
  "use strict";
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pad = (n) => String(n).padStart(2, "0");
  const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const parse = (v) => { const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(v || ""); return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null; };
  const MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];
  const fire = (el) => { el.dispatchEvent(new Event("input", { bubbles: true })); el.dispatchEvent(new Event("change", { bubbles: true })); };
  const CHEV = '<svg viewBox="0 0 24 24" class="pk-chev" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>';
  const CAL = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="5" width="17" height="15.5" rx="3"/><path d="M3.5 10h17M8 3v4M16 3v4"/></svg>';
  const CLOCK = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/></svg>';

  // ─── общая панель ────────────────────────────────────────────────────
  let panel = null, owner = null;
  function getPanel() {
    if (panel) return panel;
    panel = document.createElement("div");
    panel.className = "pk-panel";
    panel.setAttribute("popover", "manual");
    document.body.appendChild(panel);
    document.addEventListener("pointerdown", (e) => {
      if (!owner || panel.contains(e.target) || owner.contains(e.target)) return;
      close();
    }, true);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && owner) { e.stopPropagation(); e.preventDefault(); close(); } }, true);
    window.addEventListener("resize", () => owner && place(owner));
    return panel;
  }
  function place(btn) {
    const p = getPanel(), r = btn.getBoundingClientRect(), vw = innerWidth, vh = innerHeight;
    if (vw < 640) { p.classList.add("sheet"); p.style.left = p.style.top = ""; return; }
    p.classList.remove("sheet");
    const w = p.offsetWidth, h = p.offsetHeight;
    let left = Math.min(Math.max(8, r.left), vw - w - 8);
    let top = r.bottom + 6;
    if (top + h > vh - 8 && r.top - h - 6 > 8) top = r.top - h - 6;
    p.style.left = `${left}px`;
    p.style.top = `${Math.max(8, Math.min(top, vh - h - 8))}px`;
  }
  function open(btn, html, bind) {
    const p = getPanel();
    if (owner === btn) { close(); return; }
    owner = btn;
    btn.classList.add("open");
    p.innerHTML = html;
    try { p.hidePopover(); p.showPopover(); } catch { p.hidden = false; }
    p.classList.remove("in");
    place(btn);
    requestAnimationFrame(() => p.classList.add("in"));
    bind(p);
  }
  function close() {
    if (!panel || !owner) return;
    owner.classList.remove("open");
    owner = null;
    panel.classList.remove("in");
    try { panel.hidePopover(); } catch { panel.hidden = true; }
  }

  function makeBtn(input, cls, icon) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = `pk-btn ${cls}`;
    b.innerHTML = `${icon || ""}<span class="pk-val"></span>${CHEV}`;
    input.classList.add("pk-src");
    input.insertAdjacentElement("afterend", b);
    return b;
  }

  // ─── дата ────────────────────────────────────────────────────────────
  function date(input, opts = {}) {
    if (input.dataset.pk) return;
    input.dataset.pk = "1";
    input.required = false;
    const b = makeBtn(input, "pk-date", CAL);
    const show = () => {
      const d = parse(input.value);
      b.querySelector(".pk-val").textContent = d ? d.toLocaleDateString("ru-RU", { weekday: "short", day: "numeric", month: "long" })
        : (opts.placeholder || "Выбрать дату");
      b.classList.toggle("empty", !d);
    };
    const set = (d) => { input.value = d ? iso(d) : ""; show(); fire(input); };
    let view = null;
    const render = (p) => {
      const sel = parse(input.value), today = new Date(); today.setHours(0, 0, 0, 0);
      view = view || new Date((sel || today).getFullYear(), (sel || today).getMonth(), 1);
      const first = (view.getDay() + 6) % 7, days = new Date(view.getFullYear(), view.getMonth() + 1, 0).getDate();
      let cells = "";
      for (let i = 0; i < first; i++) cells += "<span></span>";
      for (let n = 1; n <= days; n++) {
        const d = new Date(view.getFullYear(), view.getMonth(), n);
        const c = [+d === +today ? "today" : "", sel && +d === +sel ? "on" : "", d < today && !opts.past ? "past" : ""].join(" ");
        cells += `<button type="button" class="pk-day ${c}" data-d="${iso(d)}">${n}</button>`;
      }
      const q = (t, add) => { const d = new Date(today); d.setDate(d.getDate() + add); return `<button type="button" class="pk-chip" data-d="${iso(d)}">${t}</button>`; };
      p.innerHTML = `<div class="pk-quick">${q("Сегодня", 0)}${q("Завтра", 1)}${q("Послезавтра", 2)}${q("Через неделю", 7)}
          ${opts.clearable ? `<button type="button" class="pk-chip" data-clear>Без даты</button>` : ""}</div>
        <div class="pk-cal-h"><button type="button" class="pk-nav" data-m="-1" aria-label="Предыдущий месяц">‹</button>
          <b>${MONTHS[view.getMonth()]} ${view.getFullYear()}</b><button type="button" class="pk-nav" data-m="1" aria-label="Следующий месяц">›</button></div>
        <div class="pk-week">${["пн", "вт", "ср", "чт", "пт", "сб", "вс"].map((w) => `<span>${w}</span>`).join("")}</div>
        <div class="pk-grid">${cells}</div>`;
    };
    b.addEventListener("click", () => {
      view = null;
      open(b, "", (p) => {
        render(p);
        p.onclick = (e) => {
          const t = e.target.closest("button");
          if (!t) return;
          if (t.dataset.m) { view = new Date(view.getFullYear(), view.getMonth() + Number(t.dataset.m), 1); render(p); place(b); return; }
          if (t.hasAttribute("data-clear")) { set(null); close(); return; }
          if (t.dataset.d) { set(parse(t.dataset.d)); close(); }
        };
      });
    });
    input.addEventListener("pk-refresh", show);
    show();
  }

  // ─── время ───────────────────────────────────────────────────────────
  function time(input, opts = {}) {
    if (input.dataset.pk) return;
    input.dataset.pk = "1";
    input.required = false;
    const b = makeBtn(input, "pk-time", CLOCK);
    const show = () => { b.querySelector(".pk-val").textContent = input.value || opts.placeholder || "Время"; b.classList.toggle("empty", !input.value); };
    const from = opts.from ?? 7, to = opts.to ?? 22;
    b.addEventListener("click", () => {
      const [h0, m0] = (input.value || "10:00").split(":").map(Number);
      let h = h0, m = m0 - (m0 % 5);
      const render = (p) => {
        let hours = "";
        for (let x = from; x <= to; x++) hours += `<button type="button" class="pk-cell${x === h ? " on" : ""}" data-h="${x}">${pad(x)}</button>`;
        const mins = [0, 10, 15, 20, 30, 40, 45, 50].map((x) => `<button type="button" class="pk-cell${x === m ? " on" : ""}" data-min="${x}">:${pad(x)}</button>`).join("");
        p.innerHTML = `<p class="pk-lbl">Час</p><div class="pk-hours">${hours}</div><p class="pk-lbl">Минуты</p><div class="pk-mins">${mins}</div>
          <button type="button" class="pk-ok" data-ok>Готово · ${pad(h)}:${pad(m)}</button>`;
      };
      open(b, "", (p) => {
        render(p);
        p.onclick = (e) => {
          const t = e.target.closest("button");
          if (!t) return;
          if (t.dataset.h) { h = Number(t.dataset.h); render(p); }
          else if (t.dataset.min) { m = Number(t.dataset.min); render(p); }
          if (t.dataset.h || t.dataset.min) { input.value = `${pad(h)}:${pad(m)}`; show(); fire(input); }
          if (t.hasAttribute("data-ok") || t.dataset.min) { input.value = `${pad(h)}:${pad(m)}`; show(); fire(input); if (t.hasAttribute("data-ok")) close(); }
        };
      });
    });
    input.addEventListener("pk-refresh", show);
    input.addEventListener("change", show);
    show();
  }

  // ─── список ──────────────────────────────────────────────────────────
  function select(sel, opts = {}) {
    if (sel.dataset.pk) { sel.dispatchEvent(new Event("pk-refresh")); return; }
    sel.dataset.pk = "1";
    const b = makeBtn(sel, "pk-select");
    const show = () => {
      const o = sel.options[sel.selectedIndex];
      b.querySelector(".pk-val").textContent = o ? o.textContent : (opts.placeholder || "Выбрать");
    };
    b.addEventListener("click", () => {
      const many = sel.options.length > 8;
      const items = (q) => [...sel.options].filter((o) => !q || o.textContent.toLowerCase().includes(q.toLowerCase()))
        .map((o) => `<button type="button" class="pk-opt${o.selected ? " on" : ""}" data-v="${esc(o.value)}">${esc(o.textContent)}</button>`).join("")
        || `<p class="pk-lbl">Ничего не нашлось</p>`;
      open(b, "", (p) => {
        p.innerHTML = `${many ? `<input class="pk-search" placeholder="Найти…" autocomplete="off">` : ""}<div class="pk-opts">${items("")}</div>`;
        const s = p.querySelector(".pk-search");
        if (s) { s.addEventListener("input", () => { p.querySelector(".pk-opts").innerHTML = items(s.value.trim()); }); if (innerWidth >= 640) s.focus(); }
        p.onclick = (e) => {
          const t = e.target.closest("[data-v]");
          if (!t) return;
          sel.value = t.dataset.v; show(); fire(sel); close();
        };
      });
    });
    sel.addEventListener("pk-refresh", show);
    sel.addEventListener("change", show);
    show();
  }

  // ─── варианты кнопками ───────────────────────────────────────────────
  function chips(sel) {
    if (sel.dataset.pk) { sel.dispatchEvent(new Event("pk-refresh")); return; }
    sel.dataset.pk = "1";
    const row = document.createElement("div");
    row.className = "pk-chips";
    sel.classList.add("pk-src");
    sel.insertAdjacentElement("afterend", row);
    const render = () => {
      row.innerHTML = [...sel.options].map((o) => `<button type="button" class="pk-chip${o.selected ? " on" : ""}" data-v="${esc(o.value)}">${esc(o.textContent)}</button>`).join("");
    };
    row.addEventListener("click", (e) => {
      const t = e.target.closest("[data-v]");
      if (!t) return;
      sel.value = t.dataset.v; render(); fire(sel);
    });
    sel.addEventListener("pk-refresh", render);
    sel.addEventListener("change", render);
    render();
  }

  // программно поменяли значение исходного поля — обновить кнопку: Pick.refresh(form)
  const refresh = (root) => root.querySelectorAll("[data-pk]").forEach((el) => el.dispatchEvent(new Event("pk-refresh")));
  window.Pick = { date, time, select, chips, refresh, close };
})();
