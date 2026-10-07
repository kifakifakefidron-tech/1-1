/* 1+1 · карта районов (только админ): контуры из OpenStreetMap + обведённые вами; нашими цветами.
   Обвести район → объекты с точкой внутри получают этот район; сверка ЖК/улиц с картой. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  // Наши цвета: светло-жёлтый, мятный, янтарный, голубой, коралловый, сиреневый, бирюзовый
  const PALETTE = ["#F3E8BC", "#9be8b8", "#ffd27a", "#7fc8ff", "#ffb4a0", "#c7a6ff", "#6fd3c4", "#ffe08a", "#a3d9ff", "#f7a8d8"];
  const color = (name) => { let h = 0; for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0; return PALETTE[h % PALETTE.length]; };

  let map, layers = new Map(), cxGroup, distGroup;
  let polygons = [], districts = [], counts = {}, selected = "", editing = null;

  let toastTimer = 0;
  function toast(text, ms = 3000) {
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

  function initMap() {
    map = L.map("dmMap", { attributionControl: false }).setView([45.04, 38.98], 12);
    L.control.attribution({ prefix: false }).addTo(map);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19,
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>' }).addTo(map);
    distGroup = L.layerGroup().addTo(map);
    cxGroup = L.layerGroup();
    map.pm.setLang("ru");
    map.pm.setGlobalOptions({ snappable: true, snapDistance: 12, allowSelfIntersection: false,
      templineStyle: { color: "#035352" }, hintlineStyle: { color: "#035352", dashArray: "5,5" } });
    map.on("pm:create", async (e) => {
      const latlngs = e.layer.getLatLngs();
      map.removeLayer(e.layer);
      await savePolygon(latlngs);
    });
  }

  async function load() {
    const r = await call("/api/admin/reconcile?polygons=1");
    if (r.status === 401 || r.status === 403) { $("dmDenied").hidden = false; return false; }
    polygons = r.data.polygons; districts = r.data.districts; counts = r.data.counts || {};
    $("dmApp").hidden = false;
    drawPolygons();
    renderList();
    if (selected) select(selected, false);
    return true;
  }

  function drawPolygons() {
    distGroup.clearLayers(); cxGroup.clearLayers(); layers.clear();
    for (const p of polygons) {
      const isCx = p.kind === "complex";
      const c = isCx ? "#035352" : color(p.name);
      const layer = L.polygon(p.polys, isCx
        ? { color: "#035352", weight: 1.5, dashArray: "4,4", fillColor: "#035352", fillOpacity: .12 }
        : { color: "#0b3f3e", weight: 2, fillColor: c, fillOpacity: .38 });
      layer.bindTooltip(isCx ? `ЖК ${esc(p.name)}` : `<b>${esc(p.name)}</b>${p.source === "admin" ? " · ваш" : " · OSM"}`,
        { sticky: !isCx ? false : true, direction: "center", className: isCx ? "dm-tip cx" : "dm-tip", permanent: !isCx });
      layer.on("click", () => { if (!isCx && !editing) select(p.name); });
      (isCx ? cxGroup : distGroup).addLayer(layer);
      if (!isCx) layers.set(p.name, { layer, poly: p });
    }
  }

  function renderList() {
    const q = ($("dmQ").value || "").trim().toLowerCase().replace(/ё/g, "е");
    const withShape = new Map(polygons.filter((p) => p.kind !== "complex").map((p) => [p.name, p.source]));
    const done = withShape.size;
    $("dmStat").innerHTML = `Обведено районов: <b>${done}</b> из ${districts.length} · ЖК с контуром: <b>${polygons.filter((p) => p.kind === "complex").length}</b>`;
    const list = districts.filter((d) => !q || d.toLowerCase().replace(/ё/g, "е").includes(q));
    // сначала без контура и с объектами — их важнее обвести
    list.sort((a, b) => (withShape.has(a) - withShape.has(b)) || ((counts[b] || 0) - (counts[a] || 0)));
    $("dmList").innerHTML = list.map((d) => {
      const src = withShape.get(d);
      const badge = src === "admin" ? '<span class="dm-badge mine">ваш</span>' : src ? '<span class="dm-badge osm">OSM</span>' : '<span class="dm-badge none">нет контура</span>';
      return `<li><button type="button" class="dm-item${d === selected ? " on" : ""}" data-d="${esc(d)}">
        <i class="dm-dot" style="background:${src ? color(d) : "transparent"}"></i><span>${esc(d)}</span>${badge}<b>${counts[d] || 0}</b></button></li>`;
    }).join("");
  }

  function select(name, fly = true) {
    selected = name;
    renderList();
    $("dmSel").hidden = false;
    $("dmSelName").textContent = name;
    const l = layers.get(name);
    $("dmSelInfo").textContent = l
      ? `Контур: ${l.poly.source === "admin" ? "ваш" : "из OpenStreetMap"} · объектов в районе: ${counts[name] || 0}`
      : `Контура нет — обведите район на карте · объектов в районе: ${counts[name] || 0}`;
    $("dmEdit").hidden = !l; $("dmDel").hidden = !l;
    $("dmDraw").textContent = l ? "✏️ Обвести заново" : "✏️ Обвести на карте";
    if (l && fly) map.flyToBounds(l.layer.getBounds().pad(0.3), { duration: 0.6 });
    for (const [n, x] of layers) x.layer.setStyle({ weight: n === name ? 4 : 2, color: n === name ? "#ffd86b" : "#0b3f3e" });
    checkAll = false;
    renderCheck();
  }

  function startDraw() {
    cancelEdit();
    $("dmHelp").hidden = false;
    $("dmHelp").innerHTML = `Нажимайте на карте по границе района «<b>${esc(selected)}</b>». Чтобы закончить — нажмите на первую точку.
      Esc — отменить. Точки прилипают к соседним районам, чтобы не было щелей.`;
    map.pm.enableDraw("Polygon", { pathOptions: { color: "#ffd86b", fillColor: color(selected), fillOpacity: .35 } });
  }
  function startEdit() {
    const l = layers.get(selected);
    if (!l) return;
    cancelEdit();
    editing = l.layer;
    editing.pm.enable({ allowSelfIntersection: false, snappable: true });
    $("dmSave").hidden = false; $("dmCancel").hidden = false; $("dmEdit").hidden = true; $("dmDraw").hidden = true;
    $("dmHelp").hidden = false;
    $("dmHelp").textContent = "Тяните точки, чтобы поправить границу. Точка посередине стороны — добавить новую. Правый клик по точке — удалить.";
  }
  function cancelEdit() {
    map.pm.disableDraw();
    if (editing) { editing.pm.disable(); editing = null; drawPolygons(); }
    $("dmSave").hidden = true; $("dmCancel").hidden = true; $("dmEdit").hidden = !layers.get(selected); $("dmDraw").hidden = false;
    $("dmHelp").hidden = true;
  }
  async function savePolygon(latlngs) {
    const rings = (Array.isArray(latlngs[0]) ? latlngs : [latlngs]).map((ring) => ring.map((p) => [p.lat, p.lng]));
    const r = await call("/api/admin/reconcile", { action: "save_polygon", name: selected, kind: "district", rings });
    if (!r.ok) { toast(r.data.detail || "Не получилось сохранить"); return; }
    toast(`✓ Контур «${selected}» сохранён`);
    editing = null;
    cancelEdit();
    await load();
    await check();
  }

  // ─── проверка объектов по карте: подробно, с галочками ───────────────
  let lastCheck = null, checkAll = false;
  const objRow = (o, to) => `<label class="dm-obj"><input type="checkbox" data-obj="${o.id}" data-to="${esc(to || o.map)}" checked>
      <span><b>${esc(o.title)}</b><small>${esc([o.addr, `${o.ours || "без района"} → ${to || o.map}`].filter(Boolean).join(" · "))}</small></span>
      <a href="/?open=${o.id}" target="_blank" title="Открыть объект">↗</a></label>`;

  async function check() {
    const r = await call("/api/admin/reconcile");
    if (!r.ok) return;
    lastCheck = r.data;
    renderCheck();
  }

  // Выбран район — показываем только вопросы по нему (объекты, которые карта кладёт в него или забирает из него)
  function renderCheck() {
    const d = lastCheck;
    if (!d) return;
    const only = checkAll ? "" : selected;
    const mine = (o) => !only || o.map === only || o.ours === only;
    const empty = d.empty.filter(mine);
    const diffs = [...d.complexes, ...d.streets]
      .map((x) => ({ ...x, items: x.items.filter(mine) }))
      .filter((x) => !only || x.items.length || x.map === only);
    $("dmCheck").innerHTML = (only
      ? `Вопросы по району «<b>${esc(only)}</b>»: без района — ${empty.length}, расхождений — ${diffs.length}.
         <button type="button" class="linkish" id="dmAll">Показать по всем районам</button>`
      : `Объектов с точной точкой: ${d.checked}. Нажмите на район в списке или на карте — останутся только вопросы по нему.`)
      + `<br>Раскройте группу, снимите галочки с объектов, которые переносить не нужно, и нажмите кнопку.`;
    $("dmFill").hidden = true;
    // 1) без района, но внутри обведённого района — по районам
    const groups = {};
    for (const o of empty) (groups[o.map] = groups[o.map] || []).push(o);
    const emptyHtml = Object.entries(groups).sort((x, y) => y[1].length - x[1].length).map(([dist, list]) => `
      <details class="dm-grp" data-grp>
        <summary><span>Без района → <b>${esc(dist)}</b></span><em>${list.length}</em></summary>
        <div class="dm-grp-body">
          <label class="dm-all"><input type="checkbox" data-all checked> отметить все</label>
          ${list.map((o) => objRow(o, dist)).join("")}
          <div class="dm-grp-acts"><button type="button" class="pill" data-apply>Проставить «${esc(dist)}» отмеченным</button></div>
        </div></details>`).join("");
    // 2) расхождения по ЖК и улицам
    const diffHtml = diffs.slice(0, 80).map((x) => `
      <details class="dm-grp" data-grp${only ? " open" : ""}>
        <summary><span>${x.kind === "complex" ? "ЖК" : "ул."} <b>${esc(x.name)}</b>
          <small>у нас: ${x.ours.map(([n, c]) => `${esc(n)} ${c}`).join(", ")} · по карте: ${x.map_all.map(([n, c]) => `${esc(n)} ${c}`).join(", ")}</small></span>
          <em>${x.items.length}</em></summary>
        <div class="dm-grp-body">
          ${x.items.length ? `<label class="dm-all"><input type="checkbox" data-all checked> отметить все</label>${x.items.map((o) => objRow(o)).join("")}`
            : `<p class="note">Объекты уже в правильном районе.</p>`}
          ${x.kind === "complex" ? `<label class="dm-remember"><input type="checkbox" data-remember="${esc(x.name)}" data-to="${esc(x.map)}" checked>
            Запомнить: ЖК ${esc(x.name)} — район «${esc(x.map)}» (новые объявления сразу туда)</label>` : ""}
          <div class="dm-grp-acts">
            <button type="button" class="pill" data-apply>Перенести отмеченные</button>
            <button type="button" class="ghost" data-ignore="${esc(x.key)}">Оставить как есть</button>
          </div>
        </div></details>`).join("");
    $("dmDiffs").innerHTML =
      (empty.length ? `<h4 class="dm-sub">Без района, но внутри обведённого района · ${only ? empty.length : d.empty_total}</h4>${emptyHtml}` : "") +
      (diffHtml ? `<h4 class="dm-sub">Расхождения по ЖК и улицам · ${diffs.length}</h4>${diffHtml}` : "") ||
      `<p class="note">${only ? `По району «${esc(only)}» вопросов нет 🎉` : "Расхождений нет 🎉"}</p>`;
  }

  async function applyGroup(grp) {
    const items = [...grp.querySelectorAll("input[data-obj]:checked")].map((i) => ({ id: Number(i.dataset.obj), district: i.dataset.to }));
    const rem = grp.querySelector("input[data-remember]:checked");
    if (!items.length && !rem) { toast("Ничего не отмечено"); return; }
    const r = await call("/api/admin/reconcile", { action: "set_districts", items,
      remember_complex: rem ? { name: rem.dataset.remember, district: rem.dataset.to } : null });
    if (!r.ok) { toast(r.data.detail || "Не получилось"); return; }
    toast(`✓ Перенесено объектов: ${r.data.listings}${rem ? " · правило для ЖК запомнено" : ""}`);
    await check(); await load();
  }


  document.addEventListener("click", async (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.d) select(b.dataset.d);
    else if (b.id === "dmDraw") startDraw();
    else if (b.id === "dmEdit") startEdit();
    else if (b.id === "dmCancel") cancelEdit();
    else if (b.id === "dmSave" && editing) await savePolygon(editing.getLatLngs());
    else if (b.id === "dmDel") {
      const l = layers.get(selected);
      if (!l || !confirm(`Удалить контур «${selected}»? Районы объектов не изменятся.`)) return;
      await call("/api/admin/reconcile", { action: "delete_polygon", id: l.poly.id });
      toast("Контур удалён"); await load();
    } else if (b.id === "dmOsm") {
      b.disabled = true; b.textContent = "Загружаю… (до 2 минут)";
      const r = await call("/api/admin/reconcile", { action: "import_osm" });
      b.disabled = false; b.textContent = "⬇ Загрузить из OpenStreetMap";
      if (!r.ok) { toast(r.data.detail || "OpenStreetMap не ответил, попробуйте позже", 5000); return; }
      toast(`Загружено: районов ${r.data.district}, ЖК ${r.data.complex}. Ваши контуры не тронуты.`, 5000);
      await load(); await check();
    } else if (b.id === "dmAll") {
      checkAll = true; renderCheck();
    } else if (b.id === "dmFill") {
      const r = await call("/api/admin/reconcile", { action: "fill_empty" });
      toast(`Район проставлен ${r.data.listings} объектам`); await check(); await load();
    } else if (b.hasAttribute("data-apply")) {
      await applyGroup(b.closest("[data-grp]"));
    } else if (b.dataset.accept) {
      const r = await call("/api/admin/reconcile", { action: "accept", kind: b.dataset.accept, name: b.dataset.name, district: b.dataset.to });
      toast(`Принято · объектов: ${r.data.listings}`); await check(); await load();
    } else if (b.dataset.ignore) {
      await call("/api/admin/reconcile", { action: "ignore", key: b.dataset.ignore });
      b.closest("li").remove();
    }
  });
  $("dmQ").addEventListener("input", renderList);
  // «отметить все» в группе
  document.addEventListener("change", (e) => {
    if (e.target.matches("input[data-all]")) {
      e.target.closest("[data-grp]").querySelectorAll("input[data-obj]").forEach((i) => { i.checked = e.target.checked; });
    }
  });
  $("dmShowCx").addEventListener("change", (e) => { if (e.target.checked) cxGroup.addTo(map); else map.removeLayer(cxGroup); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") cancelEdit(); });

  initMap();
  load().then((ok) => { if (ok) check(); });
})();
