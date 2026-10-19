/* «Мои объекты» — отдельные страницы вместо окна:
   /my       — подтверждение номера, список своих объявлений (из чатов и добавленных), снять/вернуть;
   /my/edit  — новый объект или правка (?id=): по шагам, живая карточка «как увидят», фото перетаскиванием.
   API: /api/agent, /api/agent/verify, /api/agent/listings[/{id}[/photos]], /api/meta, /api/facets. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const ic = (n) => (window.ic ? window.ic(n) : "");
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const main = $("myMain");
  const TYPES = { flat: "Квартира", room: "Комната", house: "Дом", land: "Участок", commercial: "Коммерция" };
  const ROOMS = [[0, "Студия"], [1, "1"], [2, "2"], [3, "3"], [4, "4+"]];
  let data = null, meta = {}, complexes = [], tab = "all", waitTimer = 0;

  async function call(url, body, method) {
    const opts = { credentials: "same-origin", method: method || (body !== undefined ? "POST" : "GET") };
    if (body !== undefined) { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(body); }
    let r = await fetch(url, opts);
    for (let i = 0; i < 2 && r.status === 503; i++) { await new Promise((res) => setTimeout(res, 1200)); r = await fetch(url, opts); }
    let d = {};
    try { d = await r.json(); } catch { /* */ }
    return { ok: r.ok, status: r.status, data: d };
  }
  function toast(text, ms = 2400) {
    const t = $("toast");
    t.textContent = text;
    try { t.showPopover(); } catch { t.hidden = false; }
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { try { t.hidePopover(); } catch { t.hidden = true; } }, ms);
  }
  const money = (p, deal) => (!p ? "цена не указана" : deal === "rent" ? `${Math.round(p).toLocaleString("ru-RU")} ₽/мес`
    : `${(p / 1e6).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} млн ₽`);
  const fmtDay = (ts) => new Date(ts * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
  const head = (o) => (o.complex ? `ЖК ${o.complex}` : o.district || (o.street ? `ул. ${o.street}` : o.title));
  const place = (o) => [o.district, o.street && `ул. ${o.street}${o.house ? ", " + o.house : ""}`].filter(Boolean).join(" · ");

  // ════════════ /my — список ════════════
  function verifyBlock() {
    const phones = data.phones;
    const form = `<div class="pv" id="verifyMount"></div>`;   // 17.10: звонок — основной способ, код в MAX — запасной (verify.js)
    if (phones.length) {
      return `<section class="my-card my-phones"><div class="my-card-h"><span class="my-ic ok">${ic("check-circle")}</span>
        <div><h2>Номер подтверждён</h2><p>Объявления с ${phones.length > 1 ? "этими номерами" : "этим номером"} — ваши: менять их можете только вы.</p></div></div>
        <div class="my-chips">${phones.map((p) => `<span class="my-chip">${ic("phone")}${esc(p)}</span>`).join("")}</div>
        <details class="my-more"><summary>Добавить ещё номер</summary>${form}</details></section>`;
    }
    return `<section class="my-card my-step"><div class="my-card-h"><span class="my-ic">${ic("shield")}</span>
      <div><h2>Шаг 1 · подтвердите свой номер</h2><p>Так мы поймём, какие объявления из чатов — ваши. Никто другой не сможет их изменить.
        ${(data.verify_methods || []).includes("call") ? "Проще всего — позвонить с этого номера: звонок бесплатный и сбросится сам." : "Подтверждение — сообщением с кодом в MAX."}</p></div></div>${form}</section>`;
  }
  // 19.10: статистика объекта за 30 дней — просмотры, номер, избранное, подборки
  function statsHTML(o) {
    const s = o.stats;
    if (!s || o.is_active === 0) return "";
    return `<div class="my-stats" title="За последние 30 дней">
      <span><b>${s.views}</b>просмотров${s.views7 ? ` · ${s.views7} за неделю` : ""}</span>
      <span><b>${s.phone}</b>открыли номер</span>
      <span><b>${s.fav}</b>в избранном</span>
      <span><b>${s.picks}</b>в подборках клиентам</span>
    </div>`;
  }
  function itemCard(o) {
    const sold = !o.is_active, own = o.source === "own", site = o.source === "feed";
    const photo = (o.photos || [])[0];
    return `<article class="wk-card my-item${sold ? " gone" : ""} c-${sold ? "slate" : own ? "gold" : "teal"}">
      <a class="wk-photo" href="${o.can_edit ? `/my/edit?id=${o.id}` : `/?open=${o.id}`}">${photo ? `<img src="${esc(photo)}" alt="" loading="lazy">` : `<span>${ic("plus")}<small>${o.can_edit ? "Добавить фото" : "Фото нет"}</small></span>`}
        <em class="my-src${site ? " site" : ""}">${own ? "Добавлен вами" : site ? "С сайта" : "Из чатов"}</em>${sold ? `<em class="wk-gone">Снят с сайта</em>` : ""}</a>
      <div class="wk-body">
        <a class="wk-title" href="/?open=${o.id}">${esc(head(o))}</a>
        <b class="wk-price">${esc(money(o.price, o.deal))}</b>
        <p class="wk-what">${esc(o.title)}${place(o) ? ` · ${esc(place(o))}` : ""}</p>
        <p class="my-meta">${ic("clock")}${sold ? "снят" : own && o.expires_at ? `на сайте до ${fmtDay(o.expires_at)}` : "на сайте"} · фото ${(o.photos || []).length}</p>
        ${statsHTML(o)}
        <div class="wk-foot">
          ${o.can_edit ? `<a class="pill my-edit" href="/my/edit?id=${o.id}">${ic("edit")}Изменить</a>
            <button type="button" class="wk-btn" data-status="${sold ? "active" : "sold"}" data-id="${o.id}">${sold ? "Вернуть на сайт" : "Продано · снять"}</button>`
          : site ? `<span class="my-note">Объект с сайта arrowsrealty.ru — цена, фото и описание меняются на сайте (Тильда)</span>`
          : `<span class="my-note">Этой карточкой управляет другой агент</span>`}
        </div>
      </div></article>`;
  }
  function renderList() {
    clearInterval(waitTimer);
    const items = data.items || [];
    const F = { all: () => true, on: (o) => o.is_active, off: (o) => !o.is_active, site: (o) => o.source === "feed",
      chat: (o) => o.source === "chat", own: (o) => o.source === "own" };
    const n = Object.fromEntries(Object.keys(F).map((k) => [k, items.filter(F[k]).length]));
    const shown = items.filter(F[tab] || F.all);
    main.innerHTML = `
      <header class="my-head"><div><h1>Мои объекты</h1><p>Всё с вашим номером: объекты с вашего сайта, объявления из чатов и добавленные вами — без повторов.</p></div>
        ${data.phones.length ? (data.access ? `<a class="pill my-add" href="/my/edit">${ic("plus")}Добавить объект</a>`
          : `<a class="pill light" href="/pay">Добавлять — с подпиской</a>`) : ""}</header>
      ${verifyBlock()}
      ${data.phones.length || items.length ? `
        <nav class="wk-tabs my-tabs">${[["all", "Все"], ["site", "С сайта"], ["chat", "Из чатов"], ["own", "Добавлены вами"], ["on", "Активные"], ["off", "Снятые"]]
          .filter(([k]) => k === "all" || n[k]).map(([k, t]) =>
          `<button type="button" class="wk-tab${k === tab ? " on" : ""}" data-tab="${k}">${t}<b>${n[k]}</b></button>`).join("")}</nav>
        <div class="wk-grid my-grid">${shown.map(itemCard).join("") || `<div class="wk-empty">${ic("home")}<b>${items.length ? "Здесь пусто" : "Пока нет объявлений"}</b>
          ${items.length ? "" : "Как только вы разместите объект в чатах с подтверждённым номером — он появится здесь. Или добавьте объект сами."}</div>`}</div>` : ""}
      <p class="my-rules">${ic("help")}Менять можно только объявления с вашим подтверждённым номером. Свой объект висит ${data.own_days} дней — за 10, 5, 2 и 1 день
        до конца спросим «ещё актуален?» (уведомление на сайте и письмо): «да» — продлим, «нет» — снимем сразу, без ответа — снимем по сроку. Все правки сохраняются в журнале.</p>`;
    if (window.PhoneVerify && $("verifyMount")) PhoneVerify.mount($("verifyMount"), { methods: data.verify_methods, again: data.phones.length > 0,
      onDone: () => { toast("Номер подтверждён"); load(); } });
  }

  // ════════════ /my/edit — новый объект или правка ════════════
  let o = null, own = false, isNew = false;
  function priceValue(x) {
    if (!x.price) return "";
    return x.deal !== "rent" ? String(+(x.price / 1e6).toFixed(3)).replace(".", ",") : String(x.price);
  }
  const fieldHTML = (name, label, value, attrs = "", hint = "") =>
    `<label class="my-f"><span>${esc(label)}</span><input name="${name}" value="${esc(value ?? "")}" ${attrs}>${hint ? `<small>${hint}</small>` : ""}</label>`;
  function renderEdit() {
    const t = o.type || "flat", deal = o.deal || "sale";
    main.innerHTML = `
      <header class="my-head"><div><a class="my-back" href="/my">${ic("chevron-left")}Мои объекты</a>
        <h1>${isNew ? "Новый объект" : "Изменить объявление"}</h1>
        <p>${isNew ? "Заполните по шагам — справа видно, как объект увидят коллеги в поиске." : own ? esc(o.title) : "Объявление из чатов: можно поменять цену, описание и фото — остальное берётся из сообщения."}</p></div></header>
      <div class="my-edit">
        <form id="myForm" class="my-form" autocomplete="off">
          ${own ? `
          <section class="my-sec"><h3><b>1</b>Что за объект</h3>
            <div class="my-seg" data-group="type">${Object.entries(TYPES).map(([k, v]) => `<button type="button" class="${k === t ? "on" : ""}" data-v="${k}">${v}</button>`).join("")}</div>
            <div class="my-seg sm" data-group="deal"><button type="button" class="${deal === "sale" ? "on" : ""}" data-v="sale">Продажа</button><button type="button" class="${deal === "rent" ? "on" : ""}" data-v="rent">Аренда</button></div>
            <input type="hidden" name="type" value="${t}"><input type="hidden" name="deal" value="${deal}">
          </section>
          <section class="my-sec"><h3><b>2</b>Параметры</h3>
            <div class="my-f" data-show="flat room house commercial"><span>Комнат</span><div class="my-seg sm" data-group="rooms">${ROOMS.map(([k, v]) =>
              `<button type="button" class="${Number(o.rooms ?? -1) === k || (k === 4 && o.rooms > 4) ? "on" : ""}" data-v="${k}">${v}</button>`).join("")}</div>
              <input type="hidden" name="rooms" value="${o.rooms ?? ""}"></div>
            <div class="my-grid2">
              ${fieldHTML("area", "Площадь, м²", o.area, 'inputmode="decimal" placeholder="например 54,5"')}
              <div data-show="house land">${fieldHTML("land", "Участок, соток", o.land, 'inputmode="decimal" placeholder="например 6"')}</div>
              <div data-show="flat room commercial">${fieldHTML("floor", "Этаж", o.floor, 'inputmode="numeric"')}</div>
              <div data-show="flat room commercial house">${fieldHTML("floors", "Этажей в доме", o.floors, 'inputmode="numeric"')}</div>
            </div>
          </section>
          <section class="my-sec"><h3><b>3</b>Где</h3>
            <p class="my-hint">Нужно хотя бы одно: район, ЖК или улица — иначе объект не найдут в поиске и на карте.</p>
            <div class="my-grid2">
              ${fieldHTML("district", "Район", o.district, 'placeholder="начните вводить: ФМР, Юбилейный…"')}
              ${fieldHTML("complex", "ЖК", o.complex, 'placeholder="если есть"')}
              ${fieldHTML("street", "Улица", o.street, 'placeholder="например Красная"')}
              ${fieldHTML("house", "Дом", o.house, 'placeholder="например 176/2"')}
            </div>
            <a class="my-ya" data-ya href="https://yandex.ru/maps/35/krasnodar/" target="_blank" rel="noopener">${ic("search")}Проверить адрес в Яндекс Картах ↗</a>
          </section>` : ""}
          <section class="my-sec"><h3><b>${own ? 4 : 1}</b>Цена</h3>
            <div class="my-price"><input name="price" value="${esc(priceValue(o))}" inputmode="decimal" required placeholder="${deal === "rent" ? "35000" : "6,5"}" aria-label="Цена">
              <span id="myUnit">${deal === "rent" ? "₽ в месяц" : "млн ₽"}</span></div>
            <p class="my-hint" id="myPriceHint"></p>
          </section>
          <section class="my-sec"><h3><b>${own ? 5 : 2}</b>Описание</h3>
            <textarea name="description" rows="7" maxlength="3000" placeholder="Что важно: ремонт, мебель, документы, ипотека, обременения… Номер телефона писать не нужно — коллеги увидят ваш подтверждённый номер.">${esc(o.description || "")}</textarea>
            <div class="my-ai-row"><p class="my-hint"><span id="myLen">0</span> из 3000</p>
              <button type="button" class="my-ai" id="myAi">${ic("sparkle")}Написать красиво</button></div>
            <div class="my-ai-box" id="myAiBox" hidden></div>
          </section>
          <section class="my-sec"><h3><b>${own ? 6 : 3}</b>Фото</h3>${isNew ? `<p class="my-hint">Фото можно будет добавить сразу после публикации — мы откроем этот же объект.</p>` : photosHTML()}</section>
          <p class="err" id="myErr" hidden></p>
        </form>
        <aside class="my-side">
          <div class="my-prev" id="myPrev"></div>
          <ul class="my-check" id="myCheck"></ul>
          <button type="submit" form="myForm" class="pill my-save">${isNew ? "Опубликовать" : "Сохранить"}</button>
          ${!isNew ? `<button type="button" class="chip my-status" data-status="${o.is_active ? "sold" : "active"}" data-id="${o.id}">${o.is_active ? "Продано · снять с сайта" : "Вернуть на сайт"}</button>` : ""}
        </aside>
      </div>`;
    bindEdit();
  }
  function photosHTML() {
    const list = (o.photos || []).map((u, i) => `<div class="my-ph"><img src="${esc(u)}" alt="">${i === 0 ? `<em>Обложка</em>` : ""}
      <button type="button" data-photo-del="${esc(u)}" aria-label="Удалить фото">${ic("x")}</button></div>`).join("");
    return `<div class="my-drop" id="myDrop"><div class="my-phs">${list}
        <label class="my-ph add"><input type="file" accept="image/*" multiple hidden id="myFiles">${ic("plus")}<span>Добавить</span></label></div>
      <p class="my-hint">${(o.photos || []).length} из ${data.max_photos} · можно перетащить фото сюда с компьютера. Первое — обложка.</p></div>`;
  }
  function formVals() {
    const f = $("myForm");
    return Object.fromEntries(new FormData(f).entries());
  }
  function updatePreview() {
    const v = formVals();
    const deal = v.deal || o.deal || "sale";
    const raw = String(v.price || "").replace(/\s/g, "").replace(",", ".");
    const n = Number(raw);
    let price = 0;
    if (n > 0) price = deal === "rent" ? n : n < 1000 ? n * 1e6 : n;
    $("myPriceHint").textContent = price ? `= ${Math.round(price).toLocaleString("ru-RU")} ₽${deal === "rent" ? " в месяц" : ""}` : (deal === "rent" ? "Например: 35000" : "В миллионах: 6,5 = 6 500 000 ₽");
    $("myUnit").textContent = deal === "rent" ? "₽ в месяц" : "млн ₽";
    const desc = $("myForm").description.value;
    $("myLen").textContent = desc.length;
    const x = { ...o, ...(own ? v : {}), price, deal, rooms: v.rooms === "" ? null : Number(v.rooms ?? o.rooms) };
    const rooms = x.rooms === 0 ? "Студия" : x.rooms ? `${x.rooms}-комн.` : "";
    const what = own ? [TYPES[x.type] || "", rooms, v.area && `${v.area} м²`, v.land && `${v.land} сот.`, v.floor && `${v.floor}${v.floors ? "/" + v.floors : ""} эт.`].filter(Boolean).join(", ") : o.title;
    const photo = (o.photos || [])[0];
    $("myPrev").innerHTML = `<p class="my-prev-l">Так увидят коллеги в поиске</p>
      <div class="my-prev-card"><div class="my-prev-ph">${photo ? `<img src="${esc(photo)}" alt="">` : `<span class="ph-none"><img src="/static/favicon.svg" alt=""><small>Фото у агента</small></span>`}</div>
        <div class="my-prev-b"><small>${esc(head(x) || "Район / ЖК / улица")}</small><b>${price ? esc(money(price, deal)) : "Цена"}</b>
          <span>${esc(what || "Параметры")}</span><em>${esc(place(x) || "")}</em></div></div>`;
    const has = (k) => String(v[k] || "").trim();
    const checks = own ? [
      [!!price, "Цена"], [has("district") || has("complex") || has("street"), "Район, ЖК или улица"],
      [has("area"), "Площадь"], [desc.trim().length > 40, "Описание (хотя бы пара предложений)"], [(o.photos || []).length > 0, "Фото"],
    ] : [[!!price, "Цена"], [desc.trim().length > 40, "Описание"], [(o.photos || []).length > 0, "Фото"]];
    $("myCheck").innerHTML = checks.map(([ok, t]) => `<li class="${ok ? "ok" : ""}">${ic(ok ? "check-circle" : "x-circle")}${t}</li>`).join("");
  }
  function showFor(t) {
    document.querySelectorAll("[data-show]").forEach((el) => { el.hidden = !el.dataset.show.split(" ").includes(t); });
  }
  function bindEdit() {
    const f = $("myForm");
    if (own) {
      showFor(f.type.value);
      if (window.Combo) {
        Combo(f.district, { options: () => (meta.districts || []).map((d) => ({ value: d })), onPick: updatePreview });
        Combo(f.complex, { options: () => complexes.map((c) => ({ value: c.name, meta: `${c.n}` })), onPick: updatePreview });
      }
    }
    f.addEventListener("input", updatePreview);
    f.addEventListener("submit", (e) => { e.preventDefault(); save(); });
    const drop = $("myDrop");
    if (drop) {
      ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
      ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
      drop.addEventListener("drop", (e) => addPhotos([...e.dataTransfer.files].filter((x) => x.type.startsWith("image/"))));
      $("myFiles").addEventListener("change", (e) => { addPhotos([...e.target.files]); e.target.value = ""; });
    }
    updatePreview();
  }
  async function writeNice() {   // «Написать красиво»: нейросеть пишет продающий текст по параметрам
    const btn = $("myAi"), box = $("myAiBox");
    btn.disabled = true; btn.classList.add("busy");
    box.hidden = false;
    box.innerHTML = `<div class="my-ai-wait"><i></i><i></i><i></i><span>Пишем текст по вашим параметрам…</span></div>`;
    const v = formVals();
    const r = await call("/api/agent/describe", { ...(own ? v : { type: o.type, deal: o.deal, rooms: o.rooms, area: o.area, land: o.land,
      floor: o.floor, floors: o.floors, district: o.district, complex: o.complex, street: o.street, house: o.house }),
      price: v.price, description: $("myForm").description.value, photos: (o.photos || []).length });
    btn.disabled = false; btn.classList.remove("busy");
    if (!r.ok) { box.innerHTML = `<p class="err">${esc(r.data.detail || "Не получилось.")}</p>`; return; }
    box.innerHTML = `<p class="my-ai-l">${ic("sparkle")}Вариант от нейросети — проверьте факты перед публикацией</p>
      <div class="my-ai-text">${esc(r.data.text)}</div>
      <div class="my-ai-acts"><button type="button" class="pill" data-ai-use>Вставить в описание</button>
        <button type="button" class="pill light" data-ai-again>Другой вариант</button>
        <button type="button" class="chip" data-ai-close>Закрыть</button></div>`;
    box.dataset.text = r.data.text;
  }
  async function save() {
    const err = $("myErr");
    err.hidden = true;
    const fd = formVals();
    if (!own) for (const k of Object.keys(fd)) if (!["price", "description"].includes(k)) delete fd[k];
    const btn = document.querySelector(".my-save");
    btn.disabled = true; btn.textContent = "Сохраняем…";
    const r = await call(isNew ? "/api/agent/listings" : `/api/agent/listings/${o.id}`, fd);
    btn.disabled = false; btn.textContent = isNew ? "Опубликовать" : "Сохранить";
    if (!r.ok) { err.textContent = r.data.detail || "Не получилось сохранить."; err.hidden = false; err.scrollIntoView({ block: "center", behavior: "smooth" }); return; }
    data.items = r.data.items;
    if (isNew) {   // сразу к фото
      toast("Объект опубликован — добавьте фото");
      history.replaceState(null, "", `/my/edit?id=${r.data.id}`);
      openEdit(r.data.id);
    } else {
      toast("Сохранено");
      o = data.items.find((x) => x.id === o.id) || o;
      updatePreview();
    }
  }
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
  async function addPhotos(files) {
    for (const [i, f] of files.entries()) {
      toast(`Загружаем фото ${i + 1} из ${files.length}…`, 8000);
      let url;
      try { url = await shrink(f); } catch { toast("Этот файл не похож на фото"); continue; }
      const r = await call(`/api/agent/listings/${o.id}/photos`, { data: url });
      if (!r.ok) { toast(r.data.detail || "Фото не загрузилось"); break; }
      o.photos = r.data.photos;
    }
    toast("Фото сохранены");
    refreshPhotos();
  }
  function refreshPhotos() {
    const sec = $("myDrop").parentElement;
    sec.innerHTML = `<h3><b>${own ? 6 : 3}</b>Фото</h3>${photosHTML()}`;
    bindEdit();
  }

  async function setStatus(id, status) {
    if (status === "sold" && !confirm("Снять объект с сайта? Его можно будет вернуть.")) return;
    const r = await call(`/api/agent/listings/${id}`, { status });
    if (!r.ok) { toast(r.data.detail || "Не получилось."); return; }
    data.items = r.data.items;
    toast(status === "sold" ? "Объект снят с сайта" : "Объект снова на сайте");
    if (location.pathname === "/my") renderList(); else openEdit(id);
  }
  function openEdit(id) {
    isNew = !id;
    o = isNew ? { type: "flat", deal: "sale", photos: [] } : data.items.find((x) => x.id === id);
    if (!o) { main.innerHTML = `<div class="wk-empty">${ic("alert")}<b>Объект не найден</b>Возможно, это не ваше объявление. <a class="pill" href="/my">К моим объектам</a></div>`; return; }
    if (!isNew && !o.can_edit) { main.innerHTML = `<div class="wk-empty">${ic("ban")}<b>Этот объект менять нельзя</b>Им управляет другой агент. <a class="pill" href="/my">К моим объектам</a></div>`; return; }
    own = isNew || o.source === "own";
    renderEdit();
  }

  // ─── нажатия ───
  document.addEventListener("click", async (e) => {
    const ya = e.target.closest("a[data-ya]");
    if (ya) {
      const f = $("myForm"), v = (n) => (f && f[n] ? f[n].value.trim() : "");
      const q = v("street") ? `Краснодар, ${v("street")} ${v("house")}` : v("complex") ? `ЖК ${v("complex")}, Краснодар` : `${v("district")} Краснодар`;
      ya.href = `https://yandex.ru/maps/35/krasnodar/?text=${encodeURIComponent(q.trim())}`;
      return;
    }
    const t = e.target.closest("button");
    if (!t) return;
    if (t.id === "myAi" || t.hasAttribute("data-ai-again")) { writeNice(); return; }
    if (t.hasAttribute("data-ai-use")) {
      const ta = $("myForm").description;
      ta.value = $("myAiBox").dataset.text || ta.value;
      ta.dispatchEvent(new Event("input", { bubbles: true }));
      $("myAiBox").hidden = true;
      toast("Текст вставлен — не забудьте сохранить");
      return;
    }
    if (t.hasAttribute("data-ai-close")) { $("myAiBox").hidden = true; return; }
    if (t.dataset.tab) { tab = t.dataset.tab; renderList(); return; }
    if (t.dataset.status) { setStatus(Number(t.dataset.id), t.dataset.status); return; }
    if (t.dataset.copy) {
      try { await navigator.clipboard.writeText(t.dataset.copy); toast("Скопировано"); } catch { toast(t.dataset.copy, 5000); }
      return;
    }
    if (t.dataset.photoDel) {
      if (!confirm("Удалить это фото?")) return;
      const r = await call(`/api/agent/listings/${o.id}/photos`, { url: t.dataset.photoDel }, "DELETE");
      if (!r.ok) { toast(r.data.detail || "Не получилось."); return; }
      o.photos = r.data.photos; refreshPhotos(); return;
    }
    const seg = t.closest(".my-seg");
    if (seg && t.dataset.v !== undefined) {   // выбор кнопками: тип, сделка, комнаты
      const f = $("myForm"), g = seg.dataset.group;
      const again = g === "rooms" && t.classList.contains("on");
      seg.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b === t && !again));
      f[g].value = again ? "" : t.dataset.v;
      if (g === "type") showFor(t.dataset.v);
      updatePreview();
    }
  });

  async function load() {
    const [r, m] = await Promise.all([call("/api/agent"), call("/api/meta")]);
    if (r.status === 401) {
      main.innerHTML = `<div class="wk-empty">${ic("user")}<b>Войдите, чтобы управлять своими объектами</b><a class="pill" href="/?login=1">Войти</a></div>`;
      return;
    }
    if (!r.ok) { main.innerHTML = `<div class="wk-empty">${ic("alert")}<b>Не получилось загрузить</b>${esc(r.data.detail || "Обновите страницу.")}</div>`; return; }
    data = r.data;
    meta = m.data || {};
    if (location.pathname === "/my/edit") {
      const id = Number(new URLSearchParams(location.search).get("id")) || 0;
      if (!id && !(data.phones.length && data.access)) { location.replace("/my"); return; }
      openEdit(id);
      call("/api/facets?deal=sale").then((f) => { complexes = (f.ok && f.data.complexes) || []; });
    } else renderList();
  }
  load();
})();
