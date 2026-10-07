/* 1+1 · кабинет агента: подтверждение номера кодом из MAX/WhatsApp, «Мои объявления»,
   правка цены/описания/фото, «продано», свой объект на 30 дней (только с подпиской).
   API: /api/agent, /api/agent/verify, /api/agent/listings[/{id}[/photos]]. */
(() => {
  "use strict";
  const P = window.OnePlus;
  if (!P) return;
  const { call, esc, num, toast, openDlg, closeDlg, fmtPrice, fmtDay } = P;
  const $ = (id) => document.getElementById(id);
  const dlg = $("agentDlg");
  const body = $("agentBody");
  let data = null;          // ответ /api/agent
  let waitTimer = 0;        // ожидание кода подтверждения
  const TYPES = { flat: "Квартира", room: "Комната", house: "Дом", land: "Участок", commercial: "Коммерция" };

  // ─── главный экран ──────────────────────────────────────────────────────
  async function open(editId) {
    if (!P.me()) { P.openLogin("Чтобы управлять своими объявлениями, войдите."); return; }
    body.innerHTML = `<h2 id="agentTitle">Кабинет агента</h2><div class="skel"><i></i></div>`;
    openDlg(dlg);
    const r = await call("/api/agent");
    if (!r.ok) { body.innerHTML = `<h2>Кабинет агента</h2><p class="err">${esc(r.data.detail || "Не получилось загрузить.")}</p>`; return; }
    data = r.data;
    if (editId) { const it = data.items.find((i) => i.id === editId); if (it) { renderEdit(it); return; } }
    render();
  }

  function render() {
    clearInterval(waitTimer);
    const phones = data.phones;
    const verify = phones.length ? `
        <p class="note">Ваши номера: <b>${phones.map(esc).join(", ")}</b> ✓</p>
        <details class="agent-more"><summary>Добавить ещё номер</summary>${verifyForm()}</details>`
      : `<div class="agent-step">
          <p><b>Шаг 1. Подтвердите свой номер.</b> Так мы поймём, какие объявления на сайте — ваши.
            Никто другой не сможет их изменить.</p>${verifyForm()}</div>`;
    const items = data.items.map(itemHTML).join("");
    const list = phones.length || data.items.length ? `
        <div class="agent-head"><h3 class="d-h">Мои объявления · ${data.items.length}</h3>
          ${data.access && phones.length ? `<button type="button" class="pill" data-agent-new>+ Добавить объект</button>` : ""}</div>
        ${data.access ? "" : `<p class="note">Добавлять и править объекты можно с активной подпиской.</p>`}
        ${items || `<p class="note">Пока не нашли объявлений с вашим номером. Как только вы разместите объект в чатах —
          он появится здесь. Или добавьте объект сами.</p>`}` : "";
    body.innerHTML = `
      <div class="dlg-logo">${P.LOGO}</div>
      <h2 id="agentTitle">Кабинет агента</h2>
      ${verify}
      ${list}
      <p class="note agent-rules">Правила: менять можно только объявления с вашим подтверждённым номером. Свой объект
        висит ${data.own_days} дней — за 3 дня до конца придёт письмо «ещё актуален?». «Да» — продлим, «нет» — снимем сразу,
        без ответа — снимем по сроку. Все правки сохраняются в журнале.</p>
      <div class="dlg-actions"><button type="button" class="pill light" data-close>Закрыть</button></div>`;
  }

  function verifyForm() {
    return `<form class="inline-form" id="verifyForm">
        <input id="verifyPhone" type="tel" inputmode="tel" autocomplete="tel" placeholder="+7 918 123-45-67" aria-label="Ваш номер">
        <button type="submit" class="pill">Получить код</button></form>
      <div id="verifyBox"></div>`;
  }

  function itemHTML(o) {
    const sold = !o.is_active;
    const own = o.source === "own";
    const until = own && o.expires_at && !sold ? ` · до ${esc(fmtDay(o.expires_at))}` : "";
    return `<div class="agent-item${sold ? " off" : ""}">
      ${o.photos && o.photos[0] ? `<img src="${esc(o.photos[0])}" alt="" loading="lazy">` : `<span class="agent-noimg">${o.photos ? "нет фото" : ""}</span>`}
      <div class="agent-info">
        <b>${esc(o.title)}</b>
        <span>${esc(fmtPrice(o.price, o.deal))}</span>
        <span class="note">${sold ? "снято" : "на сайте"}${own ? " · добавлен вами" : " · из чатов"}${until}</span>
      </div>
      <div class="agent-btns">
        ${o.can_edit ? `<button type="button" class="pill light" data-agent-edit="${o.id}">Изменить</button>` : `<span class="note">управляет другой агент</span>`}
        ${o.can_edit ? (sold
          ? `<button type="button" class="ghost" data-agent-status="active" data-id="${o.id}">Вернуть</button>`
          : `<button type="button" class="ghost" data-agent-status="sold" data-id="${o.id}">Продано / снять</button>`) : ""}
      </div></div>`;
  }

  // ─── подтверждение номера ───────────────────────────────────────────────
  async function startVerify(phone) {
    const box = $("verifyBox");
    const r = await call("/api/agent/verify", { phone });
    if (!r.ok) { box.innerHTML = `<p class="err">${esc(r.data.detail || "Не получилось.")}</p>`; return; }
    const { code, send_to: to, phone: ph } = r.data;
    const text = `1+1-${code}`;
    const digits = to.replace(/\D/g, "");
    const viaMax = data.channels.includes("max"), viaWa = data.channels.includes("wa");
    box.innerHTML = `<div class="agent-code">
        <p>Отправьте сообщение <b class="code">${esc(text)}</b><br>с номера <b>${esc(ph)}</b> на номер <b>${esc(to)}</b>
          ${viaMax && viaWa ? "в MAX или WhatsApp" : viaMax ? "в MAX" : "в WhatsApp"}.</p>
        <div class="agent-code-btns">
          <button type="button" class="pill light" data-copy="${esc(text)}">Скопировать код</button>
          ${viaWa ? `<a class="pill light" href="https://wa.me/${digits}?text=${encodeURIComponent(text)}" target="_blank" rel="noopener">Открыть WhatsApp</a>` : ""}
          ${viaMax ? `<button type="button" class="pill light" data-copy="${esc(to)}">Скопировать наш номер для MAX</button>` : ""}
        </div>
        <p class="note" id="verifyWait">Ждём сообщение… Обычно это занимает до 2 минут. Окно можно не закрывать.</p></div>`;
    let tries = 0;
    clearInterval(waitTimer);
    waitTimer = setInterval(async () => {
      if (!dlg.open || ++tries > 360) { clearInterval(waitTimer); return; }
      const s = await call(`/api/agent/verify?phone=${encodeURIComponent(ph)}`);
      if (s.ok && s.data.status === "ok") {
        clearInterval(waitTimer);
        toast("✓ Номер подтверждён");
        open();
      } else if (s.ok && s.data.status === "expired") {
        clearInterval(waitTimer);
        const w = $("verifyWait");
        if (w) w.textContent = "Код устарел. Запросите новый.";
      }
    }, 5000);
  }

  // ─── правка объявления / новый объект ───────────────────────────────────
  const field = (name, label, value, attrs = "") =>
    `<label class="agent-field"><span>${esc(label)}</span><input name="${name}" value="${esc(value ?? "")}" ${attrs}></label>`;

  function priceValue(o) {
    if (!o.price) return "";
    return o.deal !== "rent" && o.price >= 1e6 ? String(+(o.price / 1e6).toFixed(3)).replace(".", ",") : String(o.price);
  }

  function renderEdit(o) {
    clearInterval(waitTimer);
    const isNew = !o.id;
    const own = isNew || o.source === "own";
    const opts = (map, cur) => Object.entries(map).map(([k, v]) => `<option value="${k}"${k === cur ? " selected" : ""}>${esc(v)}</option>`).join("");
    const meta = P.meta();
    const full = own ? `
        <div class="agent-grid">
          <label class="agent-field"><span>Тип</span><select name="type">${opts(TYPES, o.type || "flat")}</select></label>
          <label class="agent-field"><span>Сделка</span><select name="deal">${opts({ sale: "Продажа", rent: "Аренда" }, o.deal || "sale")}</select></label>
          ${field("rooms", "Комнат (0 — студия)", o.rooms, 'inputmode="numeric"')}
          ${field("area", "Площадь, м²", o.area, 'inputmode="decimal"')}
          ${field("land", "Участок, соток", o.land, 'inputmode="decimal"')}
          ${field("floor", "Этаж", o.floor, 'inputmode="numeric"')}
          ${field("floors", "Этажей в доме", o.floors, 'inputmode="numeric"')}
          ${field("district", "Район", o.district, 'list="agentDistricts"')}
          ${field("complex", "ЖК", o.complex)}
          ${field("street", "Улица", o.street)}
          ${field("house", "Дом", o.house)}
        </div>
        <p class="note"><a class="ya-link" data-ya-form href="https://yandex.ru/maps/35/krasnodar/" target="_blank" rel="noopener">🔎 Проверить адрес в Яндекс Картах ↗</a></p>
        <datalist id="agentDistricts">${(meta.districts || []).map((d) => `<option value="${esc(d)}">`).join("")}</datalist>` : "";
    const photos = isNew ? `<p class="note">Фото можно добавить сразу после сохранения.</p>` : photosHTML(o);
    body.innerHTML = `
      <h2 id="agentTitle">${isNew ? "Новый объект" : "Изменить объявление"}</h2>
      ${isNew ? "" : `<p class="note">${esc(o.title)}${own ? "" : " · объявление из чатов: можно поменять цену, описание и фото"}</p>`}
      <form id="agentForm" data-id="${o.id || ""}" class="agent-form">
        ${full}
        ${field("price", "Цена — для продажи в млн (например 6,5), для аренды в ₽/мес", priceValue(o), 'inputmode="decimal" required')}
        <label class="agent-field"><span>Описание</span><textarea name="description" rows="6" maxlength="3000"
          placeholder="Номер телефона писать не нужно — покупатели увидят ваш подтверждённый номер">${esc(o.description || "")}</textarea></label>
        <p class="err" id="agentErr" hidden></p>
        <div class="dlg-actions">
          <button type="button" class="ghost" data-agent-back>← Назад</button>
          <button type="submit" class="pill">${isNew ? "Опубликовать" : "Сохранить"}</button>
        </div>
      </form>
      ${photos}`;
    dlg.scrollTop = 0;
  }

  function photosHTML(o) {
    const list = (o.photos || []).map((u) => `<div class="agent-photo"><img src="${esc(u)}" alt="">
        <button type="button" class="agent-photo-del" data-photo-del="${esc(u)}" data-id="${o.id}" aria-label="Удалить фото">✕</button></div>`).join("");
    return `<h3 class="d-h">Фото · ${(o.photos || []).length} из ${data.max_photos}</h3>
      <div class="agent-photos" id="agentPhotos">${list}
        <label class="agent-photo add"><input type="file" accept="image/*" multiple hidden data-photo-add="${o.id}">＋<span>Добавить</span></label>
      </div>`;
  }

  async function save(form) {
    const err = $("agentErr");
    err.hidden = true;
    const fd = Object.fromEntries(new FormData(form).entries());
    const id = Number(form.dataset.id);
    const btn = form.querySelector("[type=submit]");
    btn.disabled = true;
    const r = await call(id ? `/api/agent/listings/${id}` : "/api/agent/listings", fd);
    btn.disabled = false;
    if (!r.ok) { err.textContent = r.data.detail || "Не получилось сохранить."; err.hidden = false; return; }
    data.items = r.data.items;
    toast(id ? "Сохранено" : "Объект опубликован");
    const newId = id || r.data.id;
    const it = data.items.find((i) => i.id === newId);
    if (!id && it) renderEdit(it); else render();  // новый объект — сразу к фото
    P.reloadList();
  }

  async function setStatus(id, status) {
    if (status === "sold" && !confirm("Снять объект с сайта? Его можно будет вернуть.")) return;
    const r = await call(`/api/agent/listings/${id}`, { status });
    if (!r.ok) { toast(r.data.detail || "Не получилось."); return; }
    data.items = r.data.items;
    toast(status === "sold" ? "Объект снят с сайта" : "Объект снова на сайте");
    render();
    P.reloadList();
  }

  // Фото уменьшаем в браузере (до 1600 px, JPEG) — быстрее грузится и не забивает сервер
  function shrink(file) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => {
        const k = Math.min(1, 1600 / Math.max(img.width, img.height));
        const c = document.createElement("canvas");
        c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
        URL.revokeObjectURL(img.src);
        resolve(c.toDataURL("image/jpeg", 0.85));
      };
      img.onerror = () => reject(new Error("bad image"));
      img.src = URL.createObjectURL(file);
    });
  }

  async function addPhotos(id, files) {
    let it = data.items.find((i) => i.id === id);
    for (const f of files) {
      toast(`Загружаем фото…`);
      let dataUrl;
      try { dataUrl = await shrink(f); } catch { toast("Этот файл не похож на фото"); continue; }
      const r = await call(`/api/agent/listings/${id}/photos`, { data: dataUrl });
      if (!r.ok) { toast(r.data.detail || "Фото не загрузилось"); break; }
      it.photos = r.data.photos;
    }
    toast("Фото сохранены");
    reRenderPhotos(it);
  }

  async function delPhoto(id, url) {
    const r = await call(`/api/agent/listings/${id}/photos`, { url }, "DELETE");
    if (!r.ok) { toast(r.data.detail || "Не получилось."); return; }
    const it = data.items.find((i) => i.id === id);
    it.photos = r.data.photos;
    reRenderPhotos(it);
  }

  function reRenderPhotos(it) {
    const box = $("agentPhotos");
    if (box) box.parentElement.querySelectorAll("h3.d-h, #agentPhotos").forEach((el) => el.remove());
    body.insertAdjacentHTML("beforeend", photosHTML(it));
  }

  // ─── события ────────────────────────────────────────────────────────────
  document.addEventListener("click", async (e) => {
    const ya = e.target.closest("a[data-ya-form]");
    if (ya) {   // адрес из полей формы — в ссылку, до перехода
      const f = $("agentForm");
      const v = (n) => (f && f[n] ? f[n].value.trim() : "");
      const q = v("street") ? `Краснодар, ${v("street")} ${v("house")}` : v("complex") ? `ЖК ${v("complex")}, Краснодар` : `${v("district")} Краснодар`;
      ya.href = `https://yandex.ru/maps/35/krasnodar/?text=${encodeURIComponent(q.trim())}`;
      return;
    }
    const t = e.target.closest("button");
    if (!t) return;
    if (t.hasAttribute("data-agent")) { closeDlg($("cabinetDlg")); open(); return; }
    if (t.dataset.agentEdit) {
      const id = Number(t.dataset.agentEdit);
      if ($("detail").open) closeDlg($("detail"));
      if (data && dlg.open) { const it = data.items.find((i) => i.id === id); if (it) { renderEdit(it); return; } }
      open(id);
      return;
    }
    if (!dlg.contains(t)) return;
    if (t.hasAttribute("data-agent-new")) { renderEdit({}); return; }
    if (t.hasAttribute("data-agent-back")) { render(); return; }
    if (t.dataset.agentStatus) { setStatus(Number(t.dataset.id), t.dataset.agentStatus); return; }
    if (t.dataset.photoDel) { delPhoto(Number(t.dataset.id), t.dataset.photoDel); return; }
    if (t.dataset.copy) {
      try { await navigator.clipboard.writeText(t.dataset.copy); toast("Скопировано"); } catch { toast(t.dataset.copy); }
    }
  });
  dlg.addEventListener("submit", (e) => {
    e.preventDefault();
    if (e.target.id === "verifyForm") startVerify($("verifyPhone").value);
    else if (e.target.id === "agentForm") save(e.target);
  });
  dlg.addEventListener("change", (e) => {
    const inp = e.target;
    if (inp.dataset && inp.dataset.photoAdd) addPhotos(Number(inp.dataset.photoAdd), [...inp.files]);
  });
  dlg.addEventListener("close", () => clearInterval(waitTimer));
  document.addEventListener("oneplus:agent", () => open());   // ссылка «Кабинет агента» из уведомлений
  void num;
})();
