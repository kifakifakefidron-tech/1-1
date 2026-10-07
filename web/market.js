/* Аналитика рынка /market: медианы по районам и ЖК, динамика. API: /api/market?deal= */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const n0 = (x) => Number(x).toLocaleString("ru-RU");
  const sp = new URLSearchParams(location.search);
  let deal = sp.get("deal") === "rent" ? "rent" : "sale";
  let kind = sp.get("kind") === "complexes" ? "complexes" : "districts";
  let sort = { key: "n", dir: -1 };
  let data = null;

  const money = (p) => (p >= 1e6 ? `${(p / 1e6).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} млн ₽`
    : `${n0(Math.round(p / 1000))} тыс. ₽`);
  const main = (x) => (deal === "sale" ? `${n0(x.median)} ₽/м²` : `${money(x.median)}/мес`);
  const change = (v) => (v == null ? `<span class="mk-ch none">—</span>`
    : `<span class="mk-ch ${v > 0 ? "up" : v < 0 ? "down" : ""}">${v > 0 ? "▲" : v < 0 ? "▼" : ""} ${Math.abs(v).toLocaleString("ru-RU")}%</span>`);

  function writeUrl() {
    const p = new URLSearchParams();
    if (deal === "rent") p.set("deal", "rent");
    if (kind === "complexes") p.set("kind", "complexes");
    history.replaceState(null, "", p.toString() ? `?${p}` : location.pathname);
  }

  async function load() {
    $("mkTable").innerHTML = `<tr><td class="note">Считаем…</td></tr>`;
    try {
      const r = await fetch(`/api/market?deal=${deal}`);
      data = await r.json();
    } catch { $("mkTable").innerHTML = `<tr><td class="note">Не получилось загрузить. Обновите страницу.</td></tr>`; return; }
    render();
  }

  function render() {
    document.querySelectorAll("[data-deal]").forEach((b) => b.classList.toggle("on", b.dataset.deal === deal));
    document.querySelectorAll("[data-kind]").forEach((b) => b.classList.toggle("on", b.dataset.kind === kind));
    const c = data.city;
    $("mkCity").innerHTML = c.median ? `
      <div class="mk-card main"><span>Краснодар, ${deal === "sale" ? "цена м²" : "аренда в месяц"}</span><b>${main(c)}</b>
        <small>по ${n0(c.n)} квартирам · неделя ${change(c.ch7)} · месяц ${change(c.ch30)}</small></div>
      ${deal === "sale" ? `<div class="mk-card"><span>Средняя квартира</span><b>${money(c.price)}</b><small>медиана цены</small></div>` : ""}
      ${c.rooms.map((r) => `<div class="mk-card"><span>${esc(r.label)}</span><b>${deal === "sale" ? money(r.price) : money(r.median) + "/мес"}</b>
        <small>${deal === "sale" ? `${n0(r.median)} ₽/м² · ` : ""}${n0(r.n)} шт.</small></div>`).join("")}`
      : `<p class="note">Пока мало объявлений ${deal === "rent" ? "об аренде" : "о продаже"} — статистика появится, когда их станет больше.</p>`;
    $("mkNote").textContent = data.since7 ? "" : `Динамика появится через неделю: каждый день сохраняем цены${data.first_day ? ` (с ${data.first_day.split("-").reverse().join(".")})` : ""}.`;

    const q = ($("mkQ").value || "").trim().toLowerCase().replace(/ё/g, "е");
    let rows = (data[kind] || []).filter((x) => !q || x.name.toLowerCase().replace(/ё/g, "е").includes(q));
    rows = rows.slice().sort((a, b) => {
      const av = a[sort.key], bv = b[sort.key];
      if (sort.key === "name") return sort.dir * a.name.localeCompare(b.name, "ru");
      return sort.dir * ((av ?? -Infinity) - (bv ?? -Infinity));
    });
    const max = Math.max(1, ...rows.map((x) => x.median));
    const th = (key, label) => `<th data-sort="${key}" class="${sort.key === key ? (sort.dir > 0 ? "asc" : "desc") : ""}">${label}</th>`;
    const link = (x) => `/?${deal === "rent" ? "deal=rent&" : ""}type=flat&${kind === "districts" ? "district" : "complex"}=${encodeURIComponent(x.name)}`;
    $("mkTable").innerHTML = `<thead><tr>${th("name", kind === "districts" ? "Район" : "ЖК")}${th("median", deal === "sale" ? "Цена м²" : "Аренда/мес")}
        ${deal === "sale" ? th("price", "Квартира") : ""}${th("n", "Объектов")}${th("ch7", "Неделя")}${th("ch30", "Месяц")}</tr></thead>
      <tbody>${rows.map((x) => `<tr data-href="${esc(link(x))}" tabindex="0">
        <td><a href="${esc(link(x))}">${esc(x.name)}</a></td>
        <td class="mk-val"><i style="width:${Math.round((x.median / max) * 100)}%"></i><span>${deal === "sale" ? n0(x.median) + " ₽" : money(x.median)}</span></td>
        ${deal === "sale" ? `<td>${money(x.price)}</td>` : ""}<td>${n0(x.n)}</td><td>${change(x.ch7)}</td><td>${change(x.ch30)}</td></tr>`).join("")
        || `<tr><td colspan="6" class="note">Ничего не нашлось.</td></tr>`}</tbody>`;
  }

  document.addEventListener("click", (e) => {
    const b = e.target.closest("button, th[data-sort], tr[data-href]");
    if (!b) return;
    if (b.dataset.deal) { deal = b.dataset.deal; writeUrl(); load(); return; }
    if (b.dataset.kind) { kind = b.dataset.kind; writeUrl(); render(); return; }
    if (b.dataset.sort) {
      sort = sort.key === b.dataset.sort ? { key: sort.key, dir: -sort.dir } : { key: b.dataset.sort, dir: b.dataset.sort === "name" ? 1 : -1 };
      render(); return;
    }
    if (b.dataset.href && !e.target.closest("a")) location.href = b.dataset.href;
  });
  $("mkQ").addEventListener("input", () => data && render());
  load();
})();
