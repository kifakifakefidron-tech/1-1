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
    box.querySelector(".lb-stage").addEventListener("click", (e) => { if (e.target.classList.contains("lb-stage")) close(); });
    box.querySelector(".lb-thumbs").addEventListener("click", (e) => {
      const t = e.target.closest("[data-i]");
      if (t) show(Number(t.dataset.i));
    });
    const stage = box.querySelector(".lb-stage");
    stage.addEventListener("touchstart", (e) => { x0 = e.touches[0].clientX; }, { passive: true });
    stage.addEventListener("touchend", (e) => {
      if (x0 == null) return;
      const dx = e.changedTouches[0].clientX - x0;
      x0 = null;
      if (Math.abs(dx) > 45) go(dx < 0 ? 1 : -1);
    });
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
    box.querySelector(".lb-thumbs").innerHTML = urls.length > 1
      ? urls.map((u, k) => `<button type="button" data-i="${k}" aria-label="Фото ${k + 1}"><img src="${u.replace(/"/g, "&quot;")}" alt="" loading="lazy"></button>`).join("") : "";
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
