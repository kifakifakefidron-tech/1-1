/* 1+1 · «Возможно, повтор» (/dups): сравнение кандидатов и решения по выбранным парам. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const n0 = (x) => Number(x || 0).toLocaleString("ru-RU");
  const ic = (n) => (window.ic ? window.ic(n) : "");
  const day = (ts) => ts ? new Date(ts * 1000).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "—";
  const mln = (p, deal) => !p ? "—" : deal === "rent" ? `${n0(Math.round(p / 1000))} тыс. ₽/мес`
    : `${(p / 1e6).toFixed(2).replace(/\.?0+$/, "").replace(".", ",")} млн ₽`;
  const TYPES = { flat: "Квартира", new: "Квартира", room: "Комната", house: "Дом", land: "Участок", commercial: "Коммерция" };
  const KIND_ROOM = { studio: "студия", euro: "евро", mini: "мини" };

  let tab = "open", data = null, pairs = [], group = null, focused = 0, keepId = 0, busy = false, loading = false;
  let selected = new Set();
  const skipped = new Set();
  const KEYS = { "1": "same", "2": "diff", "3": "market", ArrowRight: "skip" };
  const current = () => group?.candidates.find((p) => p.card.id === focused);
  const chosenKeep = () => {
    if (keepId === group.anchor.id || selected.has(keepId)) return keepId;
    if (selected.size === 1) return group.candidates.find((p) => selected.has(p.card.id)).keep;
    return group.anchor.id;
  };

  async function call(url, body) {
    const opts = { credentials: "same-origin", method: body ? "POST" : "GET" };
    if (body) { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(body); }
    try {
      let r = await fetch(url, opts);
      for (let i = 0; i < 4 && r.status === 503; i++) {
        await new Promise((res) => setTimeout(res, 1500 * (i + 1)));
        r = await fetch(url, opts);
      }
      const d = await r.json().catch(() => ({}));
      return { ok: r.ok, status: r.status, data: d };
    } catch {
      return { ok: false, status: 0, data: {} };
    }
  }

  function toast(text) {
    let t = $("dpToast");
    if (!t) { t = document.createElement("div"); t.id = "dpToast"; t.className = "toast"; t.setAttribute("popover", "manual"); document.body.append(t); }
    t.textContent = text;
    try { t.showPopover(); } catch { /* */ }
    clearTimeout(t._h);
    t._h = setTimeout(() => { try { t.hidePopover(); } catch { /* */ } }, 2600);
  }

  // ─── различия в тексте: слова, которых нет в другом тексте, подсвечены ───
  const norm = (w) => w.toLowerCase().replace(/ё/g, "е").replace(/[^0-9a-zа-я]/g, "");
  function diffMarks(a, b) {
    const ta = a.split(/(\s+)/), tb = b.split(/(\s+)/);
    const wa = ta.map(norm), wb = tb.map(norm);
    const n = wa.length, m = wb.length;
    if (n * m > 400000) return [esc(a), esc(b)];   // очень длинные тексты — без подсветки
    const L = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
    for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
      L[i][j] = wa[i] && wa[i] === wb[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
    const ka = new Uint8Array(n), kb = new Uint8Array(m);
    for (let i = 0, j = 0; i < n && j < m;) {
      if (wa[i] && wa[i] === wb[j]) { ka[i] = kb[j] = 1; i++; j++; }
      else if (L[i + 1][j] >= L[i][j + 1]) i++; else j++;
    }
    const out = (t, w, k) => t.map((s, i) => !w[i] || k[i] ? esc(s) : `<mark>${esc(s)}</mark>`).join("");
    return [out(ta, wa, ka), out(tb, wb, kb)];
  }

  // ─── характеристики: совпало / различается / не указано у одного ───
  function rows(a, b) {
    const room = (c) => c.rooms == null ? (c.room_kind === "studio" ? "студия" : null)
      : `${c.rooms}${c.room_kind && KIND_ROOM[c.room_kind] ? ` (${KIND_ROOM[c.room_kind]})` : ""}`;
    const fl = (c) => c.floor == null ? null : `${c.floor}${c.floors ? ` из ${c.floors}` : ""}`;
    const addr = (c) => [c.street, c.house].filter(Boolean).join(", ") || null;
    const R = [
      ["Тип", (c) => TYPES[c.type] || c.type, true],
      ["Комнаты", room], ["Площадь", (c) => c.area ? `${String(c.area).replace(".", ",")} м²` : null],
      ["Участок", (c) => c.land ? `${String(c.land).replace(".", ",")} сот.` : null],
      ["Этаж", fl], ["ЖК", (c) => c.complex], ["Улица, дом", addr], ["Район", (c) => c.district || c.settlement],
      ["Цена", (c) => mln(c.price, c.deal), false, true],
      ["Номер в тексте", (c) => (c.text_phones || []).join(", ") || null],
      ["Прислал", (c) => (c.sender_phones || [])[0] || c.sender_name || null],
    ];
    return R.map(([label, f, always, price]) => {
      const va = f(a), vb = f(b);
      if (va == null && vb == null && !always) return null;
      const key = (v) => String(v ?? "").toLowerCase().replace(/ё/g, "е").replace(/\s+/g, "");
      const cls = va == null || vb == null ? "miss" : key(va) === key(vb) ? "eq" : price ? "warn" : "ne";
      return { label, va, vb, cls };
    }).filter(Boolean);
  }

  function side(c, isKeep, marked) {
    const ph = (c.photos || []).slice(0, 4);
    const used = [c.favorites && `в избранном у ${c.favorites}`, c.clients && `у клиентов: ${c.clients}`,
      c.picks && `в подборках: ${c.picks}`].filter(Boolean);
    return `<article class="dp-side${isKeep ? " keep" : ""}" data-id="${c.id}">
      <label class="dp-keep"><input type="radio" name="dpKeep" value="${c.id}"${isKeep ? " checked" : ""}>
        <span>Оставить эту карточку</span></label>
      <div class="dp-ph${ph.length ? "" : " none"}">${ph.length ? ph.map((u) => `<img src="${esc(u)}" alt="" loading="lazy">`).join("")
        : "<span>Фото нет</span>"}${(c.photos || []).length > 4 ? `<em>+${c.photos.length - 4}</em>` : ""}</div>
      <a class="dp-title" href="/?open=${c.id}">${esc(c.title || `Объект ${c.id}`)}</a>
      <div class="dp-price">${esc(mln(c.price, c.deal))}</div>
      <p class="dp-meta">№ ${c.id} · впервые ${esc(day(c.first_seen))} · последнее ${esc(day(c.last_seen))}
        · присылали ${n0(c.seen_count)} раз${c.chat ? ` · ${esc(c.chat)}` : ""}</p>
      ${used.length ? `<p class="dp-used">${used.map(esc).join(" · ")}</p>` : ""}
      <div class="dp-text">${marked}</div>
      <div class="dp-phones">
        <div class="dp-phone"><span>Номер в объявлении</span><b>${(c.text_phones || []).map(esc).join(", ") || "в тексте нет"}</b>${c.text_from === "card" ? `<small>в общей части сообщения</small>` : c.text_from === "catalog" || c.text_from === "feed" ? `<small>контакт из каталога</small>` : ""}</div>
        <div class="dp-phone snd"><span>Прислал в чат</span><b>${(c.sender_phones || []).slice(0, 2).map(esc).join(", ") || (c.sender_name ? esc(c.sender_name) : "не виден")}</b>${!(c.sender_phones || []).length && c.sender_name ? `<small>номер в чате скрыт</small>` : ""}</div>
      </div>
    </article>`;
  }

  function candidatesHTML() {
    const tile = (c, anchor) => {
      const details = [c.rooms == null ? "" : `${c.rooms} комн.`, c.area ? `${c.area} м²` : "",
        c.floor == null ? "" : `${c.floor} этаж`].filter(Boolean).join(" · ");
      const summary = `${c.photos?.[0] ? `<img src="${esc(c.photos[0])}" alt="" loading="lazy">` : ""}
        <span><strong>${esc(mln(c.price, c.deal))}</strong><small>${esc(c.title || `Объект №${c.id}`)}</small>
        <small>${esc(details || `№ ${c.id}`)}</small></span>`;
      return `<div class="dp-candidate${anchor ? " base" : ""}" data-card="${c.id}">
        ${anchor ? `<div class="dp-tile-check">Опорная карточка</div><div class="dp-tile-open">${summary}</div>`
          : `<label class="dp-tile-check"><input type="checkbox" name="dpMark" value="${c.id}"
            ${selected.has(c.id) ? " checked" : ""} aria-label="Выбрать карточку № ${c.id}"> № ${c.id}</label>
            <button type="button" class="dp-tile-open" data-focus="${c.id}" aria-pressed="${focused === c.id}"
              aria-label="Сравнить карточку № ${c.id}">${summary}</button>`}
        </div>`;
    };
    return `<div class="dp-candidates-head"><span>Отметьте кандидатов для общего решения</span>
      <button type="button" class="pill light" data-all>Выбрать все</button></div>
      <div class="dp-candidates" aria-label="Карточки для сравнения">
        ${tile(group.anchor, true)}${group.candidates.map((p) => tile(p.card, false)).join("")}</div>`;
  }

  function pairHTML(choice) {
    const p = { ...choice, a: group.anchor, b: choice.card };
    const keep = chosenKeep();
    const [ma, mb] = diffMarks(p.a.text || "", p.b.text || "");
    const R = rows(p.a, p.b);
    const ai = p.ai ? `<div class="dp-ai ${p.ai.same === true ? "same" : p.ai.same === false ? "diff" : ""}">${ic("sparkle")}
        <span><b>Нейросеть: ${p.ai.same === true ? "похоже, один объект" : p.ai.same === false ? "похоже, разные" : "не уверена"}</b>
        ${p.ai.confidence != null ? ` · уверенность ${Math.round(p.ai.confidence * 100)} %` : ""}<small>${esc(p.ai.why)}</small></span></div>`
      : data.ai_enabled ? `<button type="button" class="pill light dp-askai" data-ai="${p.id}">${ic("sparkle")} Спросить нейросеть</button>` : "";
    const nums = otherNums(p);
    return `<section class="dp-pair" data-anchor="${p.a.id}" data-pid="${p.id}">
      <header class="dp-head">
        <div><span class="mark">${esc(p.kind_label)}</span>
          ${p.kind === "similar" ? "" : `<span class="dp-sim">текст похож на ${Math.round((p.sim || 0) * 100)} %</span>`}</div>
        <span class="dp-count">Карточек для сравнения: ${n0(group.candidates.length + 1)}</span>
      </header>
      ${group.candidates.length > 1 ? candidatesHTML() : ""}
      <div class="chips dp-why">${(p.why || []).map((w) => `<span class="chip">${esc(w)}</span>`).join("")}</div>
      ${ai}
      <div class="dp-table"><div class="dp-tr dp-th"><span></span><span>Опорная № ${p.a.id}</span><span>Кандидат № ${p.b.id}</span></div>
        ${R.map((r) => `<div class="dp-tr ${r.cls}"><span>${esc(r.label)}</span><b>${esc(r.va ?? "не указано")}</b><b>${esc(r.vb ?? "не указано")}</b></div>`).join("")}
      </div>
      <p class="note dp-legend"><i class="eq"></i> совпало <i class="ne"></i> различается <i class="miss"></i> указано не у обоих
        · в тексте подсвечены слова, которых нет в другой карточке</p>
      <div class="dp-sides">${side(p.a, keep === p.a.id, ma)}${side(p.b, keep === p.b.id, mb)}</div>
      ${nums ? `<div class="dp-same-agent"><span>Номера разные: ${esc(nums.pretty)}. Если это один агент или агентство —</span>
        <button type="button" class="pill light" data-group="${esc(nums.raw.join(","))}">Это один агент — объединить номера</button></div>` : ""}
      <div class="dp-acts">
        <div class="dp-selection" id="dpSelection" aria-live="polite"></div>
        <button type="button" class="pill dp-same" data-v="same"><kbd>1</kbd> Склеить карточки</button>
        <button type="button" class="pill light dp-diff" data-v="diff"><kbd>2</kbd> Разные объекты</button>
        <button type="button" class="pill light dp-mkt" data-v="market" title="Один объект у разных агентов"><kbd>3</kbd> Связать предложения</button>
        <button type="button" class="pill light dp-skip" data-v="skip"><kbd>→</kbd> Пропустить</button>
      </div>
    </section>`;
  }

  // номера двух карточек не пересекаются — предложить «это один агент»
  function otherNums(p) {
    const a = p.a.raw_phones || [], b = p.b.raw_phones || [];
    if (!a.length || !b.length || a.some((x) => b.includes(x))) return null;
    const inGroup = (x) => (data.phone_groups || []).find((g) => g.phones.some((n) => n.phone === x));
    const ga = inGroup(a[0]);
    if (ga && b.some((x) => ga.phones.some((n) => n.phone === x))) return null;
    const raw = [...new Set([...a, ...b])].slice(0, 6);
    return { raw, pretty: [...(p.a.text_phones || []).concat(p.a.sender_phones || []).slice(0, 1),
      ...(p.b.text_phones || []).concat(p.b.sender_phones || []).slice(0, 1)].join(" и ") };
  }

  // ─── «Один агент — несколько номеров» ───
  function groupsHTML(G) {
    return `${G.length ? G.map((g, i) => `<div class="dp-group" data-gi="${i}">
        <div class="dp-gnums">${g.phones.map((n) => `<span class="chip">${esc(n.pretty)} <small>${n.n} на сайте</small></span>`).join("")}</div>
        <button type="button" class="pill light" data-gdel="${i}">Убрать группу</button></div>`).join("")
      : `<p class="dp-empty sm">Групп пока нет.</p>`}
      <form class="dp-gadd" id="dpGAdd">
        <label for="dpGIn">Новая группа — номера одного агента через запятую</label>
        <div class="dp-grow"><input id="dpGIn" type="text" inputmode="tel" autocomplete="off"
          placeholder="+7 918 000-00-00, +7 960 000-00-00">
        <button type="submit" class="pill">Добавить</button></div>
      </form>`;
  }
  const groupsText = (G) => G.map((g) => g.phones.map((n) => n.phone).join(", ")).join("\n");
  async function saveGroups(body, msg) {
    const r = await call("/api/admin/dups", body);
    if (!r.ok || r.data.ok === false) { toast(r.data.error || "Не сохранилось"); return; }
    data.phone_groups = r.data.phone_groups || [];
    toast(`${msg} · карточки с этими номерами перепроверятся в течение пары минут`);
    load(true);
  }

  const STATUS = { auto: "Склеил сервис", same: "Вы: один объект", diff: "Вы: разные", split: "Разделено",
    market: "Вы: один объект, разные агенты" };
  function listHTML() {
    if (!pairs.length) return `<p class="dp-empty">${tab === "auto" ? "Сервис пока ничего не склеивал сам." : "Решений пока нет."}</p>`;
    return `<div class="dp-list">${pairs.map((p) => `<div class="dp-row" data-pid="${p.id}">
      <span class="mark">${esc(STATUS[p.status] || p.status)}</span>
      <div class="dp-row-t"><a href="/?open=${p.keep || p.a.id}">${esc(p.a.id === p.keep ? p.a.title : p.b.title)}</a>
        <small>${esc(p.kind_label)} · ${esc((p.why || []).slice(0, 3).join(", "))} · ${esc(day(p.decided_at))}</small>
        <small>№ ${p.a.id} ${esc(mln(p.a.price, p.a.deal))} · № ${p.b.id} ${esc(mln(p.b.price, p.b.deal))}</small></div>
      ${["auto", "same"].includes(p.status) ? `<button type="button" class="pill light" data-split="${p.id}">Разделить</button>`
        : ["diff", "market"].includes(p.status) ? `<button type="button" class="pill light" data-reopen="${p.id}">Вернуть в очередь</button>` : ""}
    </div>`).join("")}</div>`;
  }

  function statsHTML(S) {
    return S.map((s) => {
      const share = s.share == null ? "—" : `${Math.round(s.share * 100)} %`;
      const tail = s.auto_kind
        ? `<small>Склеивает сам: ${n0(s.auto)}. Разделили вы: ${n0(s.diff)}.</small>`
        : `<small>Ваши ответы: один объект — ${n0(s.same)}, разные — ${n0(s.diff)}${s.market ? `, один объект у разных агентов — ${n0(s.market)}` : ""}. В очереди: ${n0(s.open)}.</small>
           ${s.trusted ? `<button type="button" class="pill light" data-trust="${s.kind}" data-on="0">Перестать склеивать сами</button>`
             : s.can_trust ? `<button type="button" class="pill" data-trust="${s.kind}" data-on="1">Склеивать такие сами</button>`
             : `<small class="dp-wait">Склеивать сам сервис сможет после 20 ваших ответов, если «один объект» будет в 95 % и чаще.</small>`}`;
      return `<div class="dp-stat${s.trusted ? " on" : ""}"><b>${share}</b><strong>${esc(s.label)}</strong>
        <span>${s.auto_kind ? "насколько верна склейка" : "как часто это один объект"}</span>${tail}</div>`;
    }).join("");
  }

  function render() {
    $("dpTabs").innerHTML = [["open", "Ждут решения", data.counts.open], ["auto", "Склеил сервис", data.counts.auto],
      ["done", "Ваши решения", data.counts.done]].map(([k, l, n]) =>
      `<button type="button" class="chip${tab === k ? " on" : ""}" data-tab="${k}">${l} <b>${n0(n)}</b></button>`).join("");
    if (tab === "open") {
      renderComparison();
    } else $("dpBody").innerHTML = listHTML();
    $("dpStats").innerHTML = statsHTML(data.stats || []);
    if (!document.activeElement || document.activeElement.id !== "dpGIn") $("dpGroups").innerHTML = groupsHTML(data.phone_groups || []);
  }

  function renderComparison() {
    const p = current();
    const old = $("dpBody").querySelector(".dp-pair");
    const scroll = Number(old?.dataset.anchor) === group?.anchor.id ? old.querySelector(".dp-candidates")?.scrollLeft : 0;
    $("dpBody").innerHTML = p ? pairHTML(p) : data.counts.open
      ? `<div class="dp-empty"><p>Остались пропущенные карточки.</p><button type="button" class="pill" data-reset>Вернуться к пропущенным</button></div>`
      : `<p class="dp-empty">${ic("check")} Всё разобрано. Новые пары появятся сами, когда в чатах придут похожие объявления.</p>`;
    const strip = $("dpBody").querySelector(".dp-candidates");
    if (strip && scroll) strip.scrollLeft = scroll;
    updateSelection();
  }

  const mergeBlocked = () => group.candidates.some((p) => p.market_only && selected.has(p.card.id));

  function updateSelection() {
    if (tab !== "open" || !group) return;
    const unavailable = busy || loading;
    const blocked = mergeBlocked();
    const root = $("dpBody");
    $("dpSelection").textContent = `Выбрано ${selected.size} из ${group.candidates.length}${blocked ? " · разные агенты" : ""}`;
    for (const button of root.querySelectorAll("[data-v]")) {
      const v = button.dataset.v;
      button.disabled = unavailable || (v !== "skip" && (!selected.size || (v === "same" && blocked)));
    }
    const selectAll = root.querySelector("[data-all]");
    if (selectAll) {
      selectAll.textContent = selected.size === group.candidates.length ? "Снять выбор" : "Выбрать все";
      selectAll.disabled = unavailable;
    }
    for (const input of root.querySelectorAll('input[name="dpMark"]')) {
      input.checked = selected.has(Number(input.value));
      input.disabled = unavailable;
      input.closest(".dp-candidate").classList.toggle("selected", input.checked);
    }
    for (const input of root.querySelectorAll('input[name="dpKeep"]')) {
      const id = Number(input.value);
      input.checked = id === chosenKeep();
      input.disabled = unavailable || blocked || !selected.size || (id !== group.anchor.id && !selected.has(id));
      input.closest(".dp-side").classList.toggle("keep", input.checked && !input.disabled);
    }
  }

  async function load(retain = false) {
    loading = true;
    updateSelection();
    const query = new URLSearchParams({ status: tab });
    if (tab === "open") {
      query.set("view", "group");
      if (skipped.size) query.set("skip", [...skipped].slice(-150).join(","));
      if (retain && group) query.set("anchor", group.anchor.id);
    }
    const r = await call(`/api/admin/dups?${query}`);
    loading = false;
    if (r.status === 401 || r.status === 403) { $("dpDenied").hidden = false; return; }
    if (!r.ok || !r.data.counts) {
      toast(r.data.error || "Очередь не загрузилась. Попробуйте ещё раз.");
      updateSelection();
      return;
    }
    $("dpApp").hidden = false;
    data = r.data;
    pairs = data.pairs || [];
    const oldAnchor = group?.anchor.id;
    group = data.group || null;
    if (group) {
      if (!retain || group.anchor.id !== oldAnchor) {
        selected.clear();
        focused = keepId = 0;
      }
      const ids = group.candidates.map((p) => p.card.id);
      selected = new Set(ids.filter((id) => selected.has(id)));
      if (!ids.includes(focused)) focused = ids[0];
      if (!selected.size) selected.add(focused);
    }
    render();
  }

  async function decide(v) {
    if (!group || busy || loading) return;
    if (v === "skip") {
      skipped.add(group.anchor.id);
      return load();
    }
    if (!selected.size) return;
    if (v === "same" && mergeBlocked()) {
      toast("Для разных агентов используйте клавишу 3.");
      return;
    }
    busy = true;
    updateSelection();
    const r = await call("/api/admin/dups", { anchor: group.anchor.id, candidates: [...selected], verdict: v, keep: chosenKeep() });
    busy = false;
    if (!r.ok || !r.data.ok) {
      toast(r.data.error || "Не получилось. Попробуйте ещё раз.");
      if (r.data.stale) return load(true);
      updateSelection();
      return;
    }
    toast(v === "same" ? "Карточки склеены" : v === "market" ? "Предложения связаны" : "Различия сохранены");
    return load(true);
  }

  document.addEventListener("click", async (e) => {
    const t = e.target.closest("[data-tab], [data-v], [data-ai], [data-split], [data-reopen], [data-trust], [data-group], [data-gdel], [data-all], [data-reset], [data-focus], .dp-side");
    if (!t || busy || loading) return;
    if (t.hasAttribute("data-reset")) { skipped.clear(); return load(); }
    if (t.hasAttribute("data-all")) {
      selected = selected.size === group.candidates.length ? new Set() : new Set(group.candidates.map((p) => p.card.id));
      updateSelection();
      return;
    }
    if (t.dataset.focus) {
      const follow = selected.size === 1 && selected.has(focused);
      focused = Number(t.dataset.focus);
      if (follow) selected = new Set([focused]);
      renderComparison();
      $("dpBody").querySelector(`[data-focus="${focused}"]`)?.focus({ preventScroll: true });
      return;
    }
    if (t.dataset.group) {
      t.disabled = true;
      return saveGroups({ phone_group_add: t.dataset.group.split(",") }, "Номера объединены: теперь это один агент");
    }
    if (t.dataset.gdel) {
      const G = (data.phone_groups || []).filter((_, i) => i !== Number(t.dataset.gdel));
      if (!confirm("Убрать группу? Уже склеенные карточки останутся склеенными, новые объявления с этих номеров — отдельно.")) return;
      return saveGroups({ phone_groups: groupsText(G) }, "Группа убрана");
    }
    if (t.dataset.tab) { tab = t.dataset.tab; return load(); }
    if (t.dataset.v) return decide(t.dataset.v);
    if (t.dataset.ai) {
      t.disabled = true; t.textContent = "Спрашиваю…";
      const r = await call("/api/admin/dups", { id: Number(t.dataset.ai), ai: true });
      const choice = group?.candidates.find((p) => p.id === Number(t.dataset.ai));
      if (r.data.ai && choice) { choice.ai = r.data.ai; renderComparison(); }
      else if (!r.data.ai) { toast(r.data.error || "Нейросеть не ответила"); t.disabled = false; }
      return;
    }
    if (t.dataset.split) {
      if (!confirm("Разделить? Копия снова станет отдельной карточкой, пара запомнится как «разные». Избранное и клиенты останутся на основной.")) return;
      const r = await call("/api/admin/dups", { id: Number(t.dataset.split), split: true });
      toast(r.data.ok ? "Разделено" : "Не получилось разделить");
      return load();
    }
    if (t.dataset.reopen) {
      await call("/api/admin/dups", { id: Number(t.dataset.reopen), verdict: "reopen" });
      toast("Пара снова в очереди");
      return load();
    }
    if (t.dataset.trust) {
      await call("/api/admin/dups", { trust: t.dataset.trust, on: t.dataset.on === "1" });
      toast(t.dataset.on === "1" ? "Такие пары сервис теперь склеивает сам" : "Такие пары снова идут к вам");
      return load(true);
    }
    if (t.classList.contains("dp-side") && !e.target.closest("a, input, label")) {   // клик по карточке — оставить её
      const inp = t.querySelector('input[name="dpKeep"]');
      if (inp && !inp.disabled) { keepId = Number(inp.value); updateSelection(); }
    }
  });
  document.addEventListener("submit", (e) => {
    if (e.target.id !== "dpGAdd") return;
    e.preventDefault();
    const v = $("dpGIn").value.trim();
    const n = v.split(/[,;\n]/).filter((x) => x.replace(/\D/g, "").length >= 10).length;
    if (n < 2) { toast("Впишите хотя бы два номера через запятую"); return; }
    $("dpGIn").blur();
    const G = groupsText(data.phone_groups || []);
    saveGroups({ phone_groups: (G ? G + "\n" : "") + v }, "Группа добавлена");
  });
  document.addEventListener("change", (e) => {
    if (e.target.name === "dpKeep") {
      keepId = Number(e.target.value);
      updateSelection();
    }
    if (e.target.name === "dpMark") {
      const id = Number(e.target.value);
      if (e.target.checked) selected.add(id); else selected.delete(id);
      updateSelection();
    }
  });
  document.addEventListener("keydown", (e) => {
    const v = KEYS[e.key];
    if (!v || e.repeat || tab !== "open" || e.target.closest("input:not([type=checkbox]):not([type=radio]), textarea, select, [contenteditable=true]")
        || e.ctrlKey || e.metaKey || e.altKey || e.isComposing) return;
    e.preventDefault();
    decide(v);
  });
  load();
})();
