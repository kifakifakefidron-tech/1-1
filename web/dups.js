/* 1+1 · «Возможно, повтор» (/dups): сравнение двух карточек и решение по паре. */
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

  let tab = "open", data = null, pairs = [], busy = false, loading = false;
  const skipped = new Set();
  let aiInFlight = false;
  const aiErrors = new Map();
  const KEYS = { "1": "repeat", "2": "diff", "3": "market", ArrowRight: "skip" };
  const comparing = () => tab === "open" || tab === "price";
  const AI_LABELS = {
    repeat: "1 · Один объект", diff: "2 · Разные объекты",
    market: "3 · Один объект, разные агенты", unsure: "Недостаточно данных",
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

  // ─── характеристики ───
  function rows(a, b, areaInTolerance) {
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
      const cls = va == null || vb == null ? "miss" : key(va) === key(vb) ? "eq"
        : label === "Площадь" && areaInTolerance ? "near" : price ? "warn" : "ne";
      return { label, va, vb, cls };
    }).filter(Boolean);
  }

  function side(c, marked, selected) {
    const ph = (c.photos || []).slice(0, 4);
    const used = [c.favorites && `в избранном у ${c.favorites}`, c.clients && `у клиентов: ${c.clients}`,
      c.picks && `в подборках: ${c.picks}`].filter(Boolean);
    return `<article class="dp-side" data-id="${c.id}">
      <label class="dp-keep">
        <input type="radio" name="dpKeep" value="${c.id}"${selected === c.id ? " checked" : ""}>
        <span>Оставить эту карточку</span>
      </label>
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

  function pairHTML(p, selected) {
    const [ma, mb] = diffMarks(p.a.text || "", p.b.text || "");
    const R = rows(p.a, p.b, p.area_in_tolerance);
    return `<section class="dp-pair" data-anchor="${p.a.id}" data-pid="${p.id}">
      <header class="dp-head">
        <span class="mark">${esc(p.kind_label)}</span>
        <span class="dp-count">Пар в очереди: ${n0(data.counts[tab])}</span>
      </header>
      <div class="chips dp-why">${(p.why || []).map((w) => `<span class="chip">${esc(w)}</span>`).join("")}
        ${p.shared_photos ? `<span class="chip">Совпали фото: ${n0(p.shared_photos)}</span>` : ""}</div>
      <div id="dpAiNote" aria-live="polite">${aiHTML(p)}</div>
      <div class="dp-table"><div class="dp-tr dp-th"><span></span><span>Карточка № ${p.a.id}</span><span>Карточка № ${p.b.id}</span></div>
        ${R.map((r) => `<div class="dp-tr ${r.cls}"><span>${esc(r.label)}</span><b>${esc(r.va ?? "не указано")}</b><b>${esc(r.vb ?? "не указано")}</b></div>`).join("")}
      </div>
      <p class="note dp-legend"><i class="eq"></i> совпало <i class="ne"></i> различается <i class="miss"></i> указано не у обоих
        ${p.area_in_tolerance ? `<i class="near"></i> разница площади в пределах 2 м²` : ""}
        · в тексте подсвечены слова, которых нет в другой карточке</p>
      <div class="dp-sides" role="group" aria-label="Карточка, которая останется после объединения">${side(p.a, ma, selected)}${side(p.b, mb, selected)}</div>
      <div class="dp-acts">
        <p class="dp-merge-note">При объединении сохранятся цена и текст выбранной карточки, а фото обеих версий соберутся в одной галерее.</p>
        <button type="button" class="pill" data-v="repeat" title="Объединить версии объявления одного агента"><kbd>1</kbd> Один объект</button>
        <button type="button" class="pill light dp-diff" data-v="diff"><kbd>2</kbd> Разные объекты</button>
        <button type="button" class="pill light dp-mkt" data-v="market"><kbd>3</kbd> Один объект · разные агенты</button>
        <button type="button" class="pill light dp-skip" data-v="skip"><kbd>→</kbd> Пропустить</button>
      </div>
    </section>`;
  }

  function recommendedKeep(p) {
    const lid = p.ai?.verdict === "repeat" ? p.ai.keep_id : null;
    return lid === p.a.id || lid === p.b.id ? lid : null;
  }

  function aiHTML(p) {
    const verdict = AI_LABELS[p.ai?.verdict];
    if (verdict) {
      const kept = recommendedKeep(p);
      const manual = p.ai.manual_reason || (p.ai.verdict === "repeat" && !data.ai_auto_merge
        ? "Выберите карточку и подтвердите объединение кнопкой 1." : "");
      const confidence = p.ai.confidence == null ? "" : `<small class="dp-ai-confidence">Уверенность модели ${Math.floor(p.ai.confidence * 100)}%</small>`;
      return `<div class="dp-ai ${p.ai.verdict}">${ic("sparkle")}<div class="dp-ai-copy">
        <span class="dp-ai-label">DeepSeek предлагает</span><strong>${verdict}</strong>
        ${kept ? `<small class="dp-ai-fresh">Свежая версия № ${kept} · ${esc(day(p.ai.keep_version_ts))}</small>` : ""}
        ${p.ai.why ? `<small>${esc(p.ai.why)}</small>` : ""}${confidence}
        ${manual ? `<small>Ручная проверка · ${esc(manual)}</small>` : ""}
      </div></div>`;
    }
    if (!data.ai_enabled) return "";
    const error = aiErrors.get(p.id);
    return `<div class="dp-ai">${ic("sparkle")}<div class="dp-ai-copy"><span>${error ? esc(error) : "DeepSeek сравнивает карточки…"}</span>
      ${error ? `<button type="button" class="chip dp-askai" data-ai="${p.id}">Повторить запрос</button>` : ""}</div></div>`;
  }

  async function requestAI(p, retry = false) {
    if (!p || !comparing() || !data.ai_enabled || p.ai || aiInFlight || (aiErrors.has(p.id) && !retry)) return;
    aiInFlight = true;
    aiErrors.delete(p.id);
    const current = () => comparing() && pairs[0]?.id === p.id;
    if (current() && $("dpAiNote")) $("dpAiNote").innerHTML = aiHTML(p);
    let result = await call("/api/admin/dups", { id: p.id, ai: true });
    for (let i = 0; current() && result.data.pending && i < 10; i++) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      if (!current()) break;
      result = await call("/api/admin/dups", { id: p.id, ai: true });
    }
    aiInFlight = false;
    if (current() && (result.data.applied || result.data.stale)) {
      if (result.data.applied) toast("DeepSeek разобрал пару автоматически");
      return load(true);
    }
    if (result.data.ai) {
      p.ai = result.data.ai;
      if (current()) pairs[0].ai = p.ai;
    } else {
      aiErrors.set(p.id, result.data.error || (result.data.pending ? "DeepSeek ещё проверяет пару." : "DeepSeek пока не ответил."));
    }
    if (current() && $("dpAiNote")) {
      const choice = pairs[0];
      $("dpAiNote").innerHTML = aiHTML(choice);
      const keep = recommendedKeep(choice);
      const panel = $("dpAiNote").closest(".dp-pair");
      if (keep && panel && !panel.querySelector('[name="dpKeep"]:checked')) {
        const input = panel.querySelector(`[name="dpKeep"][value="${keep}"]`);
        if (input) input.checked = true;
      }
    }
    if (!current() && comparing()) requestAI(pairs[0]);
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
    load();
  }

  const STATUS = { auto: "Старые автосклейки", same: "Вы: один объект", diff: "Вы: разные", split: "Разделено",
    repeat: "Версии одного объявления", market: "Один объект · разные агенты" };
  function listHTML() {
    if (!pairs.length) return `<p class="dp-empty">${tab === "auto" ? "Сервис пока ничего не склеивал сам." : "Решений пока нет."}</p>`;
    return `<div class="dp-list">${pairs.map((p) => {
      const kept = p.keep === p.b.id ? p.b : p.a;
      const archived = kept === p.a ? p.b : p.a;
      const autoPrice = p.kind === "auto_price_revision";
      const label = p.ai_applied ? "Решение DeepSeek"
        : autoPrice ? "Цена обновлена автоматически" : p.merged ? "Объединено" : STATUS[p.status] || p.status;
      return `<div class="dp-row${autoPrice ? " dp-row-price" : ""}" data-pid="${p.id}">
      <span class="mark">${esc(label)}</span>
      <div class="dp-row-t"><a href="/?open=${kept.id}">${esc(kept.title)}</a>
        <small>${autoPrice ? "" : `${esc(p.kind_label)} · ${esc((p.why || []).slice(0, 3).join(", "))} · `}${esc(day(p.decided_at))}</small>
        <small>${autoPrice ? `№ ${archived.id} ${esc(mln(archived.price, archived.deal))} → № ${kept.id} ${esc(mln(kept.price, kept.deal))}`
          : `№ ${p.a.id} ${esc(mln(p.a.price, p.a.deal))} · № ${p.b.id} ${esc(mln(p.b.price, p.b.deal))}`}</small>
        ${p.ai?.verdict ? `<small>${p.ai_applied ? `${esc(AI_LABELS[p.ai.verdict])} · ${Math.floor(p.ai.confidence * 100)}%`
          : `DeepSeek предлагал ${esc(AI_LABELS[p.ai.verdict])}`}</small>` : ""}</div>
      ${["auto", "same"].includes(p.status) ? `<button type="button" class="pill light" data-split="${p.id}">Разделить</button>`
        : ["diff", "market", "repeat"].includes(p.status) ? `<button type="button" class="pill light" data-reopen="${p.id}">${p.merged ? "Отменить объединение" : "Вернуть в очередь"}</button>` : ""}
    </div>`;
    }).join("")}</div>`;
  }

  function statsHTML(stats) {
    return stats.map((s) => `<div class="dp-stat"><strong>${esc(s.label)}</strong>
      <small>Версии ${n0(s.repeat)} · разные агенты ${n0(s.market)} · разные объекты ${n0(s.diff)} · ждут решения ${n0(s.open)}</small></div>`).join("");
  }

  function render() {
    $("dpLead").textContent = data.ai_auto_merge
      ? "DeepSeek проверяет все три варианта от 70%. Карточки объединяются после проверки квартиры и контакта."
      : "DeepSeek применяет решения 2 и 3 от 70%. Объединение по кнопке 1 подтверждает администратор.";
    $("dpTabs").innerHTML = [["open", "Ждут решения", data.counts.open], ["price", "Только цена", data.counts.price],
      ["auto", "Старые автосклейки", data.counts.auto],
      ["done", "История решений", data.counts.done]].map(([k, l, n]) =>
      `<button type="button" class="chip${tab === k ? " on" : ""}" data-tab="${k}">${l} <b>${n0(n)}</b></button>`).join("");
    if (comparing()) {
      renderComparison();
    } else $("dpBody").innerHTML = listHTML();
    $("dpStats").innerHTML = statsHTML(data.stats || []);
    const review = data.ai_review;
    $("dpAiStats").textContent = [
      data.ai_enabled ? `DeepSeek разобрал автоматически ${n0(data.ai_auto || 0)} пар.` : "",
      review?.compared ? `Рекомендации совпали с вашими решениями в ${n0(review.agreed)} из ${n0(review.compared)} пар.` : "",
    ].filter(Boolean).join(" ");
    if (!document.activeElement || document.activeElement.id !== "dpGIn") $("dpGroups").innerHTML = groupsHTML(data.phone_groups || []);
  }

  function renderComparison() {
    const pair = pairs[0];
    const selected = pair && $("dpBody").querySelector(`[data-pid="${pair.id}"] [name="dpKeep"]:checked`);
    $("dpBody").innerHTML = pair ? pairHTML(pair, selected ? Number(selected.value) : recommendedKeep(pair)) : data.counts[tab]
      ? `<div class="dp-empty"><p>Остались пропущенные пары.</p><button type="button" class="pill" data-reset>Вернуться к пропущенным</button></div>`
      : tab === "price" && !data.price_scan_ready ? `<p class="dp-empty">Проверка опубликованных объявлений выполняется в фоне.</p>`
      : tab === "price" ? `<p class="dp-empty">${ic("check")} Нет пар, где отличается только цена.</p>`
      : `<p class="dp-empty">${ic("check")} Все пары разобраны.</p>`;
    if (tab === "price" && data.price_auto) {
      $("dpBody").insertAdjacentHTML("afterbegin", `<p class="note">Автоматически объединено версий <strong>${n0(data.price_auto)}</strong>
        · <button type="button" class="chip" data-tab="done">История решений</button></p>`);
    }
    updateActions();
  }

  function updateActions() {
    for (const button of $("dpBody").querySelectorAll("[data-v]")) button.disabled = busy || loading;
  }

  async function load(next = false) {
    loading = true;
    updateActions();
    const query = new URLSearchParams({ status: tab });
    if (comparing()) {
      query.set("view", "pair");
      if (skipped.size) query.set("skip", [...skipped].slice(-150).join(","));
    }
    const r = await call(`/api/admin/dups?${query}`);
    loading = false;
    if (r.status === 401 || r.status === 403) { $("dpDenied").hidden = false; return; }
    if (!r.ok || !r.data.counts) {
      toast(r.data.error || "Очередь не загрузилась. Попробуйте ещё раз.");
      updateActions();
      return;
    }
    $("dpApp").hidden = false;
    data = r.data;
    pairs = data.pairs || [];
    render();
    if (comparing()) requestAI(pairs[0]);
    if (next && comparing() && pairs.length) $("dpBody").querySelector(".dp-pair").scrollIntoView({ block: "start" });
  }

  async function decide(v) {
    const p = pairs[0];
    if (!comparing() || !p || busy || loading) return;
    if (v === "skip") {
      skipped.add(p.id);
      return load(true);
    }
    const body = { id: p.id, verdict: v };
    if (v === "repeat") {
      const selected = $("dpBody").querySelector('[name="dpKeep"]:checked');
      if (!selected) {
        toast("Выберите, какую карточку оставить.");
        $("dpBody").querySelector('[name="dpKeep"]')?.focus();
        return;
      }
      body.keep_id = Number(selected.value);
    }
    busy = true;
    updateActions();
    const r = await call("/api/admin/dups", body);
    busy = false;
    if (!r.ok || !r.data.ok) {
      toast(r.data.error || (r.data.stale ? "Пара уже изменилась. Загружаем следующую." : "Решение не сохранилось."));
      if (r.data.stale) return load(true);
      updateActions();
      return;
    }
    toast(v === "repeat" ? `Карточка № ${r.data.keep_id} сохранена · перенесено фото: ${n0(r.data.photos_added)}`
      : v === "market" ? "Предложения агентов связаны" : "Решение сохранено");
    return load(true);
  }

  document.addEventListener("click", async (e) => {
    const t = e.target.closest("[data-tab], [data-v], [data-ai], [data-split], [data-reopen], [data-gdel], [data-reset]");
    if (!t || busy || loading) return;
    if (t.hasAttribute("data-reset")) { skipped.clear(); return load(); }
    if (t.dataset.gdel) {
      const G = (data.phone_groups || []).filter((_, i) => i !== Number(t.dataset.gdel));
      if (!confirm("Убрать группу? Уже склеенные карточки останутся склеенными, новые объявления с этих номеров — отдельно.")) return;
      return saveGroups({ phone_groups: groupsText(G) }, "Группа убрана");
    }
    if (t.dataset.tab) { tab = t.dataset.tab; return load(); }
    if (t.dataset.v) return decide(t.dataset.v);
    if (t.dataset.ai) {
      return requestAI(pairs.find((p) => p.id === Number(t.dataset.ai)), true);
    }
    if (t.dataset.split) {
      if (!confirm("Разделить? Копия снова станет отдельной карточкой, пара запомнится как «разные». Избранное и клиенты останутся на основной.")) return;
      const r = await call("/api/admin/dups", { id: Number(t.dataset.split), split: true });
      toast(r.data.ok ? "Разделено" : "Не получилось разделить");
      return load();
    }
    if (t.dataset.reopen) {
      busy = true;
      const r = await call("/api/admin/dups", { id: Number(t.dataset.reopen), verdict: "reopen" });
      busy = false;
      toast(r.ok ? "Пара снова в очереди" : r.data.error || "Статус пары уже изменился");
      return load();
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
  document.addEventListener("keydown", (e) => {
    const v = KEYS[e.key];
    if (!v || e.repeat || !comparing() || (e.target.closest("input, textarea, select, [contenteditable=true]")
        && !(e.key === "1" && e.target.name === "dpKeep"))
        || e.ctrlKey || e.metaKey || e.altKey || e.isComposing) return;
    e.preventDefault();
    decide(v);
  });
  load();
})();
