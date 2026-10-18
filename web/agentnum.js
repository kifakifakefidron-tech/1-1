/* «Ваш номер на 1+1» (/agent, 19.10): агент подтверждает номер (звонок / код в MAX — web/verify.js) и отдельными галочками
   даёт согласие на показ номера и имени (/consent-publish) и разрешение на тексты и фото (/license); выбирает, показывать ли
   имя и кнопку MAX; отзывает одной кнопкой. API: GET/POST /api/agent/consent. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const ic = (n) => (window.ic ? window.ic(n) : "");
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pretty = (p) => { const d = String(p).replace(/\D/g, ""); return d.length === 11 ? `+7 ${d.slice(1, 4)} ${d.slice(4, 7)}-${d.slice(7, 9)}-${d.slice(9)}` : p; };
  const date = (ts) => new Date(ts * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" });
  async function call(url, body) {
    const opts = { credentials: "same-origin", method: body !== undefined ? "POST" : "GET" };
    if (body !== undefined) { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(body); }
    const r = await fetch(url, opts);
    let d = {};
    try { d = await r.json(); } catch { /* */ }
    return { ok: r.ok, data: d };
  }
  function toast(text, ms = 3200) {
    const t = $("toast");
    t.textContent = text;
    try { t.showPopover(); } catch { t.hidden = false; }
    clearTimeout(toast.t);
    toast.t = setTimeout(() => { try { t.hidePopover(); } catch { t.hidden = true; } }, ms);
  }
  document.querySelectorAll("[data-ic]").forEach((el) => { el.outerHTML = ic(el.dataset.ic); });

  let data = null;
  function phoneCard(x) {
    const c = x.consent || {};
    const given = !!x.consent;
    const state = x.hidden ? `<span class="an-st off">Номер скрыт на сайте</span>`
      : given && c.publish ? `<span class="an-st on">${ic("check-circle")}Показывается с вашего согласия</span>`
        : `<span class="an-st">Показывается из объявлений в чатах — согласие ещё не дано</span>`;
    return `<section class="st-card an-phone" data-phone="${esc(x.phone)}">
      <div class="an-ph-h"><div><b class="an-num">${esc(pretty(x.phone))}</b>${state}</div>
        <a class="chip" href="/my">${ic("home-edit")}Мои объекты · ${x.listings}</a></div>
      ${given ? `<p class="an-when">Последнее решение — ${esc(date(c.ts))} (документы от ${esc(c.version || "")})</p>` : ""}
      <div class="an-checks">
        <label class="consent-chk"><input type="checkbox" data-k="publish"${given && c.publish ? " checked" : ""}>
          <span>Даю согласие на <a href="/consent-publish" target="_blank">распространение моих персональных данных</a> — имени и номера
            телефона в карточках моих объявлений на 1+1, только вошедшим пользователям с доступом. Отозвать могу в любой момент.</span></label>
        <label class="consent-chk"><input type="checkbox" data-k="license"${given && c.license ? " checked" : ""}>
          <span>Разрешаю <a href="/license" target="_blank">использовать тексты и фото моих объявлений</a> — в карточках, превью
            ссылок и подборках для клиентов коллег.</span></label>
      </div>
      <div class="an-opts">
        <label class="an-sw"><input type="checkbox" data-k="show_name"${!given || c.show_name ? " checked" : ""}><span></span>Показывать моё имя</label>
        <label class="an-sw"><input type="checkbox" data-k="allow_max"${!given || c.allow_max ? " checked" : ""}><span></span>Кнопка «Написать в MAX»</label>
      </div>
      <p class="an-hint">Без первой галочки номер будет <b>скрыт</b> во всех ваших объявлениях — это тоже честный выбор.</p>
      <div class="st-acts">
        <button type="button" class="pill" data-save>Сохранить</button>
        ${x.hidden && !(given && c.publish) ? "" : `<button type="button" class="chip danger" data-revoke>Отозвать согласие и скрыть номер</button>`}
      </div>
    </section>`;
  }
  function render() {
    const phones = data.phones || [];
    $("anBonus").textContent = `${data.bonus_days || 30} дней`;
    $("anVerifyCard").hidden = false;
    $("anVerifyH").textContent = phones.length ? "Подтвердить ещё один номер" : "Подтвердите номер из объявлений";
    if (window.PhoneVerify) PhoneVerify.mount($("anVerify"), { methods: data.verify_methods, again: phones.length > 0, onDone: load });
    $("anPhones").innerHTML = phones.map(phoneCard).join("");
  }
  async function load() {
    const r = await call("/api/agent/consent");
    if (r.data && r.data.detail && !r.ok) { $("anGuest").hidden = false; return; }
    data = r.data;
    render();
  }
  document.addEventListener("click", async (e) => {
    const card = e.target.closest(".an-phone");
    if (!card) return;
    const phone = card.dataset.phone;
    if (e.target.closest("[data-save]")) {
      const v = (k) => card.querySelector(`[data-k="${k}"]`).checked;
      const r = await call("/api/agent/consent", { phone, publish: v("publish"), license: v("license"), show_name: v("show_name"), allow_max: v("allow_max") });
      if (!r.ok) { toast(r.data.detail || "Не получилось сохранить"); return; }
      data.phones = r.data.phones;
      render();
      toast(r.data.bonus ? `Готово! Номер подтверждён, согласие сохранено — и ${r.data.bonus} дней доступа в подарок.`
        : v("publish") ? "Сохранено — номер показывается с вашего согласия." : "Сохранено — номер скрыт во всех ваших объявлениях.", 4500);
    }
    if (e.target.closest("[data-revoke]")) {
      if (!confirm("Отозвать согласие? Номер сразу перестанет показываться во всех ваших объявлениях.")) return;
      const r = await call("/api/agent/consent", { phone, revoke: true });
      if (!r.ok) { toast(r.data.detail || "Не получилось"); return; }
      data.phones = r.data.phones;
      render();
      toast("Согласие отозвано — номер скрыт во всех объявлениях.");
    }
  });
  load();
})();
