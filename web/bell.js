/* Колокольчик в шапке (число непрочитанных) и маленькая плашка о новом уведомлении — на всех страницах.
   API: /api/notices/peek (лёгкий), отметить прочитанным — POST /api/notices {id}. */
(() => {
  "use strict";
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 16V11a6 6 0 1 1 12 0v5l1.5 2h-15z"/><path d="M10 20a2 2 0 0 0 4 0"/></svg>';
  if (new URLSearchParams(location.search).has("embed")) return;   // окно объекта поверх другой страницы — без плашек
  const onNotices = location.pathname === "/notifications";
  let bell = document.querySelector("[data-bell]");
  let known = null;   // id самого свежего уведомления на момент загрузки страницы

  function ensureBell() {
    if (bell) return bell;
    const nav = document.querySelector(".nav-in");
    if (!nav) return null;
    bell = document.createElement("a");
    bell.className = "bell";
    bell.href = "/notifications";
    bell.dataset.bell = "";
    bell.hidden = true;
    bell.setAttribute("aria-label", "Уведомления");
    bell.innerHTML = `${ICON}<b class="bell-count" hidden></b>`;
    nav.appendChild(bell);
    return bell;
  }

  function setCount(n) {
    window.__bellN = n;
    if (window.railBell) window.railBell(n);
    const b = ensureBell();
    if (!b) return;
    b.hidden = false;
    const c = b.querySelector(".bell-count");
    c.hidden = !n;
    c.textContent = n > 99 ? "99+" : n;
    b.classList.toggle("has", !!n);
    b.setAttribute("aria-label", n ? `Уведомления: ${n} новых` : "Уведомления");
  }
  window.bellSet = setCount;

  const shown = () => { try { return JSON.parse(localStorage.getItem("bellShown") || "[]"); } catch { return []; } };
  const remember = (id) => { try { localStorage.setItem("bellShown", JSON.stringify([id, ...shown()].slice(0, 50))); } catch { /* */ } };

  function pop(n) {
    let box = document.getElementById("bellPop");
    if (!box) {
      box = document.createElement("div");
      box.id = "bellPop";
      box.className = "bell-pop";
      box.setAttribute("role", "status");
      document.body.appendChild(box);
    }
    box.innerHTML = `<span class="bell-pop-ic">${esc(n.icon || "🔔")}</span>
      <div class="bell-pop-txt"><b>${esc(n.title)}</b>${n.body ? `<small>${esc(n.body)}</small>` : ""}</div>
      <div class="bell-pop-acts">${n.url ? `<a class="bell-pop-go" href="${esc(n.url)}">Открыть</a>` : `<a class="bell-pop-go" href="/notifications">Открыть</a>`}
        <button type="button" class="bell-pop-x" aria-label="Закрыть">✕</button></div>`;
    box.classList.remove("out");
    requestAnimationFrame(() => box.classList.add("in"));
    const close = () => { box.classList.add("out"); box.classList.remove("in"); };
    let t = setTimeout(close, 15000);
    box.onmouseenter = () => clearTimeout(t);
    box.onmouseleave = () => { t = setTimeout(close, 6000); };
    box.querySelector(".bell-pop-x").onclick = close;
    box.querySelector(".bell-pop-go").onclick = () => {
      fetch("/api/notices", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: n.id }) }).catch(() => {});
    };
    remember(n.id);
  }

  async function peek() {
    let d;
    try {
      const r = await fetch("/api/notices/peek", { credentials: "same-origin" });
      if (r.status === 401) { if (bell) bell.hidden = true; return; }
      if (!r.ok) return;
      d = await r.json();
    } catch { return; }
    setCount(d.unread);
    const latest = d.latest || [];
    if (known === null) { known = latest.length ? latest[0].id : 0; }
    // новое с момента открытия страницы (или пропущенное, пока сайт был закрыт, — за последние 6 часов)
    const fresh = latest.find((n) => !shown().includes(n.id) && (n.id > known || Date.now() / 1000 - n.ts < 6 * 3600));
    if (fresh && !onNotices) pop(fresh);
    if (latest.length) known = Math.max(known, latest[0].id);
  }

  peek();
  setInterval(() => { if (!document.hidden) peek(); }, 45000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) peek(); });
  window.bellPeek = peek;
})();
