/* 1+1 · свои элементы выбора вместо браузерных (в нашем стиле) — на ВСЕХ страницах сами:
   <select>            → своя кнопка и список (поиск, если вариантов много, прокрутка);
   <select multiple>   → список с галочками: можно выбрать несколько, «Готово», «Сбросить»;
   <select data-pick="chips"> → ряд кнопок-переключателей;
   <input type=date>   → кнопка «Пн, 12 окт» + календарь и «Сегодня / Завтра / …»;
   <input type=time>   → кнопка «10:30» + часы и минуты плитками.
   Не трогать поле: data-native. Вручную: Pick.select / multi / chips / date / time; Pick.refresh(form).
   Значения пишутся в исходные поля (тот же формат) + событие change, поэтому остальной код не меняется;
   если код меняет значение поля сам (sel.value = …, новые варианты, form.reset) — кнопка обновится сама.
   Панель открывается ВНУТРИ открытого окна (dialog): иначе браузер считает её «за окном» и не даёт нажимать. */
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
  const TICK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';

  // ─── общая панель ────────────────────────────────────────────────────
  let panel = null, owner = null;
  const topModal = () => [...document.querySelectorAll("dialog[open]")].filter((d) => { try { return d.matches(":modal"); } catch { return true; } }).pop();
  function getPanel() {
    if (panel) return panel;
    panel = document.createElement("div");
    panel.className = "pk-panel";
    panel.setAttribute("popover", "manual");
    document.body.appendChild(panel);
    document.addEventListener("pointerdown", (e) => {
      if (!owner || panel.contains(e.target) || owner.contains(e.target)) return;
      if (panel.classList.contains("sheet")) swallowClick();   // 17.10: на телефоне тап мимо — просто закрыть
      close();
    }, true);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && owner) { e.stopPropagation(); e.preventDefault(); close(); } }, true);
    window.addEventListener("resize", () => owner && place(owner));
    document.addEventListener("scroll", (e) => { if (owner && !panel.contains(e.target)) place(owner); }, true);
    return panel;
  }
  function place(btn) {
    const p = getPanel(), r = btn.getBoundingClientRect(), vw = innerWidth, vh = innerHeight;
    if (vw < 640) { p.classList.add("sheet"); p.style.left = p.style.top = p.style.width = ""; return; }
    p.classList.remove("sheet");
    if (p.classList.contains("wide")) p.style.width = `${p.dataset.w ? Math.min(Number(p.dataset.w), vw - 16) : Math.max(300, Math.min(r.width, 420))}px`;
    const w = p.offsetWidth, h = p.offsetHeight;
    const left = Math.min(Math.max(8, r.left), vw - w - 8);
    let top = r.bottom + 6;
    if (btn.closest("[data-pk-down]")) {   // поле с подписями выше (форма клиента): только вниз, при нехватке места — прокрутить
      p.style.maxHeight = "";
      if (top + h > vh - 8 && !btn.__pkScrolled) {
        btn.__pkScrolled = true;
        btn.scrollIntoView({ block: "center" });
        setTimeout(() => { btn.__pkScrolled = false; }, 400);
        return place(btn);
      }
      p.style.maxHeight = `${Math.max(160, vh - top - 8)}px`;
      p.style.left = `${left}px`;
      p.style.top = `${top}px`;
      return;
    }
    if (top + h > vh - 8 && r.top - h - 6 > 8) top = r.top - h - 6;
    p.style.left = `${left}px`;
    p.style.top = `${Math.max(8, Math.min(top, vh - h - 8))}px`;
  }
  function open(btn, wide, bind) {
    const p = getPanel();
    p.dataset.w = typeof wide === "number" ? String(wide) : "";
    if (owner === btn) { close(); return; }
    if (owner) close();
    owner = btn;
    btn.classList.add("open");
    btn.setAttribute("aria-expanded", "true");
    try { p.hidePopover(); } catch { /* ещё не показана */ }
    const host = btn.closest("dialog[open]") || topModal() || document.body;   // внутри окна — иначе не нажимается
    if (p.parentElement !== host) host.appendChild(p);
    p.classList.toggle("wide", !!wide);
    p.innerHTML = "";
    p.onclick = null;
    try { p.showPopover(); } catch { p.hidden = false; }
    p.classList.remove("in");
    bind(p);
    place(btn);
    requestAnimationFrame(() => p.classList.add("in"));
  }
  function close() {
    if (!panel || !owner) return;
    owner.classList.remove("open");
    owner.setAttribute("aria-expanded", "false");
    const b = owner;
    owner = null;
    panel.classList.remove("in");
    try { panel.hidePopover(); } catch { panel.hidden = true; }
    if (b.__pkClose) b.__pkClose();
  }

  // Кнопка вместо поля. Повторный вызов другого вида (select → chips) заменяет прежнюю кнопку.
  function prepare(el, kind, opts) {
    el.__pkOpts = Object.assign(el.__pkOpts || {}, opts || {});   // повторный вызов (после авто-замены) дополняет настройки
    if (el.__pk && el.__pk.kind === kind) { el.__pk.show(); return null; }
    if (el.__pk) { el.__pk.ui.remove(); }
    el.dataset.pk = kind;
    el.classList.add("pk-src");
    return true;
  }
  function makeBtn(el, cls, icon) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = `pk-btn ${cls}`;
    b.setAttribute("aria-haspopup", "listbox");
    b.setAttribute("aria-expanded", "false");
    const lbl = el.getAttribute("aria-label") || (el.labels && el.labels[0] && el.labels[0].textContent.trim());
    if (lbl) b.setAttribute("aria-label", lbl);
    b.innerHTML = `${icon || ""}<span class="pk-val"></span>${CHEV}`;
    el.insertAdjacentElement("afterend", b);
    return b;
  }
  // код поменял значение / варианты / сбросил форму — кнопка показывает актуальное
  function watch(el, show) {
    const proto = el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    for (const prop of ["value", "selectedIndex"]) {
      const d = Object.getOwnPropertyDescriptor(proto, prop);
      if (!d || !d.set) continue;
      try {
        Object.defineProperty(el, prop, { configurable: true, get() { return d.get.call(this); },
          set(v) { d.set.call(this, v); queueMicrotask(show); } });
      } catch { /* старый браузер — обновится по change */ }
    }
    if (el instanceof HTMLSelectElement) new MutationObserver(() => show()).observe(el, { childList: true, subtree: true, attributes: true });
    if (el.form) el.form.addEventListener("reset", () => setTimeout(show));
    el.addEventListener("pk-refresh", show);
    el.addEventListener("change", show);
  }
  function register(el, kind, ui, show) {
    el.__pk = { kind, ui, show };
    watch(el, show);
    show();
  }

  // ─── дата ────────────────────────────────────────────────────────────
  function date(input, opts = {}) {
    if (!prepare(input, "date", opts)) return;
    opts = input.__pkOpts;
    input.required = false;
    const b = makeBtn(input, "pk-date", CAL);
    const show = () => {
      const d = parse(input.value);
      b.querySelector(".pk-val").textContent = d ? d.toLocaleDateString("ru-RU", { weekday: "short", day: "numeric", month: "long" })
        : (opts.placeholder || input.placeholder || "Выбрать дату");
      b.classList.toggle("empty", !d);
      b.disabled = input.disabled;
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
          ${opts.clearable || !input.required ? `<button type="button" class="pk-chip" data-clear>Без даты</button>` : ""}</div>
        <div class="pk-cal-h"><button type="button" class="pk-nav" data-m="-1" aria-label="Предыдущий месяц">‹</button>
          <b>${MONTHS[view.getMonth()]} ${view.getFullYear()}</b><button type="button" class="pk-nav" data-m="1" aria-label="Следующий месяц">›</button></div>
        <div class="pk-week">${["пн", "вт", "ср", "чт", "пт", "сб", "вс"].map((w) => `<span>${w}</span>`).join("")}</div>
        <div class="pk-grid">${cells}</div>`;
    };
    b.addEventListener("click", () => {
      view = null;
      open(b, false, (p) => {
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
    register(input, "date", b, show);
  }

  // ─── время ───────────────────────────────────────────────────────────
  function time(input, opts = {}) {
    if (!prepare(input, "time", opts)) return;
    opts = input.__pkOpts;
    input.required = false;
    const b = makeBtn(input, "pk-time", CLOCK);
    const show = () => {
      b.querySelector(".pk-val").textContent = input.value || opts.placeholder || input.placeholder || "Время";
      b.classList.toggle("empty", !input.value);
      b.disabled = input.disabled;
    };
    const from = opts.from ?? 7, to = opts.to ?? 22;
    b.addEventListener("click", () => {
      const [h0, m0] = (input.value || "10:00").split(":").map(Number);
      let h = h0, m = m0 - (m0 % 5);
      const render = (p) => {
        let hours = "";
        for (let x = Math.min(from, h); x <= Math.max(to, h); x++) hours += `<button type="button" class="pk-cell${x === h ? " on" : ""}" data-h="${x}">${pad(x)}</button>`;
        const mins = [0, 10, 15, 20, 30, 40, 45, 50].map((x) => `<button type="button" class="pk-cell${x === m ? " on" : ""}" data-min="${x}">:${pad(x)}</button>`).join("");
        p.innerHTML = `<p class="pk-lbl">Час</p><div class="pk-hours">${hours}</div><p class="pk-lbl">Минуты</p><div class="pk-mins">${mins}</div>
          <button type="button" class="pk-ok" data-ok>Готово · ${pad(h)}:${pad(m)}</button>`;
      };
      open(b, false, (p) => {
        render(p);
        p.onclick = (e) => {
          const t = e.target.closest("button");
          if (!t) return;
          if (t.dataset.h) h = Number(t.dataset.h);
          if (t.dataset.min) m = Number(t.dataset.min);
          input.value = `${pad(h)}:${pad(m)}`;
          show(); fire(input);
          if (t.hasAttribute("data-ok")) close(); else render(p);
        };
      });
    });
    register(input, "time", b, show);
  }

  // ─── список (один вариант) ───────────────────────────────────────────
  function optLabel(o) { return (o.dataset.label || o.textContent || "").trim(); }
  function select(sel, opts = {}) {
    if (sel.multiple) return multi(sel, opts);
    if (!prepare(sel, "select", opts)) return;
    opts = sel.__pkOpts;
    const b = makeBtn(sel, "pk-select");
    const show = () => {
      const o = sel.options[sel.selectedIndex];
      const empty = !o || (o.value === "" && !!opts.placeholder);
      b.querySelector(".pk-val").textContent = o && !empty ? optLabel(o) : (opts.placeholder || (o ? optLabel(o) : "Выбрать"));
      b.classList.toggle("empty", !o || o.value === "");
      b.disabled = sel.disabled;
    };
    b.addEventListener("click", () => {
      const all = [...sel.options].filter((o) => !o.hidden);
      const many = all.length > 8 || !!opts.remote;
      const items = (q) => {
        const ql = q.toLowerCase();
        return all.filter((o) => !q || optLabel(o).toLowerCase().includes(ql))
          .map((o) => `<button type="button" role="option" aria-selected="${o.selected}" class="pk-opt${o.selected ? " on" : ""}"${o.disabled ? " disabled" : ""}
            data-v="${esc(o.value)}"><span>${esc(optLabel(o))}</span>${o.selected ? TICK : ""}</button>`).join("")
          || `<p class="pk-empty">Ничего не нашлось</p>`;
      };
      // opts.remote(q) → [{value, label}] — ещё и поиск на сервере (например, любой объект на 1+1, не только из избранного)
      let rq = 0, rt = 0;
      const remote = (p, q) => {
        const box = p.querySelector(".pk-remote");
        if (!box) return;
        clearTimeout(rt);
        if (q.length < 2) { box.innerHTML = ""; return; }
        box.innerHTML = `<p class="pk-empty">Ищем…</p>`;
        const my = ++rq;
        rt = setTimeout(async () => {
          let res = [];
          try { res = await opts.remote(q); } catch { /* сеть */ }
          if (my !== rq) return;
          const have = new Set(all.map((o) => o.value));
          res = res.filter((r) => !have.has(String(r.value)));
          if (res.length) { const e = p.querySelector(".pk-opts:not(.pk-remote) .pk-empty"); if (e) e.remove(); }
          box.innerHTML = res.length ? `<p class="pk-lbl">${esc(opts.remoteTitle || "Ещё нашлось")}</p>` + res.map((r) =>
            `<button type="button" class="pk-opt" data-v="${esc(r.value)}" data-new="${esc(r.label)}"><span>${esc(r.label)}</span></button>`).join("") : "";
        }, 250);
      };
      open(b, true, (p) => {
        p.innerHTML = `${many ? `<input class="pk-search" placeholder="${esc(opts.searchPlaceholder || "Найти…")}" autocomplete="off" aria-label="Найти">` : ""}
          <div class="pk-opts" role="listbox">${items("")}</div>${opts.remote ? `<div class="pk-opts pk-remote"></div>` : ""}`;
        const s = p.querySelector(".pk-search");
        if (s) { s.addEventListener("input", () => { p.querySelector(".pk-opts").innerHTML = items(s.value.trim()); remote(p, s.value.trim()); }); if (innerWidth >= 640) s.focus(); }
        const on = p.querySelector(".pk-opt.on");
        if (on) on.scrollIntoView({ block: "nearest" });
        p.onclick = (e) => {
          const t = e.target.closest("[data-v]");
          if (!t || t.disabled) return;
          if (t.dataset.new && ![...sel.options].some((o) => o.value === t.dataset.v)) sel.add(new Option(t.dataset.new, t.dataset.v));
          sel.value = t.dataset.v; show(); fire(sel); close();
        };
      });
    });
    register(sel, "select", b, show);
  }

  // ─── список с галочками (несколько вариантов) ────────────────────────
  function multi(sel, opts = {}) {
    if (!prepare(sel, "multi", opts)) return;
    opts = sel.__pkOpts;
    const b = makeBtn(sel, "pk-select pk-multi");
    const show = () => {
      const on = [...sel.selectedOptions].filter((o) => o.value !== "");
      const v = b.querySelector(".pk-val");
      v.textContent = !on.length ? (opts.placeholder || sel.dataset.placeholder || "Выбрать")
        : on.length <= 2 ? on.map(optLabel).join(", ") : `${on.slice(0, 2).map(optLabel).join(", ")} +${on.length - 2}`;
      b.classList.toggle("empty", !on.length);
      b.disabled = sel.disabled;
    };
    b.addEventListener("click", () => {
      const all = [...sel.options].filter((o) => o.value !== "" && !o.hidden);
      let q = "";
      const count = () => all.filter((o) => o.selected).length;
      const list = () => {
        const ql = q.toLowerCase();
        const rows = all.filter((o) => !q || optLabel(o).toLowerCase().includes(ql));
        // выбранные — сверху, чтобы было видно, что уже отмечено
        rows.sort((a, c) => (c.selected - a.selected));
        return rows.map((o) => `<button type="button" role="option" aria-selected="${o.selected}" class="pk-opt pk-check${o.selected ? " on" : ""}"
            data-v="${esc(o.value)}"><i class="pk-box">${TICK}</i><span>${esc(optLabel(o))}</span>${o.dataset.meta ? `<small class="pk-meta">${esc(o.dataset.meta)}</small>` : ""}</button>`).join("")
          || `<p class="pk-empty">Ничего не нашлось</p>`;
      };
      const foot = () => `<button type="button" class="pk-chip" data-clear${count() ? "" : " disabled"}>Сбросить</button>
        <button type="button" class="pk-ok" data-ok>${count() ? `Готово · выбрано ${count()}` : "Готово"}</button>`;
      open(b, opts.width || true, (p) => {
        p.innerHTML = `${all.length > 8 ? `<input class="pk-search" placeholder="${esc(opts.searchPlaceholder || "Найти…")}" autocomplete="off" aria-label="Найти">` : ""}
          <div class="pk-opts" role="listbox" aria-multiselectable="true">${list()}</div><div class="pk-foot">${foot()}</div>`;
        const s = p.querySelector(".pk-search");
        if (s) { s.addEventListener("input", () => { q = s.value.trim(); p.querySelector(".pk-opts").innerHTML = list(); }); if (innerWidth >= 640) s.focus(); }
        p.onclick = (e) => {
          const t = e.target.closest("button");
          if (!t) return;
          if (t.dataset.v !== undefined) {
            const o = all.find((x) => x.value === t.dataset.v);
            if (o) o.selected = !o.selected;
            t.classList.toggle("on", o.selected);
            t.setAttribute("aria-selected", o.selected);
            p.querySelector(".pk-foot").innerHTML = foot();
            show(); fire(sel);
          } else if (t.hasAttribute("data-clear")) {
            all.forEach((o) => { o.selected = false; });
            p.querySelector(".pk-opts").innerHTML = list();
            p.querySelector(".pk-foot").innerHTML = foot();
            show(); fire(sel);
          } else if (t.hasAttribute("data-ok")) close();
        };
      });
    });
    register(sel, "multi", b, show);
  }

  // ─── варианты кнопками ───────────────────────────────────────────────
  function chips(sel) {
    if (!prepare(sel, "chips", {})) return;
    const row = document.createElement("div");
    row.className = "pk-chips";
    row.setAttribute("role", "radiogroup");
    sel.insertAdjacentElement("afterend", row);
    const render = () => {
      row.innerHTML = [...sel.options].filter((o) => !o.hidden).map((o) => `<button type="button" role="radio" aria-checked="${o.selected}"
        class="pk-chip${o.selected ? " on" : ""}" data-v="${esc(o.value)}">${esc(optLabel(o))}</button>`).join("");
    };
    row.addEventListener("click", (e) => {
      const t = e.target.closest("[data-v]");
      if (!t) return;
      if (sel.multiple) { const o = [...sel.options].find((x) => x.value === t.dataset.v); o.selected = !o.selected; } else sel.value = t.dataset.v;
      render(); fire(sel);
    });
    register(sel, "chips", row, render);
  }

  // ─── всё на странице — само ──────────────────────────────────────────
  function auto(root = document) {
    root.querySelectorAll("select:not([data-native]):not([data-pk])").forEach((s) => {
      if (s.dataset.pick === "chips") chips(s); else if (s.multiple) multi(s); else select(s);
    });
    root.querySelectorAll('input[type="date"]:not([data-native]):not([data-pk])').forEach((i) => date(i));
    root.querySelectorAll('input[type="time"]:not([data-native]):not([data-pk])').forEach((i) => time(i));
  }
  let queued = false;
  const later = () => { if (queued) return; queued = true; requestAnimationFrame(() => { queued = false; auto(); }); };
  function start() {
    auto();
    new MutationObserver((ms) => { if (ms.some((m) => m.addedNodes.length)) later(); }).observe(document.body, { childList: true, subtree: true });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();

  // ─── любое окно (<dialog>) закрывается нажатием мимо него — на всех страницах; не закрывать: data-sticky ───
  let downOnBackdrop = null;
  const outside = (d, e) => {
    const r = d.getBoundingClientRect();
    return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
  };
  document.addEventListener("pointerdown", (e) => {
    const d = e.target;
    if (location.pathname === "/") return;   // на главной окна закрываются сами, с анимацией (app.js)
    downOnBackdrop = d instanceof HTMLDialogElement && d.open && !d.hasAttribute("data-sticky") && outside(d, e) ? d : null;
  }, true);
  document.addEventListener("click", (e) => {   // и нажали, и отпустили мимо окна (не выделение текста, начатое внутри)
    const d = downOnBackdrop;
    downOnBackdrop = null;
    if (d && e.target === d && outside(d, e)) { close(); softClose(d); }
  }, true);

  // ─── 17.10 (Артём): окна на телефоне — крестик всегда, свайп вниз и плавное закрытие; тап мимо — только закрыть ───
  function swallowClick() {   // следующее нажатие (тот же тап) не должно сработать на том, что под пальцем
    const stop = (e) => { e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation(); };
    window.addEventListener("click", stop, { capture: true, once: true });
    setTimeout(() => window.removeEventListener("click", stop, { capture: true }), 600);
  }
  function softClose(d) {
    if (!d || !d.open || d.classList.contains("closing")) return;
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) { d.close(); return; }
    d.classList.add("closing");
    let done = false;
    const fin = () => { if (done) return; done = true; d.classList.remove("closing", "dragging"); d.style.transform = d.style.transition = ""; d.close(); };
    d.addEventListener("animationend", fin, { once: true });
    setTimeout(fin, 340);
  }
  window.softClose = softClose;
  const OWN = "[data-own-swipe]";   // окна главной: app.js закрывает их сам
  function hasClose(d) { return d.querySelector('[aria-label="Закрыть"], .close, [data-close], .dlg-x'); }
  function dress(d) {
    if (d.__dressed) return;
    d.__dressed = true;
    if (!d.matches(OWN)) {
      d.addEventListener("cancel", (e) => { e.preventDefault(); close(); softClose(d); });   // Esc — тоже плавно
    }
    if (!hasClose(d)) {
      const x = document.createElement("button");
      x.type = "button"; x.className = "dlg-x"; x.setAttribute("aria-label", "Закрыть");
      x.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';
      x.addEventListener("click", () => { close(); softClose(d); });
      d.prepend(x);
    }
    if (d.matches(OWN)) return;
    let y0 = null, x0 = 0, dy = 0, scroller = null;
    const scrollTop = (el) => { for (let n = el; n && n !== d.parentNode; n = n.parentElement) if (n.scrollTop > 0) return n.scrollTop; return 0; };
    d.addEventListener("touchstart", (e) => {
      y0 = null;
      if (innerWidth > 820 || e.touches.length > 1) return;
      const t = e.target;
      if (t.closest("input, textarea, select, [contenteditable], .leaflet-container, .pk-panel, [data-no-swipe]")) return;
      if (scrollTop(t) > 0) return;
      y0 = e.touches[0].clientY; x0 = e.touches[0].clientX; dy = 0; scroller = t;
    }, { passive: true });
    d.addEventListener("touchmove", (e) => {
      if (y0 === null) return;
      const ddy = e.touches[0].clientY - y0, ddx = Math.abs(e.touches[0].clientX - x0);
      if (dy === 0 && (ddy < 0 || ddx > Math.abs(ddy))) { y0 = null; return; }   // вверх или вбок — это прокрутка
      dy = Math.max(0, ddy);
      if (dy > 6) { d.classList.add("dragging"); d.style.transform = `translateY(${dy}px)`; }
    }, { passive: true });
    d.addEventListener("touchend", () => {
      if (y0 === null) return;
      y0 = null;
      d.classList.remove("dragging");
      if (dy > 100) { close(); softClose(d); return; }
      d.style.transition = "transform .3s cubic-bezier(.2,.9,.25,1)"; d.style.transform = "";
      setTimeout(() => { d.style.transition = ""; }, 320);
    });
  }
  // ─── 17.10: подсказка в поле (placeholder) не помещается — при нажатии всплывает над полем целиком ───
  let hint = null, hintFor = null, ctx = null;
  const TEXTY = /^(text|search|email|tel|url|number|password|)$/;
  function overflows(el) {
    ctx = ctx || document.createElement("canvas").getContext("2d");
    const cs = getComputedStyle(el);
    ctx.font = cs.font || `${cs.fontSize} ${cs.fontFamily}`;
    const avail = el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    return ctx.measureText(el.placeholder).width > avail - 2;
  }
  function hideHint() { if (hint) { hint.classList.remove("on"); hintFor = null; } }
  function showHint(el) {
    if (!hint) { hint = document.createElement("div"); hint.className = "ph-hint"; hint.setAttribute("aria-hidden", "true"); }
    const host = el.closest("dialog[open]") || document.body;
    if (hint.parentElement !== host) host.appendChild(hint);
    hint.textContent = el.placeholder;
    hintFor = el;
    const r = el.getBoundingClientRect(), vw = innerWidth;
    hint.style.maxWidth = `${Math.min(380, vw - 16)}px`;
    hint.classList.add("on");
    const w = hint.offsetWidth, h = hint.offsetHeight;
    hint.style.left = `${Math.max(8, Math.min(r.left, vw - w - 8))}px`;
    hint.style.top = `${r.top - h - 8 > 8 ? r.top - h - 8 : r.bottom + 8}px`;
  }
  const checkHint = (el) => {
    if (el.value || !el.placeholder || !TEXTY.test(el.type || "") || el.tagName !== "INPUT" || !overflows(el)) { if (hintFor === el) hideHint(); return; }
    showHint(el);
  };
  document.addEventListener("focusin", (e) => { if (e.target instanceof HTMLInputElement) checkHint(e.target); });
  document.addEventListener("input", (e) => { if (e.target === hintFor) checkHint(e.target); });
  document.addEventListener("focusout", (e) => { if (e.target === hintFor) hideHint(); });
  document.addEventListener("scroll", () => { if (hintFor) showHint(hintFor); }, true);

  // поле текста [data-grow] растёт под весь текст — без внутренней прокрутки (подборки: текст для клиента)
  const grow = (t) => { if (!t.getClientRects().length) return; t.style.height = "auto"; t.style.height = `${t.scrollHeight + 2}px`; };
  document.addEventListener("input", (e) => { if (e.target.matches && e.target.matches("textarea[data-grow]")) grow(e.target); });
  window.addEventListener("resize", () => document.querySelectorAll("textarea[data-grow]").forEach(grow));
  const dressAll = () => { document.querySelectorAll("dialog").forEach(dress);
    requestAnimationFrame(() => document.querySelectorAll("textarea[data-grow]").forEach(grow)); };
  let dq = false;   // не чаще раза за кадр (карта добавляет сотни меток)
  const dressSoon = () => { if (dq) return; dq = true; requestAnimationFrame(() => { dq = false; dressAll(); }); };
  new MutationObserver((ms) => { if (ms.some((m) => m.type === "attributes" || m.addedNodes.length)) dressSoon(); })
    .observe(document.documentElement, { subtree: true, childList: true, attributes: true, attributeFilter: ["open"] });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", dressAll); else dressAll();

  // программно поменяли значение исходного поля — обновить кнопку: Pick.refresh(form)
  const refresh = (root) => root.querySelectorAll("[data-pk]").forEach((el) => el.dispatchEvent(new Event("pk-refresh")));
  window.Pick = { date, time, select, multi, chips, refresh, close, auto, softClose };
})();
