/* 1+1 · «Обучение и разбор»: сводка, единая очередь, цепочка решений, перепроверка, всё выученное (app/brain.py). */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const n0 = (x) => Number(x || 0).toLocaleString("ru-RU");
  const ic = (n) => (window.ic ? window.ic(n) : "");
  const day = (ts) => new Date(ts * 1000).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

  async function call(url, body) {
    const opts = { credentials: "same-origin", method: body ? "POST" : "GET" };
    if (body) { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(body); }
    let r = await fetch(url, opts);
    for (let i = 0; i < 4 && r.status === 503; i++) {   // база на секунду занята фоновой работой — повторяем
      await new Promise((res) => setTimeout(res, 1500 * (i + 1)));
      r = await fetch(url, opts);
    }
    let d = {};
    try { d = await r.json(); } catch { /* */ }
    return { ok: r.ok, status: r.status, data: d };
  }

  // ─── сводка, очередь, цепочка ────────────────────────────────────────
  async function load() {
    const r = await call("/api/admin/learn");
    if (r.status === 401 || r.status === 403) { $("lnDenied").hidden = false; return; }
    $("lnApp").hidden = false;
    const d = r.data;
    const pct = d.total ? Math.round((d.ready / d.total) * 100) : 0;
    const waiting = d.queues.filter((q) => !["auto", "fixed"].includes(q.key)).reduce((a, q) => a + q.n, 0);
    $("lnReady").innerHTML = `
      <div class="ln-stat"><b>${n0(d.total)}</b><span>объектов на сайте</span></div>
      <div class="ln-stat ok"><b>${pct}%</b><span>готово полностью: точный дом на карте и район — ${n0(d.ready)}</span>
        <i class="ln-bar"><i style="width:${pct}%"></i></i></div>
      <div class="ln-stat"><b>${n0(waiting)}</b><span>ждут решения (объект может быть в нескольких очередях)</span></div>`;
    const first = d.queues.find((q) => q.n && !["auto", "fixed"].includes(q.key));
    $("lnGo").href = `/fix?kind=${first ? first.key : "auto"}`;
    $("lnGo").innerHTML = `${ic("chevron")} Разбирать${first ? ` · ${esc(first.label.toLowerCase())}` : ""}`;
    $("lnQueues").innerHTML = d.queues.map((q) => `
      <a class="ln-q${q.key === "conflict" && q.n ? " hot" : ""}${q.n ? "" : " empty"}" href="/fix?kind=${q.key}">
        <b>${n0(q.n)}</b><strong>${esc(q.label)}</strong><span>${esc(q.hint)}</span>
        <em>${q.n ? "Разбирать →" : "Пусто"}</em></a>`).join("");
    const pipe = (title, steps) => `<div class="ln-pipe"><h3>${title}</h3><ol>${steps.map((s) => `
      <li class="${s.bad ? "bad" : s.warn ? "warn" : ""}"><span>${esc(s.label)}</span><b>${n0(s.n)}${s.unit ? ` <small>${s.unit}</small>` : ""}</b></li>`).join("")}</ol></div>`;
    $("lnPipes").innerHTML = pipe("Район", d.pipeline.district) + pipe("Точка на карте", d.pipeline.point);
    const acc = d.accuracy || [];   // точность источников по вашим ответам в «Проверить решения сервиса»
    $("lnAcc").hidden = !acc.length;
    $("lnAcc").innerHTML = `<h3>Насколько сервис прав — по вашим проверкам (90 дней)</h3>
      <ol class="ln-acc">${acc.map((a) => `<li class="${a.share < .8 ? "bad" : a.share < .95 ? "warn" : ""}"><span>${esc(a.label)}</span>
        <b>${Math.round(a.share * 100)}%</b><small>верно ${n0(a.ok)} из ${n0(a.checked)}</small></li>`).join("")}</ol>
      <p class="note">Меньше 80 % — источнику не стоит верить: напишите об этом, сервис перестанет им пользоваться.</p>`;
    renderRecheck(d.recheck);
    const unc = d.uncontoured || [];
    $("lnUnc").hidden = !unc.length;
    $("lnUnc").innerHTML = `<h3>Районы из объявлений, которых нет на вашей карте — ${unc.length}</h3>
      <p class="note">Объекты этих районов сервис не может проверить по карте. Обведите район на карте районов
        (или, если это другое название уже обведённого района, объедините их в «Справочнике»).</p>
      <div class="chips">${unc.slice(0, 40).map((u) => `<span class="chip">${esc(u.name)} <b>${n0(u.n)}</b></span>`).join("")}</div>
      <div class="ln-acts"><a class="pill light" href="/admin/map">Обвести на карте районов</a>
        <a class="pill light" href="/admin?tab=districts">Справочник районов</a></div>`;
    const K = d.knowledge;
    $("lnKnowCards").innerHTML = [
      [K.rules, "ваших правил", "дом, ЖК, улица → район; названия ЖК"], [K.points, "ваших точек", "дом и ЖК на карте"],
      [K.prices, "правок цены и сделки", "повтор объявления — уже с верной ценой"],
      [K.districts_drawn, "нарисованных районов", "по ним ставится район по точке"],
      [K.complex_contours, "контуров ЖК", "центр ЖК и ЖК по дому"], [K.osm_addr, "адресов OSM", "дом → точка, опечатки в улицах"],
    ].map(([n, t, h]) => `<div class="ln-kc" title="${esc(h)}"><b>${n0(n)}</b><span>${t}</span></div>`).join("");
  }

  function renderRecheck(R) {
    $("lnRecheck").innerHTML = (R ? `<p class="note">Сервис сам перепроверяет все объекты раз в час и сразу после правок карты районов.
        Последний раз: <b>${day(R.ts)}</b>.</p>
      <dl class="ln-kv">
        <div><dt>Точек уточнено по адресу</dt><dd>${n0(R.points)}</dd></div>
        <div><dt>Район исправлен по вашей карте</dt><dd>${n0(R.districts_changed)}</dd></div>
        <div><dt>Район поставлен (не было)</dt><dd>${n0(R.districts_filled)}</dd></div>
        <div><dt>Добавлен второй район</dt><dd>${n0(R.districts_extra)}</dd></div>
        <div><dt>ЖК найден по контуру</dt><dd>${n0(R.complexes)}</dd></div>
        <div><dt>Оставлено по вашим правилам</dt><dd>${n0(R.kept_rule)}</dd></div>
        <div><dt>Дом у границы — оставлено</dt><dd>${n0(R.kept_border)}</dd></div>
        <div><dt>Метка фида далеко от адреса — в «Спорные»</dt><dd>${n0(R.feed_far)}</dd></div>
      </dl>` : `<p class="note">Ещё не запускалась — фоновая работа запустит сама в ближайшие минуты.</p>`) + `
      <div class="ln-acts"><button type="button" class="pill light" data-recheck="run">Перепроверить сейчас</button>
        <a class="pill light" href="/fix?kind=auto">Проверить решения сервиса</a>
        ${R && R.active_edits ? `<button type="button" class="pill light danger" data-recheck="rollback">Откатить изменения сервиса (${n0(R.active_edits)})</button>` : ""}</div>`;
  }

  // ─── всё выученное ───────────────────────────────────────────────────
  let kKind = "", kQuery = "", kTimer = 0, kinds = {};
  async function loadKnow() {
    const r = await call(`/api/admin/learn/knowledge?${new URLSearchParams({ kind: kKind, q: kQuery })}`);
    if (!r.ok) return;
    kinds = r.data.kinds;
    $("lnKinds").innerHTML = [["", "Всё"], ...Object.entries(kinds)].map(([k, t]) =>
      `<button type="button" class="chip${k === kKind ? " on" : ""}" data-kkind="${k}">${esc(t)}</button>`).join("");
    const items = r.data.items;
    $("lnList").innerHTML = items.length ? items.map((x) => `<div class="ln-row">
        <span class="ln-kind">${esc(x.kind_label)}</span>
        <span class="ln-label">${esc(x.label)}${x.type === "point" ? ` <a href="https://yandex.ru/maps/?pt=${x.lon},${x.lat}&z=17&l=map" target="_blank" rel="noopener">на карте ↗</a>` : ""}</span>
        <span class="ln-meta">${x.n > 1 ? `${x.n} раз · ` : ""}${day(x.ts)}</span>
        <button type="button" class="chip" data-forget="${esc(x.type)}|${esc(x.id)}" title="Новые объявления больше не будут так решаться (уже поправленное останется)">Забыть</button>
      </div>`).join("") : `<p class="note">${kQuery ? "Ничего не нашлось." : "Пока ничего не выучено — поправьте первый объект на экране разбора."}</p>`;
  }

  document.addEventListener("click", async (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.kkind !== undefined) { kKind = b.dataset.kkind; loadKnow(); }
    else if (b.dataset.forget) {
      const [type, id] = b.dataset.forget.split("|");
      if (!confirm("Забыть? Уже поправленные объекты останутся как есть, новые объявления так решаться не будут.")) return;
      const r = await call("/api/admin/learn/knowledge", { type, id });
      if (r.ok) { b.closest(".ln-row").remove(); load(); }
    } else if (b.dataset.recheck) {
      const rb = b.dataset.recheck === "rollback";
      if (rb && !confirm("Вернуть районы и ЖК, которые сервис поменял при перепроверке? Ваши ручные правки останутся.")) return;
      b.disabled = true; b.textContent = rb ? "Откатываем…" : "Проверяем…";
      const r = await call("/api/admin/recheck", { action: rb ? "rollback" : "run" });
      if (!r.ok) alert(r.data.detail || "Не получилось");
      else alert(rb ? `Откачено объектов: ${r.data.reverted}` : `Готово: точек ${r.data.points || 0}, районов ${(r.data.districts_changed || 0) + (r.data.districts_filled || 0)}, ЖК ${r.data.complexes || 0}`);
      load();
    }
  });
  $("lnSearch").addEventListener("input", () => {
    clearTimeout(kTimer);
    kTimer = setTimeout(() => { kQuery = $("lnSearch").value.trim(); loadKnow(); }, 300);
  });

  load().then(() => { if (!$("lnApp").hidden) loadKnow(); });
})();
