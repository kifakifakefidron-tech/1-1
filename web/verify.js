/* Подтверждение номера агента — общий блок для «Мои объекты» и «Настройки» (17.10).
   Основной способ — ЗВОНОК (SMS.ru callcheck): позвонить со своего номера на выданный номер, звонок сбросится сам.
   Запасной — код в MAX («1+1-XXXXXX» со своего номера на наш). API: POST /api/agent/verify {phone, method:"call"} |
   {phone} (MAX) | {phone, check:true}; GET /api/agent/verify?phone=&method=call.
   PhoneVerify.mount(box, { methods: ["call","max"], again: bool, onDone() }) */
(() => {
  "use strict";
  const ic = (n) => (window.ic ? window.ic(n) : "");
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  async function call(url, body) {
    const opts = { credentials: "same-origin", method: body !== undefined ? "POST" : "GET" };
    if (body !== undefined) { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(body); }
    const r = await fetch(url, opts);
    let d = {};
    try { d = await r.json(); } catch { /* */ }
    return { ok: r.ok, data: d };
  }
  async function copy(t) {
    try { await navigator.clipboard.writeText(t); return true; } catch { prompt("Скопируйте:", t); return false; }
  }

  function mount(box, opt = {}) {
    const methods = opt.methods && opt.methods.length ? opt.methods : ["max"];
    const hasCall = methods.includes("call"), hasMax = methods.includes("max");
    let timer = 0, tick = 0;
    const stop = () => { clearInterval(timer); clearInterval(tick); };
    const done = () => { stop(); box.innerHTML = `<p class="pv-ok">${ic("check-circle")}Номер подтверждён</p>`; opt.onDone && opt.onDone(); };

    function form(method) {
      stop();
      box.innerHTML = `<form class="pv-form" autocomplete="off">
          <input type="tel" inputmode="tel" autocomplete="tel" placeholder="+7 918 123-45-67" aria-label="Ваш номер" required>
          <button type="submit" class="pill">${method === "call" ? `${ic("phone")}Подтвердить звонком` : `${opt.again ? "Подтвердить ещё номер" : "Получить код"}`}</button>
        </form>
        ${method === "call" ? `<p class="note pv-hint">Позвоните со своего номера на номер, который мы покажем, — звонок бесплатный и сбросится сам. Ничего говорить и вводить не нужно.</p>`
          : `<p class="note pv-hint">Отправите сообщение с кодом со своего номера в MAX.</p>`}
        ${method === "call" && hasMax ? `<button type="button" class="pv-alt" data-alt="max">Нет возможности позвонить? Подтвердить кодом в MAX</button>` : ""}
        ${method === "max" && hasCall ? `<button type="button" class="pv-alt" data-alt="call">${ic("phone")}Подтвердить звонком — быстрее</button>` : ""}
        <div class="pv-out"></div>`;
      const f = box.querySelector("form");
      f.addEventListener("submit", (e) => { e.preventDefault(); (method === "call" ? startCall : startMax)(f.querySelector("input").value.trim()); });
      box.querySelectorAll("[data-alt]").forEach((b) => b.addEventListener("click", () => form(b.dataset.alt)));
    }
    const out = () => box.querySelector(".pv-out");

    async function startCall(phone) {
      const r = await call("/api/agent/verify", { phone, method: "call" });
      if (!r.ok) { out().innerHTML = `<p class="pv-err">${esc(r.data.detail || "Не получилось.")}</p>`; return; }
      const { call_phone: to, call_pretty: pretty, phone: ph, ttl } = r.data;
      let left = ttl || 300;
      out().innerHTML = `<div class="pv-card">
          <span class="pv-step">Позвоните с номера <b>${esc(ph)}</b> на номер</span>
          <a class="pv-num" href="tel:${esc(to)}">${esc(pretty)}</a>
          <div class="pv-acts"><a class="pill" href="tel:${esc(to)}">${ic("phone")}Позвонить</a>
            <button type="button" class="chip" data-copy>Скопировать номер</button></div>
          <p class="pv-wait"><span class="pv-dot"></span><span class="pv-wt">Ждём звонок · <b class="pv-left"></b></span></p>
          <small class="note">Звонок бесплатный, сбросится сам. Как только позвоните — номер подтвердится, страница обновится.</small></div>`;
      out().querySelector("[data-copy]").addEventListener("click", async () => { if (await copy(to)) out().querySelector("[data-copy]").textContent = "Скопировано"; });
      const show = () => { const el = out().querySelector(".pv-left"); if (el) el.textContent = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`; };
      show();
      stop();
      tick = setInterval(() => { left = Math.max(0, left - 1); show(); }, 1000);
      timer = setInterval(async () => {
        const s = await call(`/api/agent/verify?phone=${encodeURIComponent(ph)}&method=call`);
        if (!s.ok) return;
        if (s.data.status === "ok") { done(); return; }
        if (s.data.status === "expired" || left <= 0) {
          stop();
          out().innerHTML = `<div class="pv-card"><p class="pv-err">Время вышло — звонка не было.</p>
            <button type="button" class="pill" data-again>Начать заново</button></div>`;
          out().querySelector("[data-again]").addEventListener("click", () => startCall(ph));
        }
      }, 3000);
    }

    async function startMax(phone) {
      const r = await call("/api/agent/verify", { phone });
      if (!r.ok) { out().innerHTML = `<p class="pv-err">${esc(r.data.detail || "Не получилось.")}</p>`; return; }
      const { code, send_to: to, phone: ph } = r.data, text = `1+1-${code}`;
      out().innerHTML = `<div class="pv-card"><p>Отправьте сообщение <b class="pv-code">${esc(text)}</b><br>с номера <b>${esc(ph)}</b> на номер <b>${esc(to)}</b> в MAX.</p>
        <div class="pv-acts"><button type="button" class="chip" data-c="${esc(text)}">Скопировать код</button>
          <button type="button" class="chip" data-c="${esc(to)}">Скопировать наш номер</button></div>
        <button type="button" class="pill" data-check>Я отправил — проверить</button>
        <p class="note pv-wt">Отправили? Нажмите «Проверить» — посмотрим сразу. Сами проверяем раз в 1–2 минуты.</p></div>`;
      out().querySelectorAll("[data-c]").forEach((b) => b.addEventListener("click", () => copy(b.dataset.c)));
      out().querySelector("[data-check]").addEventListener("click", async (e) => {
        const b = e.currentTarget; b.disabled = true; b.textContent = "Проверяем…";
        const c = await call("/api/agent/verify", { phone: ph, check: true });
        b.disabled = false; b.textContent = "Проверить ещё раз";
        if (c.ok && c.data.status === "ok") { done(); return; }
        out().querySelector(".pv-wt").textContent = c.data.detail || "Пока не пришло — попробуйте через минуту.";
      });
      stop();
      let tries = 0;
      timer = setInterval(async () => {
        if (++tries > 360) { stop(); return; }
        const s = await call(`/api/agent/verify?phone=${encodeURIComponent(ph)}`);
        if (s.ok && s.data.status === "ok") done();
      }, 5000);
    }
    form(hasCall ? "call" : "max");
    return { stop };
  }
  window.PhoneVerify = { mount };
})();
