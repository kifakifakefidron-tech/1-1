/* 1+1 · «Объяснить адрес» (16.11, Артём: «что сервис не понял — чтобы я объяснял»).
   Одно окно для /learn («Сервис не понял») и /fix (выделенные слова в тексте объявления).
   Админ пишет, что значат слова: район / ЖК / улица и дом / посёлок / точка на карте / «не адрес» →
   POST /api/admin/place-phrase → правило для ВСЕХ объявлений с этими словами (сейчас — только пустые поля, и потом).
   window.ExplainAddr.open({ phrase, onDone(res) }) */
(() => {
  "use strict";
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let dlg = null, districts = null, onDone = null;

  async function call(url, body) {
    const opts = { credentials: "same-origin", method: body ? "POST" : "GET" };
    if (body) { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(body); }
    let r = await fetch(url, opts);
    for (let i = 0; i < 3 && r.status === 503; i++) {
      await new Promise((res) => setTimeout(res, 1500 * (i + 1)));
      r = await fetch(url, opts);
    }
    let d = {};
    try { d = await r.json(); } catch { /* */ }
    return { ok: r.ok, data: d };
  }
  // «45.0355, 38.9753», ссылка Яндекс / Google Карт
  function coords(text) {
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
    return a > 44.5 && a < 45.6 && b > 38.3 && b < 39.8 ? [a, b] : null;
  }

  const KINDS = [["district", "Район"], ["complex", "ЖК"], ["street", "Улица и дом"], ["settlement", "Посёлок / станица"],
    ["point", "Точка на карте"], ["none", "Не адрес"]];

  function build() {
    dlg = document.createElement("dialog");
    dlg.className = "small-dlg ex-dlg";
    dlg.innerHTML = `<form method="dialog" class="ex-form">
      <h2>Объяснить адрес</h2>
      <p>Что значат эти слова? Ответ запомнится: все объявления с ними — и уже собранные, и новые — получат это место
        (только там, где поле пустое; ваши правки не трогаются).</p>
      <label class="ex-l">Слова из объявления<input id="exPhrase" maxlength="80" autocomplete="off"
        placeholder="например: за Красной площадью"></label>
      <small class="ex-tip">Оставьте только сами слова о месте — без цены, этажа и номеров телефонов.</small>
      <div class="ex-kinds" role="radiogroup" aria-label="Что это">${KINDS.map(([k, t], i) =>
        `<label class="ex-k"><input type="radio" name="exKind" value="${k}"${i ? "" : " checked"}><span>${t}</span></label>`).join("")}</div>
      <div class="ex-f" data-for="district"><label class="ex-l">Район<select id="exDistrict"></select></label></div>
      <div class="ex-f" data-for="complex" hidden><label class="ex-l">ЖК<input id="exComplex" maxlength="80" autocomplete="off" placeholder="Название ЖК"></label>
        <label class="ex-l">Район <small>(если знаете)</small><select id="exDistrict2"></select></label></div>
      <div class="ex-f ex-row" data-for="street" hidden><label class="ex-l">Улица<input id="exStreet" maxlength="80" autocomplete="off" placeholder="Ставропольская"></label>
        <label class="ex-l ex-h">Дом<input id="exHouse" maxlength="20" autocomplete="off" placeholder="107/1"></label></div>
      <div class="ex-f" data-for="settlement" hidden><label class="ex-l">Посёлок / станица<input id="exSettlement" maxlength="80" autocomplete="off" placeholder="ст. Елизаветинская"></label></div>
      <div class="ex-f" data-for="point" hidden><label class="ex-l">Координаты или ссылка Яндекс / Google Карт
        <input id="exCoords" autocomplete="off" placeholder="45.0355, 38.9753"></label>
        <label class="ex-l">Район <small>(если знаете)</small><select id="exDistrict3"></select></label></div>
      <div class="ex-f" data-for="none" hidden><p class="note">Сервис перестанет показывать эти слова в «Сервис не понял».</p></div>
      <p class="ex-msg" id="exMsg" hidden></p>
      <div class="dlg-actions"><button type="button" class="pill light" id="exCancel">Отмена</button>
        <button type="submit" class="pill" id="exSave">Запомнить</button></div>
    </form>`;
    document.body.appendChild(dlg);
    dlg.addEventListener("change", (e) => { if (e.target.name === "exKind") kind(); });
    dlg.querySelector("#exCancel").addEventListener("click", () => dlg.close());
    dlg.querySelector("form").addEventListener("submit", (e) => { e.preventDefault(); save(); });
  }
  function kind() {
    const k = dlg.querySelector("[name=exKind]:checked").value;
    dlg.querySelectorAll(".ex-f").forEach((f) => { f.hidden = f.dataset.for !== k; });
    return k;
  }
  async function fillDistricts() {
    if (!districts) {
      const r = await call("/api/admin/districts");
      districts = (r.data.districts || []).slice().sort((a, b) => a.localeCompare(b, "ru"));
    }
    for (const id of ["exDistrict", "exDistrict2", "exDistrict3"]) {
      const sel = dlg.querySelector("#" + id);
      sel.innerHTML = `<option value="">${id === "exDistrict" ? "Выберите район" : "—"}</option>`
        + districts.map((d) => `<option>${esc(d)}</option>`).join("");
      if (window.Pick && window.Pick.select) window.Pick.select(sel, { search: true });
    }
  }
  function msg(t) { const m = dlg.querySelector("#exMsg"); m.hidden = !t; m.textContent = t || ""; }

  async function save() {
    const v = (id) => dlg.querySelector("#" + id).value.trim();
    const k = kind(), body = { phrase: v("exPhrase") };
    if (k === "district") { body.district = v("exDistrict"); if (!body.district) return msg("Выберите район"); }
    if (k === "complex") { body.complex = v("exComplex"); body.district = v("exDistrict2"); if (!body.complex) return msg("Напишите ЖК"); }
    if (k === "street") { body.street = v("exStreet"); body.house = v("exHouse"); if (!body.street) return msg("Напишите улицу"); }
    if (k === "settlement") { body.settlement = v("exSettlement"); if (!body.settlement) return msg("Напишите посёлок"); }
    if (k === "point") {
      const c = coords(v("exCoords"));
      if (!c) return msg("Не понял координаты. Пример: 45.0355, 38.9753 — или ссылка из Яндекс Карт");
      [body.lat, body.lon] = c;
      body.district = v("exDistrict3");
    }
    if (k === "none") body.none = true;
    const btn = dlg.querySelector("#exSave");
    btn.disabled = true;
    const r = await call("/api/admin/place-phrase", body);
    btn.disabled = false;
    if (!r.ok) return msg(r.data.detail || "Не получилось");
    dlg.close();
    if (onDone) onDone(r.data);
  }

  window.ExplainAddr = {
    async open(opts = {}) {
      if (!dlg) build();
      onDone = opts.onDone || null;
      msg("");
      dlg.querySelector("#exPhrase").value = String(opts.phrase || "").trim().slice(0, 80);
      for (const id of ["exComplex", "exStreet", "exHouse", "exSettlement", "exCoords"]) dlg.querySelector("#" + id).value = "";
      dlg.querySelector("[name=exKind][value=district]").checked = true;
      kind();
      if (!dlg.open) dlg.showModal();
      await fillDistricts();
    },
  };
})();
