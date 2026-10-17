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
    const photo = (d.style || {}).photo;   // фото агента из настроек — кругом над именем (как у агентств)
    if (!compact && photo) {
      return `<span class="pk-agent-photo" style="background-image:url('${esc(photo)}')"></span>
        <span class="pk-agent-who"><span class="pk-agent-label">Ваш агент</span><b>${esc(d.contact_name || "Агент")}</b>
        ${digits ? `<a class="pk-agent-tel" href="tel:${esc(d.contact_phone)}">${esc(d.contact_phone)}</a>` : ""}</span>
        ${digits ? `<span class="pk-agent-btns"><a class="pill" href="tel:${esc(d.contact_phone)}">Позвонить</a>
          <button type="button" class="pill light" data-max="${esc(d.contact_phone)}">Написать в MAX</button></span>` : ""}`;
    }
    return `${compact ? "" : `<span class="pk-agent-label">Ваш агент</span>`}<b>${esc(d.contact_name || "Агент")}</b>
      ${digits ? `<a class="pill" href="tel:${esc(d.contact_phone)}">Позвонить</a>
        <button type="button" class="pill light" data-max="${esc(d.contact_phone)}">Написать в MAX</button>` : ""}`;
  }

  async function load() {
    let d;
    try {
      // «Как видит клиент» из кабинета агента — ?as=agent: такое открытие не считаем просмотром клиента
      const asAgent = new URLSearchParams(location.search).get("as") === "agent";
      const r = await fetch(`/api/c/${encodeURIComponent(token)}${asAgent ? "?as=agent" : ""}`, { credentials: "same-origin" });
      if (!r.ok) throw new Error((await r.json()).detail);
      d = await r.json();
    } catch (e) {
      $("pkTitle").textContent = "Подборка не найдена";
      $("pkList").innerHTML = `<li class="pk-empty">${esc(e.message || "Возможно, агент её удалил.")}</li>`;
      return;
    }
    document.title = d.title;
    $("pkTitle").textContent = d.title;
    applyStyle(d.style || {});
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
        ${o.description ? `<details class="pk-desc" open><summary>Описание</summary><p>${esc(o.description)}</p></details>` : ""}
        ${o.is_active ? reactHTML(o) : ""}
      </div></li>`).join("") || `<li class="pk-empty">В подборке пока нет объектов.</li>`;
    window.__pick = d;
    drawMap(d.items);
  }

  // ответ клиента по объекту — агент сразу видит его у себя (статус, история, уведомление)
  const RE = [["like", "Нравится"], ["want_show", "Хочу посмотреть"], ["no", "Не подходит"], ["question", "Задать вопрос"]];
  const RE_OK = { like: "Агент увидит, что вам понравилось.", want_show: "Агент свяжется, чтобы договориться о показе.",
    no: "Понятно — агент учтёт и подберёт другие варианты.", question: "Вопрос отправлен — агент ответит." };
  function reactHTML(o) {
    return `<div class="pk-react" data-lid="${o.id}"><p class="pk-react-h">Как вам этот вариант?</p>
      <div class="pk-react-b">${RE.map(([k, t]) => `<button type="button" class="pk-rb r-${k}${o.reaction === k ? " on" : ""}" data-re="${k}">${t}</button>`).join("")}</div>
      <form class="pk-q" hidden><textarea name="text" rows="2" maxlength="1000" placeholder="Ваш вопрос об этом объекте" required></textarea>
        <input name="phone" inputmode="tel" maxlength="30" placeholder="Телефон для ответа (необязательно)">
        <button type="submit" class="pk-send">Отправить вопрос</button></form>
      <p class="pk-react-ok"${o.reaction ? "" : " hidden"}>${o.reaction ? RE_OK[o.reaction] : ""}</p></div>`;
  }
  const asAgent = new URLSearchParams(location.search).get("as") === "agent";
  async function sendReact(box, kind, text = "", phone = "") {
    const ok = box.querySelector(".pk-react-ok");
    if (asAgent) { ok.hidden = false; ok.textContent = "Это вид клиента: ответы ставит сам клиент по своей ссылке."; return; }
    const r = await fetch(`/api/c/${encodeURIComponent(token)}/react`, { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ listing_id: Number(box.dataset.lid), kind, text, phone }) });
    const d = await r.json().catch(() => ({}));
    ok.hidden = false;
    if (!r.ok) { ok.textContent = d.detail || "Не получилось отправить — попробуйте ещё раз."; return; }
    box.querySelectorAll("[data-re]").forEach((b) => b.classList.toggle("on", b.dataset.re === kind));
    box.querySelector(".pk-q").hidden = true;
    ok.textContent = RE_OK[kind];
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-re]");
    if (!b) return;
    const box = b.closest(".pk-react");
    if (b.dataset.re === "question") { const f = box.querySelector(".pk-q"); f.hidden = !f.hidden; if (!f.hidden) f.text.focus(); return; }
    sendReact(box, b.dataset.re);
  });
  document.addEventListener("submit", (e) => {
    const f = e.target.closest(".pk-q");
    if (!f) return;
    e.preventDefault();
    if (!f.text.value.trim()) return;
    sendReact(f.closest(".pk-react"), "question", f.text.value.trim(), f.phone.value.trim());
  });

  // вид подборки из настроек агента: цвет, обложка, подпись (имя или агентство)
  const PALETTE = { gold: "#e3cb8f", teal: "#5fd0c0", sky: "#7cc4f5", violet: "#b9a3f5", coral: "#f29a7e", rose: "#f08fb6", lime: "#b6db7a", slate: "#9fb3b1" };
  function applyStyle(st) {
    const c = PALETTE[st.color] || (/^#[0-9a-f]{6}$/i.test(st.color || "") ? st.color : PALETTE.gold);
    document.documentElement.style.setProperty("--accent", c);
    document.documentElement.style.setProperty("--gold", c);
    $("pkBrand").textContent = st.brand || "Подборка объектов";
    if (st.cover) {
      $("pkCover").hidden = false;
      $("pkCover").style.backgroundImage = `url("${st.cover.replace(/"/g, "")}")`;
      $("pkHead").classList.add("has-cover");
    }
  }

  function drawMap(items) {
    const pts = items.filter((o) => o.lat && o.lon);
    if (!pts.length || !window.L) return;
    $("pkMap").hidden = false;
    const map = L.map("pkMap", { scrollWheelZoom: false, attributionControl: false });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19 }).addTo(map);
    L.control.attribution({ prefix: false }).addAttribution("© OpenStreetMap").addTo(map);
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
