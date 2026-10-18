/* Подборка для клиента /c/<код> — v4 (18.10): фото во всю ширину (листаются пальцем), цена, характеристики плитками,
   адрес, комментарий агента, текст, ответ клиента; карта рядом (компьютер) или «На карте» (телефон); агент — в шапке
   и в нижней панели. API: /api/c/<код>. Никаких упоминаний сервиса. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ic = (n) => (window.ic ? window.ic(n) : "");
  const token = location.pathname.split("/").pop();
  const sp = new URLSearchParams(location.search);
  const asAgent = sp.get("as") === "agent";
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const num = (x, d = 0) => Number(x).toLocaleString("ru-RU", { maximumFractionDigits: d });
  const price = (p, deal) => (!p ? "Цена по запросу" : deal === "rent" ? `${num(p)} ₽/мес`
    : `${(p / 1e6).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} млн ₽`);
  const TYPE = { flat: "Квартира", new: "Квартира", room: "Комната", house: "Дом", land: "Участок", commercial: "Коммерческая недвижимость" };
  const ARROW = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${d === "prev" ? "M15 5l-7 7 7 7" : "M9 5l7 7-7 7"}"/></svg>`;
  const plural = (n, a, b, c) => `${n} ${n % 10 === 1 && n % 100 !== 11 ? a : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? b : c}`;
  let data = null, map = null, pins = {}, lb = { photos: [], i: 0 };

  // «+79180000000» → «+7 918 000-00-00»
  function prettyPhone(p) {
    let d = String(p || "").replace(/\D/g, "");
    if (d.length === 11 && d[0] === "8") d = "7" + d.slice(1);
    if (d.length === 10) d = "7" + d;
    return d.length === 11 && d[0] === "7" ? `+7 ${d.slice(1, 4)} ${d.slice(4, 7)}-${d.slice(7, 9)}-${d.slice(9)}` : p;
  }

  // ─── агент ─────────────────────────────────────────────────
  function avaHTML(d) {
    const photo = (d.style || {}).photo;
    const letter = (d.contact_name || "А").trim()[0].toUpperCase();
    return photo ? `<span class="pv-ava" style="background-image:url('${esc(String(photo).replace(/['"()\\]/g, ""))}')" role="img" aria-label="${esc(d.contact_name || "Агент")}"></span>`
      : `<span class="pv-ava" aria-hidden="true">${esc(letter)}</span>`;
  }
  function agentHTML(d, bar) {
    if (!d.contact_name && !d.contact_phone) return "";
    const tel = d.contact_phone ? esc(d.contact_phone) : "";
    if (bar) {
      return `${avaHTML(d)}<span class="pv-who"><small>Ваш агент</small><b>${esc(d.contact_name || "Агент")}</b></span>
        ${tel ? `<a class="pv-btn icon soft" href="tel:${tel}" aria-label="Позвонить">${ic("phone")}</a>
          <button type="button" class="pv-btn" data-max="${tel}">Написать</button>` : ""}`;
    }
    return `${avaHTML(d)}<span class="pv-who"><small>Ваш агент</small><b>${esc(d.contact_name || "Агент")}</b>
        ${tel ? `<a href="tel:${tel}">${esc(prettyPhone(d.contact_phone))}</a>` : ""}</span>
      ${tel ? `<span class="pv-agent-btns"><a class="pv-btn" href="tel:${tel}">${ic("phone")}Позвонить</a>
        <button type="button" class="pv-btn soft" data-max="${tel}">Написать в MAX</button></span>` : ""}`;
  }

  // ─── «Как видит клиент» — полоса «← Назад к подборке» (только агенту) ───
  if (asAgent) {
    const back = sp.get("back") || "";
    const ok = /^https?:\/\/[^/]+\/picks(\?open=\d+)?$/.test(back);
    const bar = document.createElement("div");
    bar.className = "pk-agentbar";
    bar.innerHTML = `<a href="${ok ? back.replace(/"/g, "") : "#"}" class="pk-back">← Назад к подборке</a><span>Так подборку видит клиент</span>`;
    bar.querySelector("a").addEventListener("click", (e) => {
      if (history.length > 1) { e.preventDefault(); history.back(); } else if (!ok) { e.preventDefault(); window.close(); }
    });
    document.body.prepend(bar);
  }

  // ─── оформление из настроек агента ─────────────────────────
  const PALETTE = { gold: "#e3cb8f", teal: "#5fd0c0", sky: "#7cc4f5", violet: "#b9a3f5", coral: "#f29a7e", rose: "#f08fb6", lime: "#b6db7a", slate: "#9fb3b1" };
  function applyStyle(st) {
    const c = PALETTE[st.color] || (/^#[0-9a-f]{6}$/i.test(st.color || "") ? st.color : PALETTE.gold);
    document.documentElement.style.setProperty("--accent", c);
    $("pkBrand").textContent = st.brand || "Подборка объектов";
    if (st.cover) {
      $("pkCover").hidden = false;
      $("pkCover").style.backgroundImage = `url("${String(st.cover).replace(/["\\]/g, "")}")`;
      $("pkHead").classList.add("has-cover");
    }
  }

  // ─── карточка объекта ──────────────────────────────────────
  function specs(o) {
    const s = [];
    if (o.room_kind === "studio" || o.rooms === 0) s.push(["Комнат", "Студия"]);
    else if (o.rooms) s.push(["Комнат", o.room_kind === "euro" ? `${o.rooms} евро` : String(o.rooms)]);
    if (o.area) s.push(["Площадь", `${num(o.area, 1)} м²`]);
    if (o.land) s.push(["Участок", `${num(o.land, 1)} сот.`]);
    if (o.floor) s.push(["Этаж", o.floors ? `${o.floor} из ${o.floors}` : String(o.floor)]);
    else if (o.floors && (o.type === "house" || o.type === "commercial")) s.push(["Этажей", String(o.floors)]);
    return s.length ? `<div class="pv-specs">${s.map(([k, v]) => `<div class="pv-spec"><small>${k}</small><b>${esc(v)}</b></div>`).join("")}</div>` : "";
  }
  const street = (o) => (o.street ? `ул. ${o.street}${o.house ? ", " + o.house : ""}` : "");
  const placeText = (o) => [o.district, street(o), o.settlement].filter(Boolean).join(" · ");
  const heading = (o) => (o.complex ? `ЖК ${o.complex}` : street(o) || o.district || o.title || "Объект");
  const yandex = (o) => `https://yandex.ru/maps/35/krasnodar/?text=${encodeURIComponent(
    o.street ? `${o.settlement || "Краснодар"}, ${o.street} ${o.house || ""}` : o.complex ? `ЖК ${o.complex}, Краснодар` : [o.district, o.settlement || "Краснодар"].filter(Boolean).join(", "))}`;

  function mediaHTML(o, i) {
    const ph = (o.photos || []).slice(0, 15);
    if (!ph.length) {
      return `<div class="pv-media"><span class="pv-num">${i + 1}</span>${o.is_active ? "" : `<span class="pv-tag">Снят с продажи</span>`}
        <div class="pv-nophoto"><svg viewBox="0 0 48 48" aria-hidden="true"><path d="M8 22L24 9l16 13v17a2 2 0 0 1-2 2H10a2 2 0 0 1-2-2z"/><path d="M19 41V29h10v12"/></svg>Фото — у агента</div></div>`;
    }
    return `<div class="pv-media" data-media="${i}"><span class="pv-num">${i + 1}</span>${o.is_active ? "" : `<span class="pv-tag">Снят с продажи</span>`}
      <div class="pv-track">${ph.map((u, j) => `<button type="button" data-ph="${i}:${j}" aria-label="Открыть фото ${j + 1} из ${ph.length}">
        <img src="${esc(u)}" alt="" ${j ? 'loading="lazy"' : ""} decoding="async"></button>`).join("")}</div>
      ${ph.length > 1 ? `<button type="button" class="pv-arrow prev" data-slide="-1" aria-label="Предыдущее фото">${ARROW("prev")}</button>
        <button type="button" class="pv-arrow next" data-slide="1" aria-label="Следующее фото">${ARROW("next")}</button>
        <div class="pv-dots">${ph.slice(0, 9).map((_, j) => `<i${j ? "" : ' class="on"'}></i>`).join("")}</div>` : ""}</div>`;
  }

  const RE = [["like", "Нравится", "heart"], ["want_show", "Посмотреть", "calendar"], ["no", "Не подходит", "x"], ["question", "Вопрос", "chat"]];
  const RE_OK = { like: "Агент увидит, что вам понравилось.", want_show: "Агент свяжется, чтобы договориться о показе.",
    no: "Понятно — агент учтёт и подберёт другие варианты.", question: "Вопрос отправлен — агент ответит." };
  function reactHTML(o) {
    return `<div class="pv-react" data-lid="${o.id}"><p class="pv-react-h">Как вам этот вариант?</p>
      <div class="pv-react-b">${RE.map(([k, t, icon]) => `<button type="button" class="pv-rb r-${k}${o.reaction === k ? " on" : ""}" data-re="${k}"
        aria-pressed="${o.reaction === k}">${ic(icon)}${t}</button>`).join("")}</div>
      <form class="pv-q" hidden><textarea name="text" rows="3" maxlength="1000" placeholder="Ваш вопрос об этом объекте" required></textarea>
        <input name="phone" type="tel" inputmode="tel" maxlength="30" placeholder="Телефон для ответа — необязательно" autocomplete="tel">
        <button type="submit" class="pv-btn">Отправить вопрос</button>
        <p class="pv-q-note">Вопрос и номер увидит только агент, который прислал подборку, — чтобы ответить вам.</p></form>
      <p class="pv-react-ok"${o.reaction ? "" : " hidden"}>${ic("check-circle")}<span>${o.reaction ? RE_OK[o.reaction] : ""}</span></p></div>`;
  }

  function cardHTML(o, i) {
    const kind = [TYPE[o.type] || "", o.deal === "rent" ? "аренда" : ""].filter(Boolean).join(" · ");
    const pl = placeText(o);
    return `<li class="pv-card${o.is_active ? "" : " gone"}${reduce ? "" : " rise"}" id="o${o.id}" data-i="${i}">
      ${mediaHTML(o, i)}
      <div class="pv-body">
        <div class="pv-price${o.price ? "" : " ask"}">${esc(price(o.price, o.deal))}${o.price_m2 && o.price && o.deal !== "rent" ? `<small>${num(o.price_m2)} ₽/м²</small>` : ""}</div>
        <h2>${esc(heading(o))}</h2>
        ${kind ? `<p class="pv-kind">${esc(kind)}</p>` : ""}
        ${specs(o)}
        ${pl || o.complex ? `<p class="pv-place">${ic("pin")}<span>${esc(pl || `ЖК ${o.complex}`)}${o.approx ? " · точный адрес — у агента" : ""}
          · <a href="${esc(yandex(o))}" target="_blank" rel="noopener noreferrer">На карте</a></span></p>` : ""}
        ${o.is_active ? "" : `<p class="pv-gone">Этот объект уже сняли с продажи — уточните у агента, есть ли похожие.</p>`}
        ${o.note ? `<div class="pv-comment"><b>Комментарий агента</b>${esc(o.note)}</div>` : ""}
        ${o.description ? `<div class="pk-desc clamp"><p>${esc(o.description)}</p><button type="button" data-more hidden>Читать полностью</button></div>` : ""}
        ${o.is_active ? reactHTML(o) : ""}
      </div></li>`;
  }

  // ─── загрузка ──────────────────────────────────────────────
  async function load() {
    try {
      const r = await fetch(`/api/c/${encodeURIComponent(token)}${asAgent ? "?as=agent" : ""}`, { credentials: "same-origin" });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail);
      data = await r.json();
    } catch (e) {
      $("pkTitle").classList.remove("pv-skel-line");
      $("pkTitle").textContent = "Подборка не найдена";
      $("pkMeta").textContent = e.message || "Возможно, агент её удалил. Попросите прислать новую ссылку.";
      $("pkList").innerHTML = "";
      return;
    }
    const d = data;
    document.title = d.title;
    $("pkTitle").classList.remove("pv-skel-line");
    $("pkTitle").textContent = d.title;
    applyStyle(d.style || {});
    const n = d.items.length;
    const upd = d.updated ? new Date(d.updated * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "long" }) : "";
    $("pkMeta").textContent = [plural(n, "объект", "объекта", "объектов"), upd && `обновлено ${upd}`].filter(Boolean).join(" · ");
    $("pkCount").textContent = n ? `· ${n}` : "";
    if (d.note) { $("pkNote").hidden = false; $("pkNote").textContent = d.note; }
    const agent = agentHTML(d);
    if (agent) {
      $("pkAgent").hidden = false; $("pkAgent").innerHTML = agent;
      $("pkBar").hidden = false; $("pkBar").innerHTML = agentHTML(d, true);
      watchBar();
    }
    $("pkList").innerHTML = d.items.map(cardHTML).join("") || `<li class="pv-empty">В подборке пока нет объектов.</li>`;
    afterRender();
    drawMap(d.items);
  }

  function afterRender() {
    // фото: проявляются после загрузки
    document.querySelectorAll(".pv-track img").forEach((img) => {
      if (img.complete && img.naturalWidth) img.classList.add("ld");
      else { img.addEventListener("load", () => img.classList.add("ld"), { once: true }); img.addEventListener("error", () => img.classList.add("ld"), { once: true }); }
    });
    // точки под фото — по прокрутке
    document.querySelectorAll(".pv-media[data-media]").forEach((m) => {
      const tr = m.querySelector(".pv-track"), dots = [...m.querySelectorAll(".pv-dots i")];
      if (!dots.length) return;
      let raf = 0;
      tr.addEventListener("scroll", () => {
        cancelAnimationFrame(raf);
        raf = requestAnimationFrame(() => {
          const k = Math.round(tr.scrollLeft / tr.clientWidth), on = Math.min(k, dots.length - 1);
          dots.forEach((x, j) => x.classList.toggle("on", j === on));
        });
      }, { passive: true });
    });
    // длинный текст — «Читать полностью»
    document.querySelectorAll(".pk-desc.clamp").forEach((b) => {
      const p = b.querySelector("p");
      if (p.scrollHeight > p.clientHeight + 4) b.querySelector("[data-more]").hidden = false; else b.classList.remove("clamp");
    });
    // карточки мягко появляются при прокрутке
    if (!reduce && "IntersectionObserver" in window) {
      const io = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } }),
        { rootMargin: "0px 0px -8% 0px", threshold: .08 });
      document.querySelectorAll(".pv-card.rise").forEach((c) => io.observe(c));
    } else document.querySelectorAll(".pv-card.rise").forEach((c) => c.classList.add("in"));
  }

  // нижняя панель с агентом — когда карточка агента в шапке ушла с экрана
  function watchBar() {
    if (!("IntersectionObserver" in window)) { $("pkBar").classList.add("in"); return; }
    new IntersectionObserver(([e]) => $("pkBar").classList.toggle("in", !e.isIntersecting && e.boundingClientRect.top < 0))
      .observe($("pkAgent"));
  }

  // ─── карта ─────────────────────────────────────────────────
  function drawMap(items) {
    const pts = items.filter((o) => o.lat && o.lon);
    if (!pts.length || !window.L) { $("pkMain").classList.add("no-map"); return; }
    $("pkMapCol").hidden = false; $("pkSwitch").hidden = false;
    map = L.map("pkMap", { scrollWheelZoom: false, attributionControl: false, zoomControl: true });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19 }).addTo(map);
    L.control.attribution({ prefix: false }).addAttribution("© OpenStreetMap").addTo(map);
    const b = [];
    pts.forEach((o) => {
      const i = items.indexOf(o) + 1;
      const m = L.marker([o.lat, o.lon], { icon: L.divIcon({ className: "", html: `<span class="pv-pin" data-pin="${o.id}">${i}</span>`, iconSize: [30, 30], iconAnchor: [15, 15] }) })
        .addTo(map).on("click", () => focusCard(o.id));
      pins[o.id] = m; b.push([o.lat, o.lon]);
    });
    if (b.length === 1) map.setView(b[0], 15); else map.fitBounds(b, { padding: [40, 40] });
    // компьютер: наведение на карточку подсвечивает метку
    document.querySelectorAll(".pv-card").forEach((c) => {
      const id = c.id.slice(1);
      c.addEventListener("mouseenter", () => pinOn(id, true));
      c.addEventListener("mouseleave", () => pinOn(id, false));
    });
  }
  function pinOn(id, on) {
    const el = document.querySelector(`[data-pin="${id}"]`);
    if (el) el.classList.toggle("on", on);
    if (on && pins[id]) pins[id].setZIndexOffset(1000); else if (pins[id]) pins[id].setZIndexOffset(0);
  }
  function setView(v) {
    const main = $("pkMain"), sw = $("pkSwitch");
    main.classList.toggle("show-map", v === "map");
    sw.classList.toggle("map", v === "map");
    sw.querySelectorAll("button").forEach((b) => { const on = b.dataset.view === v; b.classList.toggle("on", on); b.setAttribute("aria-selected", on); });
    if (v === "map" && map) setTimeout(() => map.invalidateSize(), 30);
  }
  function focusCard(id) {
    setView("list");
    const c = $(`o${id}`);
    if (!c) return;
    c.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
    c.classList.add("hl");
    setTimeout(() => c.classList.remove("hl"), 1600);
  }

  // ─── просмотр фото на весь экран ───────────────────────────
  function openLb(photos, i) {
    lb = { photos, i };
    $("pkLbTrack").innerHTML = photos.map((u) => `<figure><img src="${esc(u)}" alt="" decoding="async"></figure>`).join("");
    $("pkPhoto").showModal();
    const tr = $("pkLbTrack");
    tr.scrollTo({ left: i * tr.clientWidth, behavior: "instant" });
    lbCount();
  }
  function lbCount() { $("pkLbCount").textContent = lb.photos.length > 1 ? `${lb.i + 1} из ${lb.photos.length}` : ""; }
  function lbGo(k) {
    const tr = $("pkLbTrack");
    lb.i = (lb.i + k + lb.photos.length) % lb.photos.length;
    tr.scrollTo({ left: lb.i * tr.clientWidth, behavior: reduce ? "instant" : "smooth" });
    lbCount();
  }
  $("pkLbTrack").addEventListener("scroll", () => {
    const tr = $("pkLbTrack"), k = Math.round(tr.scrollLeft / tr.clientWidth);
    if (k !== lb.i) { lb.i = k; lbCount(); }
  }, { passive: true });

  // ─── ответы клиента ────────────────────────────────────────
  async function sendReact(box, kind, text = "", phone = "") {
    const ok = box.querySelector(".pv-react-ok"), okT = ok.querySelector("span");
    if (asAgent) { ok.hidden = false; okT.textContent = "Это вид клиента: ответы ставит сам клиент по своей ссылке."; return; }
    const r = await fetch(`/api/c/${encodeURIComponent(token)}/react`, { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ listing_id: Number(box.dataset.lid), kind, text, phone }) });
    const d = await r.json().catch(() => ({}));
    ok.hidden = false;
    if (!r.ok) { okT.textContent = d.detail || "Не получилось отправить — попробуйте ещё раз."; return; }
    box.querySelectorAll("[data-re]").forEach((b) => { const on = b.dataset.re === kind; b.classList.toggle("on", on); b.setAttribute("aria-pressed", on); });
    box.querySelector(".pv-q").hidden = true;
    okT.textContent = RE_OK[kind];
    if (navigator.vibrate) navigator.vibrate(8);
  }

  document.addEventListener("click", (e) => {
    const t = e.target;
    const v = t.closest("[data-view]");
    if (v) { setView(v.dataset.view); return; }
    const re = t.closest("[data-re]");
    if (re) {
      const box = re.closest(".pv-react");
      if (re.dataset.re === "question") { const f = box.querySelector(".pv-q"); f.hidden = !f.hidden; if (!f.hidden) f.text.focus(); return; }
      sendReact(box, re.dataset.re); return;
    }
    const sl = t.closest("[data-slide]");
    if (sl) { const tr = sl.parentElement.querySelector(".pv-track"); tr.scrollBy({ left: Number(sl.dataset.slide) * tr.clientWidth, behavior: "smooth" }); return; }
    const ph = t.closest("[data-ph]");
    if (ph) { const [i, j] = ph.dataset.ph.split(":").map(Number); openLb(data.items[i].photos.slice(0, 15), j); return; }
    const more = t.closest("[data-more]");
    if (more) { const b = more.parentElement; b.classList.remove("clamp"); more.hidden = true; return; }
    if (t.closest("#pkPhotoClose")) { $("pkPhoto").close(); return; }
    if (t.closest("#pkPrev")) { lbGo(-1); return; }
    if (t.closest("#pkNext")) { lbGo(1); return; }
    if (t.closest("#pkPhoto") && t.tagName === "FIGURE") $("pkPhoto").close();   // нажали мимо фото — закрыть
  });
  document.addEventListener("submit", (e) => {
    const f = e.target.closest(".pv-q");
    if (!f) return;
    e.preventDefault();
    if (!f.text.value.trim()) return;
    sendReact(f.closest(".pv-react"), "question", f.text.value.trim(), f.phone.value.trim());
  });
  document.addEventListener("keydown", (e) => {
    if (!$("pkPhoto").open) return;
    if (e.key === "ArrowLeft") lbGo(-1);
    if (e.key === "ArrowRight") lbGo(1);
  });
  load();
})();
