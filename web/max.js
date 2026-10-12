/* 1+1 · связь через MAX (WhatsApp и Telegram на сайте не используем).
   У MAX нет ссылки «открыть чат по номеру», поэтому кнопка [data-max="+7…"] копирует номер и подсказывает,
   что найти человека можно в MAX поиском по номеру. [data-share-text] — «Поделиться»: на телефоне системное меню
   (там и MAX), на компьютере — копируем текст. */
(() => {
  "use strict";
  let tip = null, timer = 0;
  function say(html) {
    if (!tip) {
      tip = document.createElement("div");
      tip.className = "max-tip";
      tip.setAttribute("popover", "manual");
      tip.setAttribute("role", "status");
      document.body.appendChild(tip);
    }
    tip.innerHTML = html;
    try { tip.hidePopover(); tip.showPopover(); } catch { tip.hidden = false; }
    tip.classList.add("on");
    clearTimeout(timer);
    timer = setTimeout(() => { tip.classList.remove("on"); setTimeout(() => { try { tip.hidePopover(); } catch { tip.hidden = true; } }, 250); }, 3600);
  }
  async function copy(text) {
    try { await navigator.clipboard.writeText(text); return true; } catch { /* старый браузер */ }
    const ta = document.createElement("textarea");
    ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch { /* */ }
    ta.remove();
    return ok;
  }
  document.addEventListener("click", async (e) => {
    const m = e.target.closest("[data-max]");
    if (m) {
      e.preventDefault();
      e.stopPropagation();
      const phone = m.dataset.max;
      await copy(phone);
      say(`<b>Номер скопирован:</b> ${phone}<br>Откройте MAX → поиск → вставьте номер и напишите.`);
      return;
    }
    const s = e.target.closest("[data-share-text]");
    if (s) {
      e.preventDefault();
      const text = s.dataset.shareText;
      if (navigator.share && /Android|iPhone|iPad|Mobile/i.test(navigator.userAgent)) {
        try { await navigator.share({ text }); return; } catch { /* отменили */ return; }
      }
      await copy(text);
      say("<b>Скопировано.</b> Вставьте в MAX или куда удобно.");
    }
  }, true);
  window.copyText = copy;
})();
