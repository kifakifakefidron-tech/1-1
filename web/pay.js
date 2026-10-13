/* Оформление доступа (/pay): тариф → ЮKassa (данные карты вводятся у них) → возврат ?check=1 → проверка → «Готово».
   Карта «1+1» поворачивается за мышью; при оплате светится, при успехе — зелёная обводка. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const plural = (n, a, b, c) => { const m10 = n % 10, m100 = n % 100; return m10 === 1 && m100 !== 11 ? a : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? b : c; };
  const fmtDate = (d) => d.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });
  let me = null, plans = [], chosen = null, enabled = false;

  async function call(url, body) {
    const opts = { credentials: "same-origin", method: body ? "POST" : "GET" };
    if (body) { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(body); }
    const r = await fetch(url, opts);
    let d = {};
    try { d = await r.json(); } catch { /* */ }
    return { ok: r.ok, status: r.status, data: d };
  }
  function step(n) {
    document.querySelectorAll("#paySteps li").forEach((li) => {
      const s = Number(li.dataset.step);
      li.classList.toggle("on", s === n);
      li.classList.toggle("ok", s < n);
    });
  }
  function view(id) { ["viewPlans", "viewWait", "viewDone", "viewFail"].forEach((v) => { $(v).hidden = v !== id; }); }

  // ─── карта: наклон за мышью, блик ───────────────────────────────────
  const card = $("card3d");
  card.addEventListener("pointermove", (e) => {
    if (card.classList.contains("flip")) return;
    const r = card.getBoundingClientRect(), x = (e.clientX - r.left) / r.width - .5, y = (e.clientY - r.top) / r.height - .5;
    card.style.setProperty("--ry", `${x * 16}deg`);
    card.style.setProperty("--rx", `${-y * 12}deg`);
    card.style.setProperty("--sx", `${x * 60}%`);
  });
  card.addEventListener("pointerleave", () => { card.style.setProperty("--ry", "0deg"); card.style.setProperty("--rx", "0deg"); });
  card.addEventListener("click", () => card.classList.toggle("flip"));

  function updateCard() {
    if (!chosen) return;
    $("cardDays").textContent = `${chosen.days} ${plural(chosen.days, "ДЕНЬ", "ДНЯ", "ДНЕЙ")}`;
    const base = me && me.access_until && me.access_until * 1000 > Date.now() ? new Date(me.access_until * 1000) : new Date();
    $("cardTill").textContent = fmtDate(new Date(base.getTime() + chosen.days * 86400000));
    $("payTotal").textContent = `${chosen.price} ₽`;
    $("payBtnText").textContent = enabled ? `Оплатить ${chosen.price} ₽` : "Оплата появится скоро";
  }
  function renderPlans() {
    $("plans").innerHTML = plans.map((p, i) => `<label class="plan${p === chosen ? " on" : ""}" data-i="${i}">
      <input type="radio" name="plan" ${p === chosen ? "checked" : ""}><span class="plan-dot"></span>
      <span class="plan-name"><b>${p.first ? "Первая неделя" : `${p.days} ${plural(p.days, "день", "дня", "дней")}`}</b>
        <small>${p.first ? `потом ${p.old_price} ₽ в неделю` : `${String(p.per_day).replace(".", ",")} ₽ в день`}</small></span>
      <span class="plan-price">${p.old_price ? `<s>${p.old_price} ₽</s>` : ""}${p.price} ₽</span>${p.save ? `<span class="plan-save">−${p.save}%</span>` : ""}</label>`).join("");
    updateCard();
  }
  $("plans").addEventListener("click", (e) => {
    const l = e.target.closest(".plan");
    if (!l) return;
    chosen = plans[Number(l.dataset.i)];
    renderPlans();
    card.animate([{ transform: "scale(1)" }, { transform: "scale(1.03)" }, { transform: "scale(1)" }], { duration: 380, easing: "ease-out" });
  });

  async function pay() {
    if (!me) { location.href = "/?login=1"; return; }
    if (!enabled) {
      if (me.is_admin) { demo(); return; }   // админ видит, как это будет выглядеть
      return;
    }
    $("payBtn").disabled = true;
    card.classList.remove("flip");
    card.classList.add("processing");
    step(2);
    const r = await call("/api/pay", { days: chosen.days });
    if (!r.ok || !r.data.url) {
      card.classList.remove("processing");
      $("payBtn").disabled = false;
      step(1);
      $("payNote").textContent = r.data.detail || "Не получилось начать оплату — попробуйте ещё раз.";
      return;
    }
    setTimeout(() => { location.href = r.data.url; }, 650);   // к ЮKassa — после короткой анимации
  }
  function success(days) {
    card.classList.remove("processing");
    card.classList.add("success");
    step(4);
    $("doneText").textContent = `Спасибо! Доступ продлён на ${days} ${plural(days, "день", "дня", "дней")} — номера агентов и все инструменты уже работают.`;
    view("viewDone");
  }
  function demo() {
    card.classList.add("processing");
    step(2);
    view("viewWait");
    setTimeout(() => success(chosen.days), 2200);
  }
  async function check(tries = 0) {
    view("viewWait");
    step(2);
    card.classList.add("processing");
    const r = await call("/api/pay?check=1");
    const last = r.ok ? r.data.last : null;
    if (last && last.status === "succeeded") { success(last.days); return; }
    if (last && last.status === "canceled") { card.classList.remove("processing"); step(2); view("viewFail"); return; }
    if (tries < 20) { setTimeout(() => check(tries + 1), 2500); return; }
    $("waitTitle").textContent = "Платёж ещё обрабатывается";
    $("waitText").textContent = "Как только банк подтвердит оплату, доступ откроется сам — можно закрыть страницу.";
  }

  $("payBtn").addEventListener("click", pay);
  $("payRetry").addEventListener("click", () => { card.classList.remove("processing", "success"); view("viewPlans"); step(1); $("payBtn").disabled = false; });

  (async () => {
    const [m, p] = await Promise.all([call("/api/me"), call("/api/pay")]);
    me = m.data.me;
    if (me) $("cardName").textContent = (me.name || (me.email || "").split("@")[0] || "Риелтор").slice(0, 22);
    plans = (p.ok && p.data.plans) || [{ days: 7, price: 99, per_day: 14.1, save: 0 }];
    enabled = !!(p.ok && p.data.enabled);
    // по умолчанию: первая неделя по скидке (если ещё не платил), иначе самый выгодный
    chosen = plans.find((x) => x.first) || plans.find((x) => x.save) || plans[0];
    renderPlans();
    if (!me) { $("payBtnText").textContent = "Войти и оформить"; $("payNote").textContent = "Сначала войдите по почте — доступ привяжется к аккаунту."; }
    else if (!enabled) $("payNote").textContent = me.is_admin ? "Оплата выключена (нет ключей ЮKassa). Нажмите кнопку — покажем, как будет выглядеть успех." : "Скоро здесь можно будет оплатить картой или СБП.";
    if (new URLSearchParams(location.search).has("check") && me) check();
  })();
})();
