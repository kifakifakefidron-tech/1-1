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
  // 24.10c (образцы Артёма): номер крупно + статус-капсула, согласия — плитками с галочкой, переключатели, кнопки внизу
  function phoneCard(x) {
    const c = x.consent || {};
    const given = !!x.consent;
    const state = x.hidden ? `<span class="an2-st off">${ic("ban")}Номер скрыт на сайте</span>`
      : given && c.publish ? `<span class="an2-st on">${ic("check-circle")}Показывается с вашего согласия</span>`
        : `<span class="an2-st wait">${ic("clock")}Согласие ещё не дано — номер из объявлений в чатах</span>`;
    const opt = (k, on, icon, title, text) => `<label class="an2-opt${on ? " on" : ""}"><input type="checkbox" data-k="${k}"${on ? " checked" : ""}>
        <span class="an2-box">${ic("check")}</span><span class="an2-oi">${ic(icon)}</span><span class="an2-ot"><b>${title}</b><small>${text}</small></span></label>`;
    const n = x.listings || 0;
    return `<section class="st-card an2-card an-phone" data-phone="${esc(x.phone)}">
      <div class="an-ph-h"><div><p class="an2-lbl">Подтверждённый номер</p><b class="an-num">${esc(pretty(x.phone))}</b>${state}</div>
        <a class="an2-objs" href="/my"><b>${n}</b><span>${n % 10 === 1 && n % 100 !== 11 ? "объект" : "объектов"}<br>с номером</span>${ic("chevron")}</a></div>
      ${given ? `<p class="an-when">Последнее решение — ${esc(date(c.ts))} (документы от ${esc(c.version || "")})</p>` : ""}
      <div class="an-checks">
        ${opt("publish", given && c.publish, "phone", "Показывать мой номер и имя",
          `Согласие на <a href="/consent-publish" target="_blank">распространение персональных данных</a> — в карточках ваших объявлений, только вошедшим с доступом. Отозвать — в любой момент.`)}
        ${opt("license", given && c.license, "layers", "Тексты и фото объявлений",
          `Разрешение <a href="/license" target="_blank">использовать тексты и фото</a> — в карточках, превью ссылок и подборках для клиентов коллег.`)}
      </div>
      <div class="an-opts">
        <label class="an-sw"><input type="checkbox" data-k="show_name"${!given || c.show_name ? " checked" : ""}><span></span>Показывать моё имя</label>
        <label class="an-sw"><input type="checkbox" data-k="allow_max"${!given || c.allow_max ? " checked" : ""}><span></span>Кнопка «Написать в MAX»</label>
      </div>
      <p class="an-hint">${ic("help")}<span>Без первой галочки номер будет <b>скрыт</b> во всех ваших объявлениях — это тоже честный выбор.</span></p>
      <div class="st-acts an2-acts">
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
    const objs = phones.reduce((s, x) => s + (x.listings || 0), 0), ok = phones.filter((x) => x.consent && x.consent.publish).length;
    $("anNums").innerHTML = phones.length ? `<span><b>${phones.length}</b><small>${phones.length === 1 ? "номер" : "номера"}</small></span>
      <span><b>${objs}</b><small>объектов</small></span><span class="${ok === phones.length ? "up" : ""}"><b>${ok}/${phones.length}</b><small>с согласием</small></span>` : "";
  }
  async function load() {
    const r = await call("/api/agent/consent");
    if (r.data && r.data.detail && !r.ok) { $("anGuest").hidden = false; return; }
    data = r.data;
    render();
  }
  document.addEventListener("change", (e) => {   // плитка согласия подсвечивается сразу
    const o = e.target.closest(".an2-opt");
    if (o) o.classList.toggle("on", e.target.checked);
  });
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
