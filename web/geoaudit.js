/* «Обучение» → «Проверка правил и справочников» (ТЗ «админка-гео», P0.0).
   Отчёт ничего не меняет: правила «улица → район» и «ЖК → район» против вашей карты районов (по точкам домов), ЖК-мусор,
   «улицы»-подписи полей. Отметьте, что исправить, и нажмите «Применить отмеченное» — это и есть подтверждение.
   Каждое изменение объекта — в журнале правок; откат — в окне объекта («История правок»). API: /api/admin/geo-audit */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const box = $("lnAudit");
  if (!box) return;
  let data = null, showAll = false;

  async function call(url, body) {
    const opts = { credentials: "same-origin", method: body ? "POST" : "GET" };
    if (body) { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(body); }
    const r = await fetch(url, opts);
    let d = {};
    try { d = await r.json(); } catch { /* */ }
    return { ok: r.ok, data: d };
  }
  const ST = { bad: ["против карты", "bad"], few: ["мало точек", "few"], ok: ["сходится", "ok"] };
  const distTxt = (d) => Object.entries(d).map(([k, v]) => `${esc(k)} ${v}`).join(", ") || "—";

  function render() {
    const rules = data.rules.filter((r) => showAll || r.status === "bad");
    box.innerHTML = `
      <p class="ln-a-sum">Правил «улица/ЖК → район»: <b>${data.rules.length}</b>, против вашей карты районов (в своём районе меньше 70 %
        объектов с точкой дома): <b class="${data.bad ? "bad" : ""}">${data.bad}</b>. ЖК-мусор: <b>${data.junk.length}</b>.
        «Улицы»-подписи («Площадь», «Этаж»…): <b>${data.labels.length}</b>. Испорченных правил: <b>${data.broken.length}</b>.
        Выученных точек домов без посёлка: ${data.points_total} (верных по справочнику адресов ${data.points_ok}, сомнительных
        <b class="${data.points.length ? "bad" : ""}">${data.points.length}</b>). Точек ЖК из одного дома: <b>${data.cx_points.length}</b>.</p>
      <h3 class="ln-a-h">Правила <label class="ln-a-all"><input type="checkbox" id="laShowAll"${showAll ? " checked" : ""}> показать все, не только спорные</label></h3>
      ${rules.length ? `<table class="ln-a-t"><thead><tr><th></th><th>Правило</th><th>Объектов</th><th>По контурам (точки домов)</th><th>В своём районе</th></tr></thead><tbody>
        ${rules.map((r) => `<tr class="st-${ST[r.status][1]}"><td><input type="checkbox" data-rule="${r.id}"${r.status === "bad" ? " checked" : ""}></td>
          <td>${esc(r.label)}<small>${ST[r.status][0]} · применялось ${r.applied}</small></td><td>${r.objects}</td>
          <td>${distTxt(r.dist)}</td><td>${r.share == null ? "—" : Math.round(r.share * 100) + "%"}</td></tr>`).join("")}</tbody></table>`
        : `<p class="note">Спорных правил нет.</p>`}
      <h3 class="ln-a-h">Не ЖК (города и общие слова)</h3>
      ${data.junk.length ? data.junk.map((j) => `<label class="ln-a-row"><input type="checkbox" data-junk="${esc(j.name)}" checked>
          «${esc(j.name)}» — объектов ${j.listings}: убрать ЖК, впредь не принимать</label>`).join("") : `<p class="note">Не найдено.</p>`}
      <h3 class="ln-a-h">«Улицы», которые на самом деле подписи полей</h3>
      ${data.labels.length ? `<label class="ln-a-row"><input type="checkbox" id="laLabels" checked>
          Убрать улицу у ${data.labels.length} объектов (${esc([...new Set(data.labels.map((x) => x.street))].join(", "))}) — точку поищем заново</label>`
        : `<p class="note">Не найдено.</p>`}
      <h3 class="ln-a-h">Испорченные правила (район склеен из нескольких или повторяется)</h3>
      ${data.broken.length ? data.broken.map((b) => `<label class="ln-a-row"><input type="checkbox" data-broken="${b.id}" checked>
          «${esc(b.label)}» → «${esc(b.new_label)}»</label>`).join("") : `<p class="note">Не найдено.</p>`}
      <h3 class="ln-a-h">Выученные точки домов без посёлка, которые не сходятся со справочником адресов</h3>
      ${!data.osm_loaded ? `<p class="note">Справочник адресов ещё не загружен — проверим после загрузки.</p>`
        : data.points.length ? data.points.map((x) => `<label class="ln-a-row"><input type="checkbox" data-point="${esc(x.key)}" checked>
          «${esc(x.label)}» — ${x.status === "far" ? `дом по справочнику в ${x.m} м от точки` : "в Краснодаре такой улицы нет (посёлок?)"};
          объектов на точке: ${x.listings}. Забыть точку, объекты — в «Спорные»</label>`).join("") : `<p class="note">Не найдено.</p>`}
      <h3 class="ln-a-h">Точки ЖК, которые на самом деле точка одного дома</h3>
      ${data.cx_points.length ? data.cx_points.map((x) => `<label class="ln-a-row"><input type="checkbox" data-cxpt="${esc(x.key)}" checked>
          «${esc(x.label)}» — ${x.new ? `перенести в центр (${esc(x.how)})` : esc(x.how)}; объектов на точке: ${x.listings}</label>`).join("")
        : `<p class="note">Не найдено.</p>`}
      <h3 class="ln-a-h">Районы-дубли (одно место под двумя именами)</h3>
      ${data.dupes.length ? data.dupes.map((x) => `<label class="ln-a-row"><input type="checkbox" data-dupe="${esc(x.name)}" checked>
          «${esc(x.name)}» (объектов ${x.listings}) → «${esc(x.to)}»</label>`).join("") : `<p class="note">Не найдено.</p>`}
      ${data.empty_contours.length ? `<p class="note">Нарисованы, но объектов в них нет: ${esc(data.empty_contours.join(", "))} —
          проверьте на <a href="/admin/map">карте районов</a>, нужны ли они.</p>` : ""}
      <div class="ln-a-acts"><button type="button" class="pill" id="laApply">Применить отмеченное</button>
        <span class="note">Отключённое правило: его объекты получат район по контуру под точкой дома, иначе — из текста объявления.</span></div>`;
  }

  async function load() {
    box.innerHTML = `<p class="note">Считаем…</p>`;
    const r = await call("/api/admin/geo-audit");
    if (!r.ok) { box.innerHTML = `<p class="note">Не получилось загрузить отчёт.</p>`; return; }
    data = r.data;
    render();
  }
  box.addEventListener("change", (e) => { if (e.target.id === "laShowAll") { showAll = e.target.checked; render(); } });
  box.addEventListener("click", async (e) => {
    if (e.target.id !== "laApply") return;
    const rules = [...box.querySelectorAll("[data-rule]:checked")].map((x) => Number(x.dataset.rule));
    const junk = [...box.querySelectorAll("[data-junk]:checked")].map((x) => x.dataset.junk);
    const labels = !!($("laLabels") && $("laLabels").checked);
    const pick = (attr) => [...box.querySelectorAll(`[data-${attr}]:checked`)].map((x) => x.dataset[attr]);
    const broken = pick("broken").map(Number), points = pick("point"), cx_points = pick("cxpt"), dupes = pick("dupe");
    if (!rules.length && !junk.length && !labels && !broken.length && !points.length && !cx_points.length && !dupes.length) return;
    if (!confirm(`Применить?\nОтключить правил: ${rules.length}; убрать ЖК-мусор: ${junk.length}; улицы-подписи: ${labels ? "да" : "нет"};\n`
      + `починить правил: ${broken.length}; забыть точек домов: ${points.length}; пересчитать точек ЖК: ${cx_points.length}; слить районов: ${dupes.length}.\n`
      + "Все изменения объектов попадут в журнал правок — их можно откатить.")) return;
    e.target.disabled = true;
    const r = await call("/api/admin/geo-audit", { rules, junk, labels, broken, points, cx_points, dupes });
    e.target.disabled = false;
    const d = r.data;
    alert(r.ok ? `Готово: правил отключено ${d.rules || 0}, починено ${d.broken || 0}; ЖК убрано ${d.junk || 0}; улиц-подписей ${d.labels || 0}; `
      + `точек домов забыто ${d.points || 0} (в «Спорные» ${d.to_queue || 0}), точек ЖК пересчитано ${d.cx_points || 0}; районов слито ${d.dupes || 0}; объектов изменено ${d.listings || 0}.`
      : "Не получилось применить");
    load();
  });
  load();
})();
