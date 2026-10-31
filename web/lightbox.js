/* 1+1 · просмотр фото на странице (вместо новых вкладок): фото целиком (без обрезки), стрелки, свайп, клавиши ←/→/Esc,
   счётчик и полоса миниатюр. Подключение: элементы с data-lb="группа" и data-src="ссылка" (или <a href> внутри группы);
   или из кода — window.Lightbox.open([ссылки], номер). */
(() => {
  "use strict";
  let box = null, list = [], idx = 0, x0 = null;

  function build() {
    box = document.createElement("div");
    box.className = "lb";
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-modal", "true");
    box.setAttribute("aria-label", "Просмотр фото");
    box.setAttribute("popover", "manual");   // верхний слой — поверх открытого окна объекта (dialog)
    box.innerHTML = `<div class="lb-top"><span class="lb-count"></span>
        <button type="button" class="lb-x" aria-label="Закрыть"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div>
      <div class="lb-stage"><img class="lb-img" alt=""><div class="lb-spin" aria-hidden="true"></div></div>
      <button type="button" class="lb-nav prev" aria-label="Предыдущее фото"><svg viewBox="0 0 24 24"><path d="M15 5l-7 7 7 7"/></svg></button>
      <button type="button" class="lb-nav next" aria-label="Следующее фото"><svg viewBox="0 0 24 24"><path d="M9 5l7 7-7 7"/></svg></button>
      <div class="lb-thumbs"></div>`;
    document.body.appendChild(box);
    box.querySelector(".lb-x").addEventListener("click", close);
    box.querySelector(".prev").addEventListener("click", (e) => { e.stopPropagation(); go(-1); });
    box.querySelector(".next").addEventListener("click", (e) => { e.stopPropagation(); go(1); });
    // 31.10: нажатие по любому тёмному месту (вокруг фото, полоса сверху, пустое место у миниатюр) — закрыть
    box.addEventListener("click", (e) => { if (!e.target.closest(".lb-img, .lb-nav, .lb-thumbs button, .lb-x, .lb-count")) close(); });
    box.querySelector(".lb-thumbs").addEventListener("click", (e) => {
      const t = e.target.closest("[data-i]");
      if (t) show(Number(t.dataset.i));
    });
    // 31.10: палец — вбок листает, вниз (или вверх) закрывает; фото едет за пальцем. Жесты не уходят окну объекта под фото
    // (иначе оно тоже тянулось вниз и закрывалось) и не листают страницу
    const stage = box.querySelector(".lb-stage"), img = box.querySelector(".lb-img");
    let y0 = null, dx = 0, dy = 0, axis = "";
    stage.addEventListener("touchstart", (e) => {
      e.stopPropagation();
      if (e.touches.length > 1) { x0 = y0 = null; return; }
      x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; dx = dy = 0; axis = "";
    }, { passive: true });
    stage.addEventListener("touchmove", (e) => {
      e.stopPropagation();
      if (x0 == null) return;
      e.preventDefault();
      dx = e.touches[0].clientX - x0; dy = e.touches[0].clientY - y0;
      if (!axis && Math.hypot(dx, dy) > 8) axis = Math.abs(dx) > Math.abs(dy) ? "x" : "y";
      box.classList.add("drag");
      if (axis === "x") img.style.transform = `translateX(${dx}px)`;
      else if (axis === "y") { img.style.transform = `translateY(${dy}px) scale(${1 - Math.min(Math.abs(dy), 300) / 1500})`;
        box.style.background = `rgba(1, 22, 22, ${0.9 - Math.min(Math.abs(dy), 300) / 500})`; }
    }, { passive: false });
    stage.addEventListener("touchend", (e) => {
      e.stopPropagation();
      if (x0 == null) return;
      x0 = y0 = null;
      box.classList.remove("drag");
      img.style.transform = ""; box.style.background = "";
      if (axis === "y" && Math.abs(dy) > 90) close();
      else if (axis === "x" && Math.abs(dx) > 45) go(dx < 0 ? 1 : -1);
    });
    box.addEventListener("touchmove", (e) => { if (!e.target.closest(".lb-thumbs")) e.preventDefault(); }, { passive: false });
    document.addEventListener("keydown", (e) => {
      if (!box.classList.contains("on")) return;
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
      else if (e.key === "ArrowRight") go(1);
      else if (e.key === "ArrowLeft") go(-1);
    }, true);
  }
  function show(i) {
    idx = (i + list.length) % list.length;
    const img = box.querySelector(".lb-img");
    box.classList.add("loading");
    img.onload = () => box.classList.remove("loading");
    img.onerror = () => box.classList.remove("loading");
    img.src = list[idx];
    box.querySelector(".lb-count").textContent = list.length > 1 ? `${idx + 1} / ${list.length}` : "";
    box.querySelectorAll(".lb-thumbs [data-i]").forEach((t) => t.classList.toggle("on", Number(t.dataset.i) === idx));
    const on = box.querySelector(".lb-thumbs .on");
    if (on) on.scrollIntoView({ block: "nearest", inline: "center", behavior: "smooth" });
    [idx + 1, idx - 1].forEach((j) => { if (list[(j + list.length) % list.length]) new Image().src = list[(j + list.length) % list.length]; });
  }
  function go(d) { if (list.length > 1) show(idx + d); }
  function open(urls, i = 0) {
    if (!urls || !urls.length) return;
    if (!box) build();
    list = urls;
    box.classList.toggle("single", urls.length < 2);
    box.querySelector(".lb-hint")?.remove();
    box.insertAdjacentHTML("beforeend", `<span class="lb-hint">${urls.length > 1 ? "Листайте вбок · смахните вниз, чтобы закрыть" : "Смахните вниз, чтобы закрыть"}</span>`);
    box.querySelector(".lb-thumbs").innerHTML = urls.length > 1
      ? urls.map((u, k) => `<button type="button" data-i="${k}" aria-label="Фото ${k + 1}"><img src="${u.replace(/"/g, "&quot;")}" alt="" loading="lazy"></button>`).join("") : "";
    // внутри открытого окна (dialog) — иначе браузер не даёт нажимать стрелки и крестик
    const host = [...document.querySelectorAll("dialog[open]")].filter((d) => { try { return d.matches(":modal"); } catch { return true; } }).pop() || document.body;
    if (box.parentElement !== host) { try { box.hidePopover(); } catch { /* */ } host.appendChild(box); }
    try { box.showPopover(); } catch { /* старый браузер — просто поверх страницы */ }
    box.classList.add("on");
    document.documentElement.classList.add("lb-open");
    show(i);
  }
  function close() {
    if (!box) return;
    box.classList.remove("on");
    document.documentElement.classList.remove("lb-open");
    try { box.hidePopover(); } catch { /* */ }
  }
  // Любые фото в группе data-lb открываются здесь, а не в новой вкладке
  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-lb] a[href], [data-lb] [data-src]");
    if (!el) return;
    const group = el.closest("[data-lb]");
    const items = [...group.querySelectorAll("a[href], [data-src]")];
    const urls = items.map((a) => a.dataset.src || a.getAttribute("href"));
    e.preventDefault();
    e.stopPropagation();
    open(urls, Math.max(0, items.indexOf(el)));
  }, true);
  window.Lightbox = { open, close };
})();
