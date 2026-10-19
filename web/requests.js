/* Запросы покупателей (/requests) — всем с подпиской. 19.10 (Артём): просто лента от свежих — без фильтров, без «под мои
   объекты», «подходящих объектов» и «сохранить как клиента». «Новый» с прошлого захода, у запроса — звонок/MAX, админу —
   «Удалить». ?id=N (из уведомления «Под ваш объект есть покупатель») — этот запрос первым и подсвечен.
   API: GET /api/requests[?page=&id=], POST {id, delete}. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const ic = (n) => (window.ic ? window.ic(n) : "");
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const TYPES = { flat: "Квартира", room: "Комната", house: "Дом", land: "Участок", commercial: "Коммерция" };
  const pin = Number(new URLSearchParams(location.search).get("id")) || 0;
  let page = 1, admin = false;
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
  function itemHTML(r, i) {
    const chips = [r.deal === "rent" ? "Аренда" : "Покупка", r.type && TYPES[r.type], roomsText(r.rooms_mask),
      r.price_min && r.price_max ? `${money(r.price_min, r.deal)} – ${money(r.price_max, r.deal)}` : r.price_max ? `до ${money(r.price_max, r.deal)}` : "",
      ...r.districts, ...r.complexes.map((c) => `ЖК ${c}`)].filter(Boolean);
    const fresh = lastVisit && r.first_seen > lastVisit;
    const phones = (r.phones || []).map((p) => `<a class="pill" href="tel:${esc(p)}">${ic("phone")}${esc(pretty(p))}</a>
      <button type="button" class="pill light" data-max="${esc(p)}">MAX</button>`).join("");
    return `<li class="rq-item${fresh ? " fresh" : ""}${r.pinned ? " pinned" : ""}" style="--d:${Math.min(i, 12) * 40}ms">
      <div class="rq-chips">${fresh ? `<span class="rq-new">Новый</span>` : ""}${r.source === "agent" ? `<span class="rq-agent">${ic("check-circle")}Запрос агента 1+1</span>` : ""}${chips.map((c) => `<span class="chip on">${esc(c)}</span>`).join("")}</div>
      <p class="rq-text clamp">${esc(r.text)}</p>
      <button type="button" class="rq-more" data-more hidden>Полностью</button>
      <div class="rq-foot"><span class="note">${esc(ago(r.last_seen))}${r.source === "agent" ? " · опубликовал агент на сайте" : r.seen_count > 1 ? ` · присылали ${r.seen_count} раз` : ""}</span></div>
      ${phones ? `<div class="rq-phones">${phones}</div>` : ""}
      ${admin ? `<div class="rq-acts"><button type="button" class="chip danger" data-del="${r.id}">${ic("trash")}Удалить</button></div>` : ""}</li>`;
  }

  async function load(append = false) {
    const p = new URLSearchParams({ page });
    if (pin) p.set("id", pin);
    let d;
    try { d = await (await fetch(`/api/requests?${p}`, { credentials: "same-origin" })).json(); }
    catch { $("rqCount").textContent = "Не получилось загрузить."; return; }
    if (d.locked) {   // без подписки: число запросов и приглашение
      $("rqCount").textContent = "";
      $("rqLocked").hidden = false;
      $("rqLocked").innerHTML = `<b>${(d.total || 0).toLocaleString("ru-RU")} запросов покупателей сейчас</b>
        <span>${d.new_24h ? `${d.new_24h} новых за сутки. ` : ""}Кто что ищет, бюджет, районы и номер агента — по подписке.</span>
        <a class="pill" href="${d.guest ? "/?login=1" : "/pay"}">${d.guest ? "Войти — неделя бесплатно" : "Оформить доступ"}</a>`;
      $("rqList").innerHTML = "";
      return;
    }
    admin = !!d.admin;
    const n = d.total || 0;
    const word = n % 10 === 1 && n % 100 !== 11 ? "запрос" : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? "запроса" : "запросов";
    $("rqCount").textContent = `${n.toLocaleString("ru-RU")} ${word}${d.new_24h ? ` · новых за сутки: ${d.new_24h}` : ""}`;
    const html = d.items.map((r, i) => itemHTML(r, i)).join("");
    if (append) $("rqList").insertAdjacentHTML("beforeend", html);
    else $("rqList").innerHTML = html || `<li class="rq-empty"><b>Запросов пока нет</b>Как только кто-то напишет «ищу / куплю» — запрос появится здесь.</li>`;
    $("rqMore").hidden = d.page >= d.pages;
    document.querySelectorAll(".rq-text.clamp").forEach((p) => {
      const b = p.nextElementSibling;
      if (p.scrollHeight > p.clientHeight + 4) b.hidden = false; else p.classList.remove("clamp");
    });
  }
  document.addEventListener("click", async (e) => {
    const t = e.target.closest("button, a");
    if (!t) return;
    if (t.hasAttribute("data-more")) { t.previousElementSibling.classList.remove("clamp"); t.hidden = true; return; }
    if (t.dataset.del) {
      if (!confirm("Удалить запрос? Повтор того же текста больше не появится.")) return;
      const r = await fetch("/api/requests", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: Number(t.dataset.del), delete: true }) });
      if (r.ok) { t.closest(".rq-item").remove(); toast("Запрос удалён"); } else toast("Не получилось удалить");
    }
  });
  $("rqMore").addEventListener("click", () => { page += 1; load(true); });
  load().then(() => { const el = document.querySelector(".rq-item.pinned"); if (el) el.scrollIntoView({ block: "center" }); });
})();
