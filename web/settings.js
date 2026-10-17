/* Настройки аккаунта: вид подборок для клиента (обложка, цвет, подпись, контакт), профиль, порядок блоков кабинета,
   выход на других устройствах. API: /api/settings. Всё сохраняется само через полсекунды после правки. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const ic = (n) => (window.ic ? window.ic(n) : "");
  const PALETTE = { gold: "#e3cb8f", teal: "#5fd0c0", sky: "#7cc4f5", violet: "#b9a3f5", coral: "#f29a7e", rose: "#f08fb6", lime: "#b6db7a", slate: "#9fb3b1" };
  const NAMES = { gold: "Золото", teal: "Бирюза", sky: "Небо", violet: "Сирень", coral: "Коралл", rose: "Роза", lime: "Лайм", slate: "Графит" };
  let st = null;

  async function call(url, body) {
    const opts = { credentials: "same-origin", method: body !== undefined ? "POST" : "GET" };
    if (body !== undefined) { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(body); }
    const r = await fetch(url, opts);
    let d = {};
    try { d = await r.json(); } catch { /* */ }
    return { ok: r.ok, status: r.status, data: d };
  }
  function toast(text, ms = 2200) {
    const t = $("toast");
    t.textContent = text;
    try { t.showPopover(); } catch { t.hidden = false; }
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { try { t.hidePopover(); } catch { t.hidden = true; } }, ms);
  }
  function saved(ok = true) {
    const s = $("stSaved");
    s.textContent = ok ? "Сохранено" : "Не сохранилось — проверьте интернет";
    s.className = `st-saved on${ok ? "" : " bad"}`;
    clearTimeout(saved.t);
    saved.t = setTimeout(() => { s.className = "st-saved"; }, 1600);
  }

  function preview() {
    const s = st.settings;
    const c = PALETTE[s.pick_color] || s.pick_color || PALETTE.gold;
    $("stPreview").style.setProperty("--accent", c);
    $("spBrand").textContent = s.pick_brand || "Подборка объектов";
    $("spName").textContent = s.pick_name || st.name || "Агент";
    $("spCover").style.backgroundImage = s.pick_cover ? `url("${s.pick_cover}")` : "";
    $("spHead").classList.toggle("has-cover", !!s.pick_cover);
    $("stCoverDel").hidden = !s.pick_cover;
    $("stPhotoDel").hidden = !s.pick_photo;
    for (const id of ["stPhotoPrev", "spAva"]) {
      $(id).style.backgroundImage = s.pick_photo ? `url("${s.pick_photo}")` : "";
      $(id).classList.toggle("has", !!s.pick_photo);
    }
    $("stColors").innerHTML = Object.entries(PALETTE).map(([k, hex]) => `<button type="button" role="radio" aria-checked="${k === s.pick_color}"
      class="st-color${k === s.pick_color ? " on" : ""}" style="--c:${hex}" data-color="${k}" title="${NAMES[k]}" aria-label="${NAMES[k]}">${ic("check")}</button>`).join("");
  }
  function fill() {
    const s = st.settings;
    $("stBrand").value = s.pick_brand;
    $("stPickName").value = s.pick_name;
    $("stPickPhone").value = s.pick_phone;
    $("stName").value = st.name;
    $("stEmail").value = st.email || "вход по коду коллег";
    $("stPickUrl").innerHTML = st.pick_domain
      ? `Ссылки на подборки открываются на отдельном адресе <b>${st.pick_url.replace(/^https?:\/\//, "")}</b> — без упоминания сервиса.`
      : "Ссылки на подборки ведут на адрес сервиса. Чтобы клиент не видел его, админ может подключить отдельный нейтральный адрес.";
    $("stSessInfo").textContent = `Вход на устройстве помнится, пока вы заходите хотя бы раз в ${Math.round(st.session_days / 30)} месяцев. Сейчас вы вошли на устройствах: ${st.sessions}.`;
    preview();
  }

  let timer = 0, pending = {};
  function save(patch) {
    Object.assign(st.settings, patch);
    Object.assign(pending, patch);
    if ("name" in patch) st.name = patch.name;
    preview();
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const body = pending; pending = {};
      const r = await call("/api/settings", body);
      if (r.ok) { st = r.data; preview(); }
      saved(r.ok);
    }, 500);
  }

  // фото обложки: уменьшаем в браузере до 1800 px по ширине (быстро грузится у клиента)
  function shrink(file) {
    return new Promise((res, rej) => {
      const img = new Image();
      img.onload = () => {
        const k = Math.min(1, 1800 / img.width);
        const c = document.createElement("canvas");
        c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
        res(c.toDataURL("image/jpeg", 0.85));
        URL.revokeObjectURL(img.src);
      };
      img.onerror = rej;
      img.src = URL.createObjectURL(file);
    });
  }

  document.addEventListener("click", async (e) => {
    const t = e.target.closest("button");
    if (!t) return;
    if (t.dataset.color) { save({ pick_color: t.dataset.color }); return; }
    if (t.id === "stCoverDel") { save({ pick_cover: "" }); return; }
    if (t.id === "stPhotoDel") { save({ pick_photo: "" }); return; }
    if (t.id === "stResetLayout") { save({ cabinet_order: [] }); toast("Порядок блоков кабинета — как было"); return; }
    if (t.id === "stLogoutAll") {
      if (!confirm("Выйти на всех других устройствах? На этом вы останетесь.")) return;
      const r = await call("/api/settings", { action: "logout_all" });
      toast(r.ok ? `Готово: закрыто входов — ${r.data.closed}` : "Не получилось");
      const g = await call("/api/settings"); if (g.ok) { st = g.data; fill(); }
      return;
    }
    if (t.id === "stLogout") { await call("/api/auth/logout", {}); location.href = "/"; }
  });
  $("stCoverFile").addEventListener("change", async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    toast("Загружаем обложку…", 6000);
    try {
      const data = await shrink(f);
      const r = await call("/api/settings", { cover: data });
      if (!r.ok) { toast(r.data.detail || "Не получилось загрузить"); return; }
      st = r.data; preview(); toast("Обложка загружена");
    } catch { toast("Это не похоже на фото"); }
    e.target.value = "";
  });
  $("stPhotoFile").addEventListener("change", async (e) => {   // фото агента — клиент видит его в подборке
    const f = e.target.files[0];
    if (!f) return;
    toast("Загружаем фото…", 6000);
    try {
      const data = await shrink(f);
      const r = await call("/api/settings", { photo: data });
      if (!r.ok) { toast(r.data.detail || "Не получилось загрузить"); return; }
      st = r.data; preview(); toast("Фото загружено");
    } catch { toast("Это не похоже на фото"); }
    e.target.value = "";
  });
  for (const [id, key] of [["stBrand", "pick_brand"], ["stPickName", "pick_name"], ["stPickPhone", "pick_phone"], ["stName", "name"]]) {
    $(id).addEventListener("input", (e) => save({ [key]: e.target.value }));
  }
  // установка приложения: Android/компьютер — системное окно «Установить»; уже открыто как приложение — так и пишем
  const standalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone;
  $("stInstalled").hidden = !standalone;
  const showInstall = () => { $("stInstall").hidden = standalone || !window.__installPrompt; };
  document.addEventListener("oneplus:installable", showInstall);
  showInstall();
  $("stInstall").addEventListener("click", async () => {
    const p = window.__installPrompt;
    if (!p) return;
    p.prompt();
    const r = await p.userChoice;
    window.__installPrompt = null; showInstall();
    if (r.outcome === "accepted") toast("Приложение 1+1 появится на экране телефона");
  });
  document.querySelectorAll("[data-ic]").forEach((el) => { el.outerHTML = ic(el.dataset.ic); });

  // ─── 17.10: подтверждение номера агента — прямо в настройках (тот же порядок, что в «Мои объекты») ───
  const escH = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let verTimer = 0;
  async function verLoad() {
    const r = await call("/api/agent");
    const box = $("stVerBody");
    if (!r.ok) { $("stVerify").hidden = true; return; }
    const phones = r.data.phones || [];
    box.innerHTML = (phones.length ? `<div class="st-ver-ok">${phones.map((p) => `<span class="st-ver-chip">${ic("check-circle")}${escH(p)}</span>`).join("")}</div>
        <p class="note">Номер подтверждён. Можно добавить ещё один (например, рабочий).</p>` : "")
      + `<form class="st-ver-form" id="stVerForm"><input id="stVerPhone" type="tel" inputmode="tel" autocomplete="tel" placeholder="+7 918 123-45-67" aria-label="Ваш номер">
        <button type="submit" class="pill">${phones.length ? "Подтвердить ещё номер" : "Получить код"}</button></form><div id="stVerCode"></div>`;
    $("stVerForm").addEventListener("submit", (e) => { e.preventDefault(); verStart($("stVerPhone").value.trim()); });
  }
  async function verStart(phone) {
    const box = $("stVerCode");
    const r = await call("/api/agent/verify", { phone });
    if (!r.ok) { box.innerHTML = `<p class="st-ver-err">${escH(r.data.detail || "Не получилось.")}</p>`; return; }
    const { code, send_to: to, phone: ph } = r.data, text = `1+1-${code}`;
    box.innerHTML = `<div class="st-ver-code"><p>Отправьте сообщение <b class="code">${escH(text)}</b><br>с номера <b>${escH(ph)}</b> на номер <b>${escH(to)}</b> в MAX.</p>
      <div class="st-acts"><button type="button" class="chip" data-vcopy="${escH(text)}">Скопировать код</button>
        <button type="button" class="chip" data-vcopy="${escH(to)}">Скопировать наш номер</button></div>
      <button type="button" class="pill" id="stVerCheck">Я отправил — проверить</button>
      <p class="note" id="stVerWait">Отправили? Нажмите «Проверить» — посмотрим сразу. Сами проверяем раз в 1–2 минуты.</p></div>`;
    $("stVerCheck").addEventListener("click", async (e) => {
      const b = e.currentTarget; b.disabled = true; b.textContent = "Проверяем…";
      const c = await call("/api/agent/verify", { phone: ph, check: true });
      b.disabled = false; b.textContent = "Проверить ещё раз";
      if (c.ok && c.data.status === "ok") { clearInterval(verTimer); toast("Номер подтверждён"); verLoad(); return; }
      $("stVerWait").textContent = c.data.detail || "Пока не пришло — попробуйте через минуту.";
    });
    clearInterval(verTimer);
    let tries = 0;
    verTimer = setInterval(async () => {
      if (++tries > 360) { clearInterval(verTimer); return; }
      const s = await call(`/api/agent/verify?phone=${encodeURIComponent(ph)}`);
      if (s.ok && s.data.status === "ok") { clearInterval(verTimer); toast("Номер подтверждён"); verLoad(); }
    }, 5000);
  }
  document.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-vcopy]");
    if (!b) return;
    try { await navigator.clipboard.writeText(b.dataset.vcopy); toast("Скопировано"); } catch { prompt("Скопируйте:", b.dataset.vcopy); }
  });

  (async () => {
    const r = await call("/api/settings");
    if (r.status === 401) { $("stGuest").hidden = false; return; }
    st = r.data;
    $("stApp").hidden = false;
    fill();
    verLoad();
  })();
})();
