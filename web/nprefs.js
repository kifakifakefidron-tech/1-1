/* 10.11 (Артём: «в настройках и во вкладке уведомлений можно выключить уведомления»): какие уведомления присылать.
   Один блок на двух страницах — /settings и /notifications. Выключенный вид не приходит ни на сайт, ни письмом
   (сервер: notices.is_off, users.settings.notify_off; "all" — все сразу). Письма на почту — отдельным переключателем (mail_off).
   NPrefs.mount(el, { kinds, off, mailOff, email }) */
(() => {
  "use strict";
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  async function post(patch) {
    for (let i = 0; i < 3; i++) {
      try {
        const r = await fetch("/api/settings", { method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) });
        if (r.ok) return true;
        if (r.status !== 503) return false;
      } catch { /* сеть — ещё раз */ }
      await new Promise((res) => setTimeout(res, 600 * (i + 1)));
    }
    return false;
  }

  function mount(el, opts) {
    if (!el) return;
    const kinds = opts.kinds || [];
    let off = new Set(opts.off || []), mailOff = !!opts.mailOff;
    const row = (key, title, hint, on, cls = "") => `<label class="np-row${cls}">
      <span class="np-t"><b>${esc(title)}</b>${hint ? `<small>${esc(hint)}</small>` : ""}</span>
      <input type="checkbox" class="np-sw" role="switch" data-np="${esc(key)}"${on ? " checked" : ""}></label>`;
    function draw() {
      const all = off.has("all");
      el.innerHTML = `<div class="np${all ? " all-off" : ""}">
        ${row("all", "Уведомления", all ? "Сейчас выключены все — на сайте и на почте" : "Включены — ниже можно выключить отдельные", !all, " main")}
        <div class="np-list">${kinds.map((k) => row(k.kind, k.title, k.hint, !all && !off.has(k.kind))).join("")}</div>
        ${opts.email ? row("mail", "Дублировать на почту", `Важное придёт и письмом на ${opts.email}. Коды для входа приходят всегда.`, !mailOff, " mail")
          : `<p class="np-note">Почта не привязана — уведомления приходят только на сайте.</p>`}
        <p class="np-saved" aria-live="polite"></p></div>`;
    }
    let hideT = 0;
    function saved(ok) {
      const p = el.querySelector(".np-saved");
      if (!p) return;
      p.textContent = ok ? "Сохранено" : "Не получилось сохранить — проверьте интернет";
      p.classList.toggle("bad", !ok);
      p.classList.add("on");
      clearTimeout(hideT);
      hideT = setTimeout(() => p.classList.remove("on"), 2200);
    }
    el.addEventListener("change", async (e) => {
      const sw = e.target.closest("[data-np]");
      if (!sw) return;
      const key = sw.dataset.np;
      let patch;
      if (key === "mail") {
        mailOff = !sw.checked;
        patch = { mail_off: mailOff };
      } else {
        if (key === "all") {
          if (sw.checked) off.delete("all"); else off.add("all");
        } else {
          if (off.has("all")) {   // включили один вид при выключенных всех — остальные остаются выключенными
            off = new Set(kinds.map((k) => k.kind));
            off.delete(key);
          } else if (sw.checked) off.delete(key); else off.add(key);
          if (kinds.every((k) => off.has(k.kind))) off = new Set(["all"]);
        }
        patch = { notify_off: [...off] };
        draw();
      }
      saved(await post(patch));
      if (opts.onChange) opts.onChange({ off: [...off], mailOff });
    });
    draw();
  }
  window.NPrefs = { mount };
})();
