/* «Как это работает»: живые цифры (/api/stats), графики рынка (/api/market), появление блоков при прокрутке. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;

  function countUp(el, to) {
    if (reduce || to < 10) { el.textContent = to.toLocaleString("ru-RU"); return; }
    const t0 = performance.now(), dur = 1100;
    const step = (t) => {
      const k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 3);
      el.textContent = Math.round(to * e).toLocaleString("ru-RU");
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  async function live() {
    try {
      const s = await (await fetch("/api/stats")).json();
      const box = $("hwLive");
      box.querySelectorAll("[data-k]").forEach((b) => {
        const v = Number(s[b.dataset.k]) || 0;
        if (!v) { b.parentElement.remove(); return; }   // нули не показываем
        countUp(b, v);
      });
      if (!box.children.length) box.hidden = true;
    } catch { $("hwLive").hidden = true; }
  }

  async function market() {
    let m;
    try { m = await (await fetch("/api/market")).json(); } catch { return; }
    const rooms = ((m.city || {}).rooms || []).filter((r) => r.median);
    const ds = (m.districts || []).filter((d) => d.median).sort((a, b) => b.median - a.median).slice(0, 6);
    if (rooms.length < 2 && ds.length < 2) return;
    $("hwMarket").hidden = false;
    const k = (v) => Math.round(v / 1000);
    const maxR = Math.max(...rooms.map((r) => r.median)), minR = Math.min(...rooms.map((r) => r.median));
    // столбики: от 35 % до 100 %, чтобы разница была видна, но маленькие не исчезали
    $("hwRooms").innerHTML = rooms.map((r, i) => `<div class="hw-col" style="--h:${35 + Math.round((r.median - minR) / Math.max(1, maxR - minR) * 65)}%;--i:${i}">
        <b>${k(r.median)}</b><i></i><span>${esc(r.label.replace("-комнатные", "-к").replace(" комнат", ""))}</span><small>${r.n} об.</small></div>`).join("");
    const maxD = Math.max(...ds.map((d) => d.median));
    $("hwDistr").innerHTML = ds.map((d, i) => `<div class="hw-bar" style="--w:${Math.round(d.median / maxD * 100)}%;--i:${i}">
        <span>${esc(d.name)}</span><i></i><b>${k(d.median)}</b></div>`).join("");
  }

  // блоки появляются при прокрутке (графики «растут» в этот момент)
  if ("IntersectionObserver" in window && !reduce) {
    const io = new IntersectionObserver((es) => es.forEach((e) => {
      if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
    }), { threshold: .15 });
    document.querySelectorAll(".hw-sec, .hw-final").forEach((s) => { s.classList.add("rv"); io.observe(s); });
  }

  // 19.10: инструкция — ссылка #g-… раскрывает свой раздел; «Раскрыть всё / Свернуть всё»
  function openFromHash() {
    const id = decodeURIComponent(location.hash.slice(1));
    const d = id && document.getElementById(id);
    if (d && d.matches("details.hw-g")) {
      d.open = true;
      d.closest(".rv")?.classList.add("in");
      requestAnimationFrame(() => d.scrollIntoView({ block: "start", behavior: reduce ? "auto" : "smooth" }));
    }
  }
  addEventListener("hashchange", openFromHash);
  document.querySelectorAll(".hw-toc a").forEach((a) => a.addEventListener("click", () => setTimeout(openFromHash, 0)));   // тот же раздел второй раз — hashchange не придёт
  openFromHash();
  const all = $("hwAll");
  if (all) all.addEventListener("click", () => {
    const gs = [...document.querySelectorAll("details.hw-g")];
    const open = !gs.every((g) => g.open);
    gs.forEach((g) => { g.open = open; });
    all.textContent = open ? "Свернуть всё" : "Раскрыть всё";
  });

  live();
  market();
})();
