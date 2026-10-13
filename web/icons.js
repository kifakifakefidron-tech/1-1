/* 1+1 · единые тонкие иконки вместо смайликов.
   Любой смайлик интерфейса (в разметке, в тексте от сервера, в уведомлениях) заменяется на линейную иконку
   из /static/icons.svg. Тексты объявлений агентов (описание, исходное сообщение) не трогаем.
   window.ic("calendar") — иконка для шаблонов. */
(() => {
  "use strict";
  const SPRITE = "/static/icons.svg?v=4";
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

  const start = () => { scan(document.body); RE.lastIndex = 0; if (RE.test(document.title)) document.title = strip(document.title); };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
