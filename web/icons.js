/* Единые тонкие иконки вместо смайликов.
   Любой смайлик интерфейса (в разметке, в тексте от сервера, в уведомлениях) заменяется на линейную иконку
   из /static/icons.svg. Тексты объявлений агентов (описание, исходное сообщение) не трогаем.
   window.ic("calendar") — иконка для шаблонов. */
(() => {
  "use strict";
  popoverFill();
  const SPRITE = "/static/icons.svg?v=7";
  // согласие на cookie — на всех страницах (icons.js подключён везде первым), см. cookies.js
  if (!document.querySelector('script[src*="cookies.js"]')) {
    const ck = document.createElement("script");
    ck.src = "/static/cookies.js?v=3"; ck.defer = true;
    document.head.appendChild(ck);
  }
  const MAP = {
    "📅": "calendar", "🗓": "calendar", "🔔": "bell", "📁": "folder", "📂": "folder", "📍": "pin", "🗑": "trash",
    "🔥": "flame", "⚖": "scale", "📞": "phone", "☎": "phone", "✅": "check-circle", "❌": "x-circle", "♡": "heart",
    "♥": "heart", "❤": "heart", "⚙": "sliders", "🚫": "ban", "🙋": "users", "👥": "users", "⚠": "alert",
    "👀": "eye", "👁": "eye", "🤔": "hourglass", "⏳": "hourglass", "📊": "chart", "✏": "edit", "✎": "edit",
    "🔗": "link", "📈": "trend", "📉": "trend-down", "🏠": "home", "🏡": "home", "🤝": "briefcase", "🎁": "gift",
    "⬇": "download", "💾": "save", "🗺": "map", "➕": "plus", "➖": "minus", "🔁": "repeat", "📝": "note",
    "🕓": "history", "🕒": "clock", "⏰": "clock", "📱": "device", "📲": "device", "🏷": "tag", "🎟": "ticket",
    "❓": "help", "📌": "pushpin", "🎉": "sparkle", "✨": "sparkle", "🔎": "search", "🔍": "search", "⇄": "swap",
    "↔": "swap", "★": "star", "⭐": "star", "👤": "user", "🏷️": "tag",
    "✓": "check", "✔": "check", "✕": "x", "✖": "x",
  };
  const keys = Object.keys(MAP).sort((a, b) => b.length - a.length).map((k) => k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const RE = new RegExp(`(${keys.join("|")})\\uFE0F?( ?)`, "gu");
  // где смайлики — часть чужого текста (объявления агентов) или поле ввода — не меняем
  const SKIP_TAGS = new Set(["SCRIPT", "STYLE", "TEXTAREA", "INPUT", "SELECT", "OPTION", "CODE", "PRE", "svg", "SVG", "NOSCRIPT"]);
  const SKIP_SEL = ".dv-desc, .d-source, .item-desc, .pk-desc, .rq-text, .fx-text, .d-desc, .pl-note, .dv-list p, [data-raw]";

  const ic = (name, cls = "") => `<svg class="ic${cls ? " " + cls : ""}" aria-hidden="true"><use href="${SPRITE}#i-${name}"></use></svg>`;
  window.ic = ic;

  function icNode(name, gap) {
    const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("class", gap ? "ic ic-l" : "ic");
    s.setAttribute("aria-hidden", "true");
    const u = document.createElementNS("http://www.w3.org/2000/svg", "use");
    u.setAttribute("href", `${SPRITE}#i-${name}`);
    s.appendChild(u);
    return s;
  }

  function swapText(node) {
    const t = node.nodeValue;
    RE.lastIndex = 0;
    if (!RE.test(t)) return;
    const p = node.parentElement;
    if (!p || SKIP_TAGS.has(p.tagName) || p.closest(SKIP_SEL)) return;
    RE.lastIndex = 0;
    const frag = document.createDocumentFragment();
    let last = 0, m;
    while ((m = RE.exec(t))) {
      if (m.index > last) frag.appendChild(document.createTextNode(t.slice(last, m.index)));
      frag.appendChild(icNode(MAP[m[1]], !!m[2] || m.index + m[0].length < t.length));
      last = m.index + m[0].length;
    }
    if (last < t.length) frag.appendChild(document.createTextNode(t.slice(last)));
    node.replaceWith(frag);
  }
  const strip = (s) => s.replace(RE, "").replace(/\s{2,}/g, " ").trim();

  function scan(root) {
    if (!root) return;
    if (root.nodeType === 3) { swapText(root); return; }
    if (root.nodeType !== 1) return;
    if (root.tagName === "OPTION") { RE.lastIndex = 0; if (RE.test(root.textContent)) root.textContent = strip(root.textContent); return; }
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (w.nextNode()) nodes.push(w.currentNode);
    nodes.forEach(swapText);
    root.querySelectorAll && root.querySelectorAll("option, [title], [placeholder], [aria-label]").forEach((el) => {
      if (el.tagName === "OPTION") { RE.lastIndex = 0; if (RE.test(el.textContent)) el.textContent = strip(el.textContent); }
      for (const a of ["title", "placeholder", "aria-label"]) {
        const v = el.getAttribute(a);
        RE.lastIndex = 0;
        if (v && RE.test(v)) el.setAttribute(a, strip(v));
      }
    });
  }

  let queued = new Set(), raf = 0;
  const flush = () => { raf = 0; const q = queued; queued = new Set(); q.forEach(scan); };
  new MutationObserver((list) => {
    for (const m of list) {
      if (m.type === "characterData") queued.add(m.target);
      else m.addedNodes.forEach((n) => queued.add(n));
    }
    if (!raf) raf = requestAnimationFrame(flush);
  }).observe(document.documentElement, { childList: true, subtree: true, characterData: true });

  // 02.11: старые браузеры без «верхнего слоя» (Popover API: Chrome < 114, браузер Xiaomi и т. п.). Без этого шторка
  // «Все разделы» висела всегда и не закрывалась, строка поиска лезла поверх неё, а выпадающие списки фильтров
  // открывались под шторкой — ничего не нажималось. Здесь — те же методы, класс pp-open вместо :popover-open и
  // правила стилей с :popover-open, переписанные под этот класс.
  function popoverFill() {
    const test = /[?&]nopopover=1\b/.test(location.search);   // проверить на новом браузере, как увидит старый
    if (typeof HTMLElement === "undefined" || (typeof HTMLElement.prototype.showPopover === "function" && !test)) return;
    const OPEN = "pp-open";
    let z = 2147480000;
    const base = document.createElement("style");
    base.textContent = "[popover]:not(.pp-open):not(dialog[open]){display:none!important}"
      + ":where([popover]){position:fixed;inset:0;width:fit-content;height:fit-content;margin:auto;border:solid;padding:.25em;"
      + "overflow:auto;color:CanvasText;background-color:Canvas}:where([popover].pp-open){display:block}";
    (document.head || document.documentElement).appendChild(base);
    const P = HTMLElement.prototype;
    P.showPopover = function () {
      if (!this.hasAttribute("popover")) throw new DOMException("Not a popover", "NotSupportedError");
      if (this.classList.contains(OPEN)) return;
      this.classList.add(OPEN);
      this.style.setProperty("z-index", String(++z), "important");   // открытый позже — выше
    };
    P.hidePopover = function () { this.classList.remove(OPEN); this.style.removeProperty("z-index"); };
    P.togglePopover = function (force) {
      const on = force === undefined ? !this.classList.contains(OPEN) : !!force;
      if (on) this.showPopover(); else this.hidePopover();
      return on;
    };
    // el.matches(":popover-open") и т. п. — понимать как класс
    const fix = (s) => (typeof s === "string" && s.includes(":popover-open") ? s.replace(/:popover-open/g, "." + OPEN) : s);
    [[Element.prototype, ["matches", "closest", "querySelector", "querySelectorAll"]],
      [Document.prototype, ["querySelector", "querySelectorAll"]],
      [DocumentFragment.prototype, ["querySelector", "querySelectorAll"]]].forEach(([proto, names]) => names.forEach((n) => {
      const f = proto[n];
      if (f) proto[n] = function (sel) { return f.call(this, fix(sel)); };
    }));
    // правила с :popover-open браузер выбросил при разборе — берём их из файла стилей и добавляем сразу после него
    const pick = (css) => {
      let out = "", i = 0;
      while (i < css.length) {
        const ob = css.indexOf("{", i);
        if (ob < 0) break;
        const semi = css.indexOf(";", i);
        if (semi >= 0 && semi < ob) { i = semi + 1; continue; }   // @import, @layer a; …
        let d = 1, j = ob + 1;
        while (j < css.length && d) { const c = css[j]; if (c === "{") d++; else if (c === "}") d--; j++; }
        const pre = css.slice(i, ob).trim(), body = css.slice(ob + 1, j - 1);
        if (pre[0] === "@") {
          if (/^@(media|supports|layer)\b/i.test(pre)) { const inner = pick(body); if (inner) out += `${pre}{${inner}}`; }
        } else if (pre.includes(":popover-open")) out += `${fix(pre)}{${body}}`;
        i = j;
      }
      return out;
    };
    const load = () => document.querySelectorAll('link[rel="stylesheet"]').forEach((link) => {
      if (link.__pp || !link.href || new URL(link.href, location.href).origin !== location.origin) return;
      link.__pp = true;
      fetch(link.href, { credentials: "same-origin" }).then((r) => (r.ok ? r.text() : "")).then((css) => {
        const rules = pick(css.replace(/\/\*[\s\S]*?\*\//g, ""));
        if (!rules) return;
        const st = document.createElement("style");
        st.textContent = rules;
        link.after(st);
      }).catch(() => { /* без этих правил — просто проще вид */ });
    });
    load();
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", load);
  }

  const start = () => { scan(document.body); RE.lastIndex = 0; if (RE.test(document.title)) document.title = strip(document.title); };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
