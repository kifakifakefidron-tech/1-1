/* Лента запросов покупателей (/requests). API: /api/requests */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const TYPES = { flat: "Квартира", room: "Комната", house: "Дом", land: "Участок", commercial: "Коммерция" };
  const f = { deal: "", rooms: null, district: "", q: "" };
  let page = 1;

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

  function itemHTML(r, access) {
    const chips = [r.deal === "rent" ? "Аренда" : "Покупка", r.type && TYPES[r.type], roomsText(r.rooms_mask),
      r.price_min && r.price_max ? `${money(r.price_min, r.deal)} – ${money(r.price_max, r.deal)}` : r.price_max ? `до ${money(r.price_max, r.deal)}` : "",
      ...r.districts, ...r.complexes.map((c) => `ЖК ${c}`)].filter(Boolean);
    const phones = access && r.phones && r.phones.length
      ? r.phones.map((p) => `<a class="pill" href="tel:${esc(p)}">${esc(p)}</a><a class="pill light" href="https://wa.me/${p.replace(/\D/g, "")}" target="_blank" rel="noopener">WhatsApp</a>`).join("")
      : r.phones_count ? `<span class="rq-lock">📞 +7 ••• •••-••-•• — по подписке</span>` : "";
    return `<li class="rq-item">
      <div class="rq-chips">${chips.map((c) => `<span class="chip on">${esc(c)}</span>`).join("")}</div>
      <p class="rq-text">${esc(r.text)}</p>
      <div class="rq-foot"><span class="note">${esc(ago(r.last_seen))}${r.seen_count > 1 ? ` · присылали ${r.seen_count} раз` : ""}</span>
        <a class="chip" href="${esc(r.url)}">Подходящие объекты →</a></div>
      ${phones ? `<div class="rq-phones">${phones}</div>` : ""}</li>`;
  }

  async function load(append = false) {
    const p = new URLSearchParams({ page });
    if (f.deal) p.set("deal", f.deal);
    if (f.rooms != null) p.set("rooms", f.rooms);
    if (f.district) p.set("district", f.district);
    if (f.q) p.set("q", f.q);
    let d;
    try { d = await (await fetch(`/api/requests?${p}`)).json(); } catch { $("rqCount").textContent = "Не получилось загрузить."; return; }
    $("rqCount").textContent = d.total ? `Запросов: ${d.total.toLocaleString("ru-RU")} · за последние ${d.stale_days} дней` : "";
    $("rqLocked").hidden = d.access;
    const html = d.items.map((r) => itemHTML(r, d.access)).join("");
    if (append) $("rqList").insertAdjacentHTML("beforeend", html);
    else $("rqList").innerHTML = html || `<li class="rq-empty"><b>Запросов пока нет</b>Как только в чатах появятся «куплю/ищу» — они будут здесь.</li>`;
    $("rqMore").hidden = d.page >= d.pages;
  }
  // Фильтров пока нет (решение Артёма) — просто лента запросов
  $("rqMore").addEventListener("click", () => { page += 1; load(true); });
  load();
})();
