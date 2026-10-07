/* Подборка для клиента /c/<код>: объекты с фото, параметрами, картой и контактом агента. API: /api/c/<код> */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const token = location.pathname.split("/").pop();
  const num = (x, d = 0) => Number(x).toLocaleString("ru-RU", { maximumFractionDigits: d });
  const price = (p, deal) => (!p ? "Цена по запросу" : deal === "rent" ? `${num(p)} ₽/мес`
    : `${(p / 1e6).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} млн ₽`);
  let gallery = [], gi = 0;

  function facts(o) {
    const rooms = o.room_kind === "studio" || o.rooms === 0 ? "студия" : o.rooms ? `${o.rooms}-комн.` : null;
    return [rooms, o.area && `${num(o.area, 1)} м²`, o.land && `участок ${num(o.land, 1)} сот.`,
      o.floor && `${o.floor}${o.floors ? "/" + o.floors : ""} этаж`].filter(Boolean).join(" · ");
  }
  const place = (o) => [o.complex && `ЖК ${o.complex}`, o.district, o.settlement,
    o.street && `ул. ${o.street}${o.house ? ", " + o.house : ""}`].filter(Boolean).join(" · ");
  const yandex = (o) => `https://yandex.ru/maps/35/krasnodar/?text=${encodeURIComponent(
    o.street ? `${o.settlement || "Краснодар"}, ${o.street} ${o.house || ""}` : o.complex ? `ЖК ${o.complex}, Краснодар` : place(o))}`;

  function agentHTML(d, compact) {
    if (!d.contact_name && !d.contact_phone) return "";
    const digits = (d.contact_phone || "").replace(/\D/g, "");
    return `${compact ? "" : `<span class="pk-agent-label">Ваш агент</span>`}<b>${esc(d.contact_name || "Агент")}</b>
      ${digits ? `<a class="pill" href="tel:${esc(d.contact_phone)}">Позвонить</a>
        <a class="pill light" href="https://wa.me/${digits}" target="_blank" rel="noopener">WhatsApp</a>` : ""}`;
  }

  async function load() {
    let d;
    try {
      const r = await fetch(`/api/c/${encodeURIComponent(token)}`);
      if (!r.ok) throw new Error((await r.json()).detail);
      d = await r.json();
    } catch (e) {
      $("pkTitle").textContent = "Подборка не найдена";
      $("pkList").innerHTML = `<li class="pk-empty">${esc(e.message || "Возможно, агент её удалил.")}</li>`;
      return;
    }
    document.title = `${d.title} · 1+1`;
    $("pkTitle").textContent = d.title;
    if (d.note) { $("pkNote").hidden = false; $("pkNote").textContent = d.note; }
    const agent = agentHTML(d);
    if (agent) { $("pkAgent").hidden = false; $("pkAgent").innerHTML = agent; $("pkBar").hidden = false; $("pkBar").innerHTML = agentHTML(d, true); }
    $("pkList").innerHTML = d.items.map((o, i) => `<li class="pk-item${o.is_active ? "" : " gone"}" id="o${o.id}">
      ${o.photos.length ? `<div class="pk-gal">${o.photos.slice(0, 12).map((u, j) => `<button type="button" data-ph="${i}:${j}"><img src="${esc(u)}" alt="" loading="lazy"></button>`).join("")}</div>` : ""}
      <div class="pk-body">
        <span class="pk-num">${i + 1}</span>
        <div class="pk-price">${esc(price(o.price, o.deal))}${o.price_m2 && o.deal !== "rent" ? `<small>${num(o.price_m2)} ₽/м²</small>` : ""}</div>
        <h2>${esc(o.complex ? `ЖК ${o.complex}` : o.title)}</h2>
        <p class="pk-facts">${esc(o.complex ? o.title : facts(o))}</p>
        <p class="pk-place">${esc(place(o))} · <a href="${esc(yandex(o))}" target="_blank" rel="noopener">на карте ↗</a></p>
        ${o.is_active ? "" : `<p class="pk-gone">Объект уже снят с продажи — уточните у агента.</p>`}
        ${o.note ? `<p class="pk-comment"><b>Комментарий агента:</b> ${esc(o.note)}</p>` : ""}
        ${o.description ? `<details class="pk-desc"><summary>Описание</summary><p>${esc(o.description)}</p></details>` : ""}
      </div></li>`).join("") || `<li class="pk-empty">В подборке пока нет объектов.</li>`;
    window.__pick = d;
    drawMap(d.items);
  }

  function drawMap(items) {
    const pts = items.filter((o) => o.lat && o.lon);
    if (!pts.length || !window.L) return;
    $("pkMap").hidden = false;
    const map = L.map("pkMap", { scrollWheelZoom: false, attributionControl: true });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(map);
    const b = [];
    pts.forEach((o) => {
      const i = items.indexOf(o) + 1;
      L.marker([o.lat, o.lon], { icon: L.divIcon({ className: "pk-pin-wrap", html: `<span class="pk-pin">${i}</span>`, iconSize: [30, 30], iconAnchor: [15, 15] }) })
        .addTo(map).on("click", () => document.getElementById(`o${o.id}`).scrollIntoView({ behavior: "smooth", block: "start" }));
      b.push([o.lat, o.lon]);
    });
    if (b.length === 1) map.setView(b[0], 15); else map.fitBounds(b, { padding: [30, 30] });
  }

  function showPhoto() { $("pkPhotoImg").src = gallery[gi]; }
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-ph]");
    if (b) {
      const [i, j] = b.dataset.ph.split(":").map(Number);
      gallery = window.__pick.items[i].photos; gi = j; showPhoto(); $("pkPhoto").showModal(); return;
    }
    if (e.target.id === "pkPhotoClose" || e.target.id === "pkPhoto") $("pkPhoto").close();
    if (e.target.id === "pkPrev") { gi = (gi - 1 + gallery.length) % gallery.length; showPhoto(); }
    if (e.target.id === "pkNext") { gi = (gi + 1) % gallery.length; showPhoto(); }
  });
  document.addEventListener("keydown", (e) => {
    if (!$("pkPhoto").open) return;
    if (e.key === "ArrowLeft") $("pkPrev").click();
    if (e.key === "ArrowRight") $("pkNext").click();
  });
  load();
})();
