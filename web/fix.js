/* 1+1 · экран разбора: ЖК/район и точки на карте — по одному объекту, с подсказками и обучением.
   Пропущенные уходят в конец очереди; «Назад» — к предыдущему; Enter — сохранить и дальше. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const KINDS = {
    place: [["nodistrict", "Без района"], ["nocomplex", "Квартиры без ЖК"], ["fixed", "Уже поправленные"]],
    geo: [["none", "Нет на карте"], ["approx", "Примерные"], ["pending", "Ещё ищем"]],
  };
  const sp = new URLSearchParams(location.search);
  let mode = sp.get("mode") === "geo" ? "geo" : "place";
  let kind = sp.get("kind") || KINDS[mode][0][0];
  let item = null, left = 0, districts = [];
  let map = null, marker = null;
  let chosenDistrict = null;
  let extraDistricts = [];
  let complexes = [];

  // Состояние сессии разбора: разобранные, пропущенные (в конец очереди), история для «Назад»
  const key = () => `fix:${mode}:${kind}`;
  function load() {
    try { return JSON.parse(sessionStorage.getItem(key())) || { done: [], skipped: [], history: [] }; }
    catch { return { done: [], skipped: [], history: [] }; }
  }
  let ss = load();
  const save = () => { try { sessionStorage.setItem(key(), JSON.stringify(ss)); } catch { /* */ } };

  let toastTimer = 0;
  function toast(text, ms = 2600) {
    const t = $("toast");
    t.textContent = text;
    try { t.hidePopover(); t.showPopover(); } catch { /* */ }
    requestAnimationFrame(() => t.classList.add("show"));
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove("show"), ms);
  }
  async function call(url, body) {
    const opts = { credentials: "same-origin", method: body ? "POST" : "GET" };
    if (body) { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(body); }
    // База на секунду занята фоновой работой (503) — тихо повторяем, а не показываем ошибку
    let r = await fetch(url, opts);
    for (let i = 0; i < 4 && r.status === 503; i++) {
      await new Promise((res) => setTimeout(res, 1500 * (i + 1)));
      r = await fetch(url, opts);
    }
    let d = {};
    try { d = await r.json(); } catch { /* */ }
    return { ok: r.ok, status: r.status, data: d };
  }
  const price = (p, deal) => !p ? "цена не указана" : deal === "rent" ? `${p.toLocaleString("ru-RU")} ₽/мес`
    : `${(p / 1e6).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} млн ₽`;

  // ─── очередь ──────────────────────────────────────────────────────────
  async function fetchItem(id) {
    const q = new URLSearchParams({ mode, kind });
    if (id) q.set("id", id);
    else q.set("exclude", [...ss.done, ...ss.skipped, ...(item ? [item.id] : [])].join(","));
    const r = await call(`/api/admin/fix?${q}`);
    if (r.status === 403 || r.status === 401) { $("fxDenied").hidden = false; return null; }
    if (!r.ok) { toast(r.data.detail || "Не получилось загрузить"); return null; }
    left = r.data.left;
    districts = r.data.districts || districts;
    if (r.data.complexes) complexes = r.data.complexes;   // все ЖК по алфавиту: справочник + встречавшиеся
    return r.data.item;
  }
  async function next() {
    let it = await fetchItem();
    if (!it && ss.skipped.length) {
      // Новых нет — по кругу идём по пропущенным, начиная с самого давнего
      const id = ss.skipped.shift();
      save();
      it = await fetchItem(id);
    }
    show(it);
  }
  async function back() {
    const id = ss.history.pop();
    save();
    if (!id) { toast("Это первый объект в этой сессии"); return; }
    show(await fetchItem(id));
  }
  function skip() {
    if (!item) return;
    if (!ss.skipped.includes(item.id)) ss.skipped.push(item.id);
    ss.history.push(item.id);
    save();
    next();
  }

  // ─── показ объекта ────────────────────────────────────────────────────
  function show(it) {
    item = it;
    renderTop();
    $("fxCard").hidden = !it;
    $("fxDone").hidden = !!it;
    if (!it) {
      $("fxDone").innerHTML = `Очередь разобрана 🎉 <br><span class="note">Разобрано за сессию: ${ss.done.length}</span>`;
      return;
    }
    $("fxTitle").textContent = it.title;
    $("fxPrice").textContent = price(it.price, it.deal);
    $("fxNow").innerHTML = [
      it.complex ? `ЖК <b>${esc(it.complex)}</b>` : "ЖК —",
      it.district ? `район <b>${esc([it.district, ...(it.extra_districts || [])].join(", "))}</b>` : "район —",
      it.street ? `ул. ${esc(it.street)}${it.house ? ", " + esc(it.house) : ""}` : "",
      it.settlement ? esc(it.settlement) : "",
    ].filter(Boolean).join(" · ");
    $("fxText").textContent = it.text;
    $("fxOpen").href = `/?open=${it.id}`;
    $("fxYaAll").href = yaHref(it);
    $("fxPhoto").hidden = !it.photo;
    if (it.photo) $("fxPhoto").src = it.photo;
    // карточка целиком: все фото, параметры, номера
    const photos = it.photos || [];
    $("fxGal").innerHTML = photos.length > 1 ? photos.map((u) => `<a href="${esc(u)}" target="_blank" rel="noopener"><img src="${esc(u)}" alt="" loading="lazy"></a>`).join("") : "";
    const rooms = it.rooms == null ? null : it.rooms === 0 ? "студия" : `${it.rooms}-комн.`;
    $("fxFacts").innerHTML = [rooms, it.area && `${it.area} м²`, it.land && `участок ${it.land} сот.`,
      it.floor && `этаж ${it.floor}${it.floors ? "/" + it.floors : ""}`, it.deal === "rent" ? "аренда" : "продажа",
      it.source === "feed" ? "фид СТРЕЛ" : it.source === "own" ? "свой объект агента" : ""].filter(Boolean)
      .map((x) => `<span class="chip">${esc(x)}</span>`).join("");
    $("fxPhones").innerHTML = (it.phones || []).map((p) => `<span class="fx-phone">
      <button type="button" class="fx-phone-num" data-phone-base="${esc(p)}" title="Все объекты этого номера">${esc(p)}</button>
      <button type="button" class="chip danger" data-ban-phone="${esc(p)}" title="Скрыть все объекты и сообщения от этого номера и не принимать новые">🚫 Всё от номера</button></span>`).join("")
      + `<button type="button" class="chip danger fx-del-one" data-del-one="${it.id}">🗑 Удалить только этот объект</button>`;
    const rent = it.deal === "rent";
    $("fxPriceLabel").textContent = rent ? "Цена, тыс. ₽/мес" : "Цена, млн ₽";
    $("fxPrice").value = it.price ? String(+(it.price / (rent ? 1000 : 1e6)).toFixed(rent ? 0 : 3)).replace(".", ",") : "";
    $("fxPrice").dataset.orig = $("fxPrice").value;
    $("fxPlace").hidden = mode !== "place";
    $("fxHide").hidden = mode !== "geo";
    $("fxGeoBox").classList.toggle("geo-mode", mode === "geo");
    $("fxGeoBox").open = mode === "geo";
    $("fxGeoSave").hidden = mode === "geo";
    $("fxGeoState").textContent = it.lat ? ({ manual: "· поставлена вручную", learned: "· из ваших правок", feed: "· из фида",
      approx: "· примерная" }[it.geo_status] || "· найдена по адресу") : "· точки нет";
    if (mode === "place") showPlace(it); else showGeo(it);
    window.scrollTo({ top: 0 });
  }

  function renderExtra() {
    $("fxExtraChips").innerHTML = extraDistricts.map((d) =>
      `<span class="chip on extra-chip">${esc(d)}<button type="button" data-extra-del="${esc(d)}" aria-label="Убрать">×</button></span>`).join("");
    renderLearn();
  }
  function showPlace(it) {
    chosenDistrict = it.district || null;
    extraDistricts = [...(it.extra_districts || [])];
    $("fxExtraInput").value = "";
    const sug = it.suggest || { districts: [], complexes: [] };
    $("fxDistricts").innerHTML = sug.districts.map((d, i) =>
      `<button type="button" class="chip${d.name === chosenDistrict ? " on" : ""}" data-district="${esc(d.name)}"
        title="${esc(d.why)}"><span class="kbd">${i + 1}</span> ${esc(d.name)} <small>${esc(d.why)}</small></button>`).join("")
      + `<button type="button" class="chip${chosenDistrict ? "" : " on"}" data-district="">без района</button>`;
    $("fxDistrictInput").value = chosenDistrict && !sug.districts.some((d) => d.name === chosenDistrict) ? chosenDistrict : "";
    $("fxComplex").value = it.complex || "";
    $("fxExtraChips").innerHTML = extraDistricts.map((d) =>
      `<span class="chip on extra-chip">${esc(d)}<button type="button" data-extra-del="${esc(d)}" aria-label="Убрать">×</button></span>`).join("");
    $("fxComplexes").innerHTML = sug.complexes.map((c) =>
      `<button type="button" class="chip" data-complex="${esc(c.name)}" title="${esc(c.why)}">ЖК ${esc(c.name)} <small>${esc(c.why)}</small></button>`).join("")
      + `<button type="button" class="chip" data-complex="">без ЖК</button>`;
    renderLearn();
  }

  function renderLearn() {
    if (!item) return;
    const cx = $("fxComplex").value.trim();
    const d = [chosenDistrict, ...extraDistricts].filter(Boolean).join(", ") || null;
    const rows = [];
    if (item.street && d) rows.push(["street", `Улица <b>${esc(item.street)}</b> → район <b>${esc(d)}</b> (для её объявлений без своего района)`]);
    if (item.street && item.house) rows.push(["addr", `Дом <b>ул. ${esc(item.street)}, ${esc(item.house)}</b> → ${cx ? "ЖК " + esc(cx) : "без ЖК"}${d ? ", " + esc(d) : ""}`]);
    if (item.complex && item.complex !== cx) rows.push(["alias", cx
      ? `«${esc(item.complex)}» → это ЖК <b>${esc(cx)}</b> (везде)` : `«${esc(item.complex)}» — это не ЖК (везде)`]);
    if (cx && d) rows.push(["cx_district", `ЖК <b>${esc(cx)}</b> → район <b>${esc(d)}</b> (всегда)`]);
    const prev = {};
    document.querySelectorAll("#fxLearn input").forEach((i) => { prev[i.name] = i.checked; });
    $("fxLearn").innerHTML = rows.length
      ? `<div class="fx-label">Научить сервис — для всех таких объявлений, старых и новых</div>` + rows.map(([k, t]) =>
        `<label class="fx-check"><input type="checkbox" name="${k}"${prev[k] === false ? "" : " checked"}><span>${t}</span></label>`).join("")
      : `<div class="note">Поправим только этот объект.</div>`;
  }

  function pickDistrict(name) {
    chosenDistrict = name || null;
    document.querySelectorAll("#fxDistricts .chip").forEach((b) => b.classList.toggle("on", (b.dataset.district || null) === chosenDistrict));
    if (chosenDistrict && !document.querySelector(`#fxDistricts .chip[data-district="${CSS.escape(chosenDistrict)}"]`)) {
      $("fxDistrictInput").value = chosenDistrict;
    } else if (!chosenDistrict || document.querySelector(`#fxDistricts .chip.on[data-district]`)) {
      $("fxDistrictInput").value = "";
    }
    renderLearn();
  }

  // Яндекс Карты с вписанным адресом: дом → ЖК → улица → район
  function yaHref(it) {
    const place = it.settlement || "Краснодар";
    const q = it.street ? `${place}, ${it.street}${it.house ? " " + it.house : ""}`
      : it.complex ? `ЖК ${it.complex}, Краснодар` : `${it.district || ""} ${place}`.trim();
    return `https://yandex.ru/maps/35/krasnodar/?text=${encodeURIComponent(q)}`;
  }

  // ─── карта ────────────────────────────────────────────────────────────
  function showGeo(it) {
    const start = it.lat ? [it.lat, it.lon] : [45.035, 38.975];
    if (!map) {
      map = L.map("fxMap", { attributionControl: false });
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19 }).addTo(map);
      marker = L.marker(start, { draggable: true, icon: L.divIcon({ className: "geo-pin-wrap", iconSize: [36, 46],
        iconAnchor: [18, 44], html: `<div class="geo-pin"><img src="/static/favicon.svg" alt=""></div>` }) }).addTo(map);
      map.on("click", (e) => { marker.setLatLng(e.latlng); geoNow(); });
      marker.on("dragend", geoNow);
    }
    marker.setLatLng(start);
    $("fxCoords").value = "";
    $("fxYa").href = yaHref(it);
    const fit = () => { map.invalidateSize(); map.setView(start, it.lat ? 16 : 12); };
    fit(); setTimeout(fit, 150);
    geoNow();
  }
  function geoNow() {
    const ll = marker.getLatLng();
    $("fxGeoNow").textContent = `Точка: ${ll.lat.toFixed(6)}, ${ll.lng.toFixed(6)}`;
  }
  function parseCoords(text) {
    let t = String(text || "").trim();
    try { t = decodeURIComponent(t); } catch { /* */ }
    let a, b;
    const ya = t.match(/[?&](?:pt|whatshere\[point\])=(-?\d+\.\d+),(-?\d+\.\d+)/) || t.match(/[?&]ll=(-?\d+\.\d+),(-?\d+\.\d+)/);
    const gm = t.match(/@(-?\d+\.\d+),(-?\d+\.\d+)/);
    if (ya) { a = +ya[2]; b = +ya[1]; } else if (gm) { a = +gm[1]; b = +gm[2]; } else {
      const nums = t.replace(/(\d),(\d)/g, "$1.$2").match(/-?\d+(?:\.\d+)?/g) || [];
      if (nums.length < 2) return null;
      a = +nums[0]; b = +nums[1];
    }
    if (a > 36 && a < 42 && b > 42 && b < 48) [a, b] = [b, a];
    return a > 40 && a < 50 && b > 35 && b < 45 ? [a, b] : null;
  }

  // ─── сохранение ───────────────────────────────────────────────────────
  let busy = false;
  async function submit(extra) {
    if (!item || busy) return;
    busy = true;
    let r;
    if (mode === "place") {
      const learn = {};
      document.querySelectorAll("#fxLearn input").forEach((i) => { if (i.checked) learn[i.name] = true; });
      const body = { complex: $("fxComplex").value, district: chosenDistrict || "", extra: extraDistricts, learn };
      if ($("fxPrice").value.trim() && $("fxPrice").value !== $("fxPrice").dataset.orig) body.price = $("fxPrice").value;
      r = await call(`/api/admin/listings/${item.id}/place`, body);
    } else {
      const ll = marker.getLatLng();
      r = await call(`/api/admin/listings/${item.id}/geo`, extra || { lat: ll.lat, lon: ll.lng });
    }
    busy = false;
    if (!r.ok) { toast(r.data.detail || "Не получилось сохранить"); return; }
    ss.done.push(item.id);
    ss.skipped = ss.skipped.filter((x) => x !== item.id);
    ss.history.push(item.id);
    save();
    const learned = r.data.learned || [];
    toast(`✓ Сохранено${learned.length ? " · выучено: " + learned.join("; ") : ""}${r.data.applied ? ` · поправлено ещё ${r.data.applied}` : ""}`, 4000);
    next();
  }

  // Режим «ЖК и районы»: точку можно поправить тут же, не уходя из очереди
  async function saveGeoOnly() {
    if (!item || !marker) return;
    const ll = marker.getLatLng();
    const r = await call(`/api/admin/listings/${item.id}/geo`, { lat: ll.lat, lon: ll.lng });
    if (!r.ok) { toast(r.data.detail || "Не получилось сохранить точку"); return; }
    item.lat = ll.lat; item.lon = ll.lng; item.geo_status = "manual";
    $("fxGeoState").textContent = "· поставлена вручную";
    toast(`📍 Точка сохранена${r.data.applied ? ` · поправлено ещё ${r.data.applied}` : ""}`);
  }
  // Удалить всё от номера: объекты, запросы, сообщения; новые не принимать
  async function banPhone(phone) {
    if (!confirm(`Удалить всё от номера ${phone}?\nВсе его объекты и запросы скроются, сообщения удалятся, новые не будут приниматься.\nВернуть — админка → «Удалённые».`)) return;
    const r = await call("/api/admin/phones/ban", { phone });
    if (!r.ok) { toast(r.data.detail || "Не получилось"); return; }
    toast(`🚫 Номер удалён: объектов ${r.data.listings}, сообщений ${r.data.messages}`, 4000);
    if (item) { ss.done.push(item.id); save(); }
    next();
  }

  // Удалить только этот объект (навсегда: повторы и тот же объект не вернутся)
  async function delOne(id) {
    if (!confirm("Удалить этот объект навсегда?\nПовторы этого объявления больше не появятся. Вернуть — админка → «Удалённые».")) return;
    const r = await call(`/api/admin/listings/${id}/action`, { action: "delete" });
    if (!r.ok) { toast(r.data.detail || "Не получилось"); return; }
    toast("Объект удалён");
    ss.done.push(id); save(); next();
  }
  // База номера: все объекты, запросы, сообщения; удалить/вернуть по одному или всё сразу
  async function phoneBase(phone) {
    $("phoneTitle").textContent = phone;
    $("phoneBody").innerHTML = `<p class="note">Загружаем…</p>`;
    $("phoneDlg").showModal();
    const r = await call(`/api/admin/phones?phone=${encodeURIComponent(phone)}`);
    if (!r.ok) { $("phoneBody").innerHTML = `<p class="note">Не получилось загрузить.</p>`; return; }
    const d = r.data;
    const active = d.listings.filter((o) => o.is_active).length;
    $("phoneBody").innerHTML = `<p class="note">Объектов: <b>${d.listings.length}</b> (на сайте ${active}) · запросов: ${d.requests.length}
        · сообщений: ${d.messages}${d.banned ? " · <b class=\"bad\">номер удалён</b>" : ""}</p>
      <div class="fx-pd-acts">${d.banned
        ? `<button type="button" class="chip" data-unban-phone="${esc(d.phone)}">Вернуть номер</button>`
        : `<button type="button" class="chip danger" data-ban-phone="${esc(d.phone)}">🚫 Удалить всё от номера</button>`}
        <a class="chip" href="https://wa.me/${d.phone.replace(/\D/g, "")}" target="_blank" rel="noopener">WhatsApp</a></div>
      <ul class="fx-pd-list">${d.listings.map((o) => `<li class="${o.is_active ? "" : "off"}">
        ${o.photo ? `<img src="${esc(o.photo)}" alt="" loading="lazy">` : `<span class="fx-pd-noph"></span>`}
        <div><a href="/?open=${o.id}" target="_blank">${esc(o.title)}</a>
          <small>${esc(price(o.price, o.deal))} · ${esc([o.complex && "ЖК " + o.complex, o.district].filter(Boolean).join(" · ") || "без места")}
          ${o.is_active ? "" : ` · <b>скрыт${o.hidden_reason === "admin" ? " (удалён)" : o.hidden_reason === "phone" ? " (номер удалён)" : ""}</b>`}</small></div>
        <div class="fx-pd-btns"><a class="chip" href="/fix?mode=place&id=${o.id}" title="Разобрать">✎</a>
          ${o.is_active ? `<button type="button" class="chip danger" data-pb-del="${o.id}" title="Удалить навсегда">🗑</button>`
            : `<button type="button" class="chip" data-pb-restore="${o.id}" title="Вернуть на сайт">Вернуть</button>`}</div></li>`).join("")
        || `<li class="note">Объектов с этим номером нет.</li>`}</ul>`;
  }
  async function pbAction(id, action, btn) {
    if (action === "delete" && !confirm("Удалить объект навсегда?")) return;
    const r = await call(`/api/admin/listings/${id}/action`, { action });
    if (!r.ok) { toast(r.data.detail || "Не получилось"); return; }
    toast(action === "delete" ? "Удалён" : "Возвращён на сайт");
    phoneBase($("phoneTitle").textContent);
  }
  document.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-unban-phone]");
    if (!b) return;
    await call("/api/admin/phones/ban", { phone: b.dataset.unbanPhone, action: "unban" });
    toast("Номер возвращён"); phoneBase(b.dataset.unbanPhone);
  });

  // ─── верхняя панель ───────────────────────────────────────────────────
  function renderTop() {
    document.querySelectorAll("[data-mode]").forEach((b) => b.classList.toggle("on", b.dataset.mode === mode));
    $("fxKinds").innerHTML = KINDS[mode].map(([k, t]) => `<button type="button" class="chip${k === kind ? " on" : ""}" data-kind="${k}">${t}</button>`).join("");
    $("fxProgress").innerHTML = `Осталось: <b>${left}</b> · разобрано: <b>${ss.done.length}</b>${ss.skipped.length ? ` · пропущено: ${ss.skipped.length}` : ""}`;
    history.replaceState(null, "", `/fix?mode=${mode}&kind=${kind}`);
  }
  function switchTo(m, k) {
    mode = m; kind = k || KINDS[m][0][0]; item = null; ss = load();
    next();
  }

  // ─── события ──────────────────────────────────────────────────────────
  document.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.mode) switchTo(b.dataset.mode);
    else if (b.dataset.kind) switchTo(mode, b.dataset.kind);
    else if (b.dataset.district !== undefined) pickDistrict(b.dataset.district);
    else if (b.dataset.complex !== undefined) { $("fxComplex").value = b.dataset.complex; renderLearn(); }
    else if (b.id === "fxSave") submit();
    else if (b.id === "fxSkip") skip();
    else if (b.id === "fxBack") back();
    else if (b.id === "fxHide") submit({ hide: true });
    else if (b.id === "fxGeoSave") saveGeoOnly();
    else if (b.dataset.banPhone) banPhone(b.dataset.banPhone);
    else if (b.dataset.phoneBase) phoneBase(b.dataset.phoneBase);
    else if (b.dataset.delOne) delOne(Number(b.dataset.delOne));
    else if (b.dataset.pbDel) pbAction(Number(b.dataset.pbDel), "delete", b);
    else if (b.dataset.pbRestore) pbAction(Number(b.dataset.pbRestore), "restore", b);
    else if (b.hasAttribute("data-close")) b.closest("dialog").close();
    else if (b.dataset.extraDel) { extraDistricts = extraDistricts.filter((x) => x !== b.dataset.extraDel); renderExtra(); }
  });
  // Район и ЖК — выпадающие списки с поиском; нет такого района — «+ Добавить район»
  Combo($("fxDistrictInput"), {
    options: () => districts,
    onPick: (v) => pickDistrict(v),
    newLabel: (v) => `+ Добавить новый район «${v}»`,
    onNew: async (name) => {
      if (!confirm(`Добавить новый район «${name}»? Он появится в фильтрах и будет узнаваться в объявлениях.`)) return;
      const r = await call("/api/admin/districts", { name });
      if (!r.ok) { toast(r.data.detail || "Не получилось добавить"); return; }
      districts = r.data.districts;
      pickDistrict(r.data.name);
      $("fxDistrictInput").value = r.data.name;
      toast(`Район «${r.data.name}» добавлен`);
    },
  });
  Combo($("fxComplex"), { options: () => complexes, onPick: () => renderLearn() });
  Combo($("fxExtraInput"), {
    options: () => districts.filter((d) => d !== chosenDistrict && !extraDistricts.includes(d)),
    onPick: (v) => {
      if (!chosenDistrict) pickDistrict(v);       // основного ещё нет — пусть будет основным
      else if (!extraDistricts.includes(v)) extraDistricts.push(v);
      $("fxExtraInput").value = "";
      renderExtra();
    },
  });
  $("fxComplex").addEventListener("input", renderLearn);
  $("fxPlace").addEventListener("submit", (e) => { e.preventDefault(); submit(); });
  $("fxGeoBox").addEventListener("toggle", () => { if ($("fxGeoBox").open && item && mode === "place") showGeo(item); });
  $("fxCoordsForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const ll = parseCoords($("fxCoords").value);
    if (!ll) { toast("Не понял координаты. Пример: 45.0355, 38.9753"); return; }
    marker.setLatLng(ll); map.setView(ll, 17); geoNow();
  });
  // Клавиши: Enter — сохранить и дальше, → — пропустить, ← — назад, 1–9 — район из подсказок
  document.addEventListener("keydown", (e) => {
    const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName);
    if (e.key === "Enter") {
      const id = document.activeElement.id;
      if (id === "fxCoords") return;                       // Enter в поле координат — «Найти»
      e.preventDefault();
      if (id === "fxComplex") { document.activeElement.blur(); renderLearn(); return; }   // выбрали ЖК — ещё не сохраняем
      submit();
      return;
    }
    if (typing) return;
    if (e.key === "ArrowRight") skip();
    else if (e.key === "ArrowLeft") back();
    else if (mode === "place" && /^[1-9]$/.test(e.key)) {
      const chip = document.querySelectorAll("#fxDistricts .chip[data-district]")[Number(e.key) - 1];
      if (chip && chip.dataset.district) pickDistrict(chip.dataset.district);
    }
  });


  if (window.yaSelect) window.yaSelect(true);   // выделил адрес в тексте → «В Яндекс Картах»
  // Старт: можно начать с конкретного объекта (?id=)
  (async () => {
    const startId = Number(sp.get("id"));
    if (startId) show(await fetchItem(startId)); else next();
  })();
})();
