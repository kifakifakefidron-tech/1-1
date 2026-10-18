/* Запросы покупателей (/requests) — 19.10: всем с подпиской. Вкладки «Все / Под мои объекты» (по подтверждённым номерам),
   быстрые кнопки сделка/комнаты и поиск по тексту, «Новый» с прошлого захода, у запроса — звонок/MAX, подходящие объекты,
   «Сохранить как клиента», админу — «Удалить». API: GET /api/requests[?mine=1&deal=&rooms=&q=&page=], POST {id, delete}. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const ic = (n) => (window.ic ? window.ic(n) : "");
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const TYPES = { flat: "Квартира", room: "Комната", house: "Дом", land: "Участок", commercial: "Коммерция" };
  const f = { tab: "all", deal: "", rooms: null, q: "" };
  let page = 1, admin = false, qTimer = 0;
  let lastVisit = 0;
  try { lastVisit = Number(localStorage.getItem("rqVisit") || 0); localStorage.setItem("rqVisit", String(Math.floor(Date.now() / 1000))); } catch { /* */ }

  const money = (p, deal) => (deal === "rent" ? `${Math.round(p / 1000).toLocaleString("ru-RU")} тыс. ₽/мес`
    : `${(p / 1e6).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} млн ₽`);
  const roomsText = (mask) => {
    const r = [0, 1, 2, 3, 4].filter((i) => mask & (1 << i)).map((i) => (i === 0 ? "студия" : i === 4 ? "4+" : `${i}`));
    return r.length ? (r.length === 1 && r[0] === "студия" ? "студия" : `${r.join(", ")} комн.`) : "";
  };
  const ago = (ts) => {
    const h = Math.round((Date.now() / 1000 - ts) / 3600);
    return h < 1 ? "только что" : h < 24 ? `${h} ч назад` : h < 48 ? "вчера" : new Date(ts * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
  };
  const pretty = (p) => { const d = String(p).replace(/\D/g, ""); return d.length === 11 ? `+7 ${d.slice(1, 4)} ${d.slice(4, 7)}-${d.slice(7, 9)}-${d.slice(9)}` : p; };
  function toast(text, ms = 2600) {
    const t = $("toast");
    t.textContent = text;
    try { t.showPopover(); } catch { t.hidden = false; }
    clearTimeout(toast.t);
    toast.t = setTimeout(() => { try { t.hidePopover(); } catch { t.hidden = true; } }, ms);
  }
  // «Сохранить как клиента»: пожелания запроса → форма нового клиента в CRM
  const clientLink = (r) => `/clients?new=1&from=${encodeURIComponent(JSON.stringify({
    rooms: [0, 1, 2, 3, 4].filter((i) => r.rooms_mask & (1 << i)), districts: r.districts,
    price_min: r.deal === "sale" ? r.price_min : null, price_max: r.deal === "sale" ? r.price_max : null }))}`;

  function itemHTML(r, i) {
    const chips = [r.deal === "rent" ? "Аренда" : "Покупка", r.type && TYPES[r.type], roomsText(r.rooms_mask),
      r.price_min && r.price_max ? `${money(r.price_min, r.deal)} – ${money(r.price_max, r.deal)}` : r.price_max ? `до ${money(r.price_max, r.deal)}` : "",
      ...r.districts, ...r.complexes.map((c) => `ЖК ${c}`)].filter(Boolean);
    const fresh = lastVisit && r.first_seen > lastVisit;
    const phones = (r.phones || []).map((p) => `<a class="pill" href="tel:${esc(p)}">${ic("phone")}${esc(pretty(p))}</a>
      <button type="button" class="pill light" data-max="${esc(p)}">MAX</button>`).join("");
    const mine = (r.mine || []).length ? `<p class="rq-mine">${ic("check-circle")}Подходит под ${r.mine.length > 1 ? "ваши объекты" : "ваш объект"}:
      ${r.mine.map((id, k) => `<a href="/?open=${id}">№${k + 1}</a>`).join(", ")}</p>` : "";
    return `<li class="rq-item${fresh ? " fresh" : ""}" style="--d:${Math.min(i, 12) * 40}ms">
      <div class="rq-chips">${fresh ? `<span class="rq-new">Новый</span>` : ""}${r.source === "agent" ? `<span class="rq-agent">${ic("check-circle")}Запрос агента 1+1</span>` : ""}${chips.map((c) => `<span class="chip on">${esc(c)}</span>`).join("")}</div>
      <p class="rq-text clamp">${esc(r.text)}</p>
      <button type="button" class="rq-more" data-more hidden>Полностью</button>
      ${mine}
      <div class="rq-foot"><span class="note">${esc(ago(r.last_seen))}${r.source === "agent" ? " · опубликовал агент на сайте" : r.seen_count > 1 ? ` · присылали ${r.seen_count} раз` : ""}</span></div>
      ${phones ? `<div class="rq-phones">${phones}</div>` : ""}
      <div class="rq-acts"><a class="chip" href="${esc(r.search)}">${ic("search")}Подходящие объекты</a>
        <a class="chip" href="${esc(clientLink(r))}">${ic("users")}Сохранить как клиента</a>
        ${admin ? `<button type="button" class="chip danger" data-del="${r.id}">${ic("trash")}Удалить</button>` : ""}</div></li>`;
  }

  async function load(append = false) {
    const p = new URLSearchParams({ page });
    if (f.tab === "mine") p.set("mine", "1");
    if (f.deal) p.set("deal", f.deal);
    if (f.rooms != null) p.set("rooms", f.rooms);
    if (f.q) p.set("q", f.q);
    let d;
    try { d = await (await fetch(`/api/requests?${p}`, { credentials: "same-origin" })).json(); }
    catch { $("rqCount").textContent = "Не получилось загрузить."; return; }
    if (d.locked) {   // без подписки: число запросов и приглашение
      $("rqTools").hidden = true;
      $("rqCount").textContent = "";
      $("rqLocked").hidden = false;
      $("rqLocked").innerHTML = `<b>${(d.total || 0).toLocaleString("ru-RU")} запросов покупателей сейчас</b>
        <span>${d.new_24h ? `${d.new_24h} новых за сутки. ` : ""}Кто что ищет, бюджет, районы и номер агента — по подписке.</span>
        <a class="pill" href="${d.guest ? "/?login=1" : "/pay"}">${d.guest ? "Войти — неделя бесплатно" : "Оформить доступ"}</a>`;
      $("rqList").innerHTML = "";
      return;
    }
    admin = !!d.admin;
    $("rqTools").hidden = false;
    $("rqMineN").textContent = d.mine_total ? d.mine_total : "";
    $("rqMineHint").hidden = f.tab !== "mine" || d.mine_total;
    $("rqMineHint").innerHTML = d.has_listings ? "Под ваши объекты сейчас запросов нет — загляните позже."
      : `Здесь — запросы под ваши объекты. <a href="/agent">Подтвердите свой номер</a>, и сервис сам подберёт покупателей под ваши объявления.`;
    const n = d.total || 0;
    const word = n % 10 === 1 && n % 100 !== 11 ? "запрос" : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? "запроса" : "запросов";
    $("rqCount").textContent = `${n.toLocaleString("ru-RU")} ${word}${f.tab === "mine" ? " под ваши объекты" : ""}${d.new_24h && f.tab === "all" ? ` · новых за сутки: ${d.new_24h}` : ""}`;
    const html = d.items.map((r, i) => itemHTML(r, i)).join("");
    if (append) $("rqList").insertAdjacentHTML("beforeend", html);
    else $("rqList").innerHTML = html || `<li class="rq-empty"><b>${f.tab === "mine" ? "Пока пусто" : "Запросов не нашлось"}</b>
      ${f.tab === "mine" ? "Как только кто-то будет искать похожее на ваши объекты — запрос появится здесь." : "Попробуйте убрать часть условий."}</li>`;
    $("rqMore").hidden = d.page >= d.pages;
    document.querySelectorAll(".rq-text.clamp").forEach((p) => {
      const b = p.nextElementSibling;
      if (p.scrollHeight > p.clientHeight + 4) b.hidden = false; else p.classList.remove("clamp");
    });
  }
  function sync() {
    document.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("on", b.dataset.tab === f.tab));
    document.querySelectorAll("[data-deal]").forEach((b) => b.classList.toggle("on", b.dataset.deal === f.deal));
    document.querySelectorAll("[data-rooms]").forEach((b) => b.classList.toggle("on", Number(b.dataset.rooms) === f.rooms));
  }
  document.addEventListener("click", async (e) => {
    const t = e.target.closest("button, a");
    if (!t) return;
    if (t.dataset.tab) { f.tab = t.dataset.tab; page = 1; sync(); load(); return; }
    if (t.dataset.deal) { f.deal = f.deal === t.dataset.deal ? "" : t.dataset.deal; page = 1; sync(); load(); return; }
    if (t.dataset.rooms != null && t.dataset.rooms !== undefined && t.hasAttribute("data-rooms")) {
      const n = Number(t.dataset.rooms); f.rooms = f.rooms === n ? null : n; page = 1; sync(); load(); return;
    }
    if (t.hasAttribute("data-more")) { t.previousElementSibling.classList.remove("clamp"); t.hidden = true; return; }
    if (t.dataset.del) {
      if (!confirm("Удалить запрос? Повтор того же текста больше не появится.")) return;
      const r = await fetch("/api/requests", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: Number(t.dataset.del), delete: true }) });
      if (r.ok) { t.closest(".rq-item").remove(); toast("Запрос удалён"); } else toast("Не получилось удалить");
    }
  });
  $("rqQ").addEventListener("input", () => { clearTimeout(qTimer); qTimer = setTimeout(() => { f.q = $("rqQ").value.trim(); page = 1; load(); }, 350); });
  $("rqMore").addEventListener("click", () => { page += 1; load(true); });
  if (new URLSearchParams(location.search).get("mine") === "1") { f.tab = "mine"; sync(); }
  load();
})();
