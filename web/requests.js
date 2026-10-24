/* Запросы покупателей (/requests) — всем с подпиской. 19.10 (Артём): просто лента от свежих — без фильтров, без «под мои
   объекты», «подходящих объектов» и «сохранить как клиента». «Новый» с прошлого захода, у запроса — звонок/MAX, админу —
   «Удалить». ?id=N (из уведомления «Под ваш объект есть покупатель») — этот запрос первым и подсвечен.
   API: GET /api/requests[?page=&id=], POST {id, delete}. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const ic = (n) => (window.ic ? window.ic(n) : "");
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const TYPES = { flat: "Квартира", room: "Комната", house: "Дом", land: "Участок", commercial: "Коммерция" };
  const pin = Number(new URLSearchParams(location.search).get("id")) || 0;
  let page = 1, admin = false;
  let lastVisit = 0;
  try { lastVisit = Number(localStorage.getItem("rqVisit") || 0); localStorage.setItem("rqVisit", String(Math.floor(Date.now() / 1000))); } catch { /* */ }

  const money = (p, deal) => (deal === "rent" ? `${Math.round(p / 1000).toLocaleString("ru-RU")} тыс. ₽/мес`
    : `${(p / 1e6).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} млн ₽`);
  const roomsText = (mask) => {
    const r = [0, 1, 2, 3, 4].filter((i) => mask & (1 << i)).map((i) => (i === 0 ? "студия" : i === 4 ? "4+" : `${i}`));
    return r.length ? (r.length === 1 && r[0] === "студия" ? "студия" : `${r.join(", ")} комн.`) : "";
  };
  const ago = (ts) => {
    const h = Math.round((Date.now() / 1000 - ts) / 3600);
    return h < 1 ? "только что" : h < 24 ? `${h} ч назад` : h < 48 ? "вчера" : new Date(ts * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
  };
  const pretty = (p) => { const d = String(p).replace(/\D/g, ""); return d.length === 11 ? `+7 ${d.slice(1, 4)} ${d.slice(4, 7)}-${d.slice(7, 9)}-${d.slice(9)}` : p; };
  function toast(text, ms = 2600) {
    const t = $("toast");
    t.textContent = text;
    try { t.showPopover(); } catch { t.hidden = false; }
    clearTimeout(toast.t);
    toast.t = setTimeout(() => { try { t.hidePopover(); } catch { t.hidden = true; } }, ms);
  }
  // 24.10c (Артём: «убрать цену вверху»): бюджет — отметкой рядом с комнатами и районами, а не крупно сверху
  const budget = (r) => {
    const p = r.price_max || r.price_min;
    if (!p) return "";
    const f = (x) => (r.deal === "rent" ? `${Math.round(x / 1000).toLocaleString("ru-RU")} тыс.` : `${(x / 1e6).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} млн`);
    const unit = r.deal === "rent" ? " ₽/мес" : " ₽";
    if (r.price_min && r.price_max && r.price_min !== r.price_max) return `${f(r.price_min)}–${f(r.price_max)}${unit}`;
    return `${r.price_max ? "до" : "от"} ${f(p)}${unit}`;
  };
  function itemHTML(r, i) {
    const chips = [r.type && TYPES[r.type], roomsText(r.rooms_mask), ...r.districts, ...r.complexes.map((c) => `ЖК ${c}`)].filter(Boolean);
    const bud = budget(r);
    const fresh = lastVisit && r.first_seen > lastVisit;
    const phones = (r.phones || []).map((p, k) => `<div class="rq2-ph"><a class="pill${k ? " light" : ""}" href="tel:${esc(p)}">${ic("phone")}${esc(pretty(p))}</a>
      <button type="button" class="rq2-max" data-max="${esc(p)}">MAX</button>
      ${admin && k === 0 ? `<button type="button" class="rq2-del" data-del="${r.id}" aria-label="Удалить запрос" title="Удалить запрос">${ic("trash")}</button>` : ""}</div>`).join("");
    return `<li class="rq-item rq2-item${fresh ? " fresh" : ""}${r.pinned ? " pinned" : ""}" style="--d:${Math.min(i, 12) * 40}ms">
      <div class="rq2-top"><span class="rq2-deal${r.deal === "rent" ? " rent" : ""}">${r.deal === "rent" ? "Аренда" : "Покупка"}</span>
        ${fresh ? `<span class="rq-new">Новый</span>` : ""}${r.source === "agent" ? `<span class="rq-agent">${ic("check-circle")}Агент 1+1</span>` : ""}
        <small>${esc(ago(r.last_seen))}${r.source !== "agent" && r.seen_count > 1 ? ` · присылали ${r.seen_count} ${r.seen_count % 10 >= 2 && r.seen_count % 10 <= 4 && (r.seen_count % 100 < 12 || r.seen_count % 100 > 14) ? "раза" : "раз"}` : ""}</small></div>
      ${chips.length || bud ? `<div class="rq-chips">${bud ? `<span class="chip rq2-bud">${esc(bud)}</span>` : ""}${chips.map((c) => `<span class="chip">${esc(c)}</span>`).join("")}</div>` : ""}
      <p class="rq-text clamp">${esc(r.text)}</p>
      <button type="button" class="rq-more" data-more hidden>Полностью</button>
      ${phones ? `<div class="rq-phones rq2-phones">${phones}</div>` : admin ? `<div class="rq2-ph"><button type="button" class="rq2-del" data-del="${r.id}" aria-label="Удалить запрос">${ic("trash")}</button></div>` : ""}</li>`;
  }

  // ─── 24.10b: «Опубликовать свой запрос» — подтверждённый номер + согласие на показ номера ──────────────
  let pubState = null;
  function pubBody() {
    const s = pubState || {};
    if (!s.verified) return `<p class="pk2-hint">Публиковать запросы могут агенты с подтверждённым номером — коллеги будут звонить вам.</p>
      <div class="dlg-actions"><a class="pill" href="/agent">Подтвердить номер</a></div>`;
    if (!s.consent) return `<p class="pk2-hint">Номер подтверждён. Осталось дать согласие на показ вашего номера коллегам — без него
      им некуда звонить.</p><div class="dlg-actions"><a class="pill" href="/agent">Дать согласие</a></div>`;
    const list = (s.clients || []).filter((c) => !c.published);
    if (!list.length) return `<p class="pk2-hint">${(s.clients || []).length ? "Запросы всех ваших покупателей уже опубликованы." : "Запрос публикуется по пожеланиям клиента."}
      Заведите клиента — укажите, что он ищет, и опубликуйте запрос.</p><div class="dlg-actions"><a class="pill" href="/clients?new=1">+ Новый клиент</a></div>`;
    return `<p class="pk2-hint">Коллеги увидят пожелания клиента (комнаты, бюджет, районы) и ваш номер. Имя и телефон клиента не показываются.
        Агентам с подходящими объектами придёт уведомление. Запрос живёт 10 дней.</p>
      <label class="agent-field"><span>Клиент</span><select id="rqPubClient">${list.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("")}</select></label>
      <label class="agent-field"><span>Комментарий для коллег — необязательно</span><textarea id="rqPubNote" rows="3" maxlength="500" placeholder="Срочность, способ оплаты…"></textarea></label>
      <p class="note">Пожелания берутся из карточки клиента — <a href="/clients" id="rqPubEdit">проверить или дополнить</a>.</p>
      <div class="dlg-actions"><button type="button" class="pill light" data-close-pub>Отмена</button><button type="button" class="pill" id="rqPubGo">Опубликовать</button></div>`;
  }
  $("rqPub").addEventListener("click", () => {
    $("rqPubBody").innerHTML = pubBody();
    const d = $("rqPubDlg");
    if (!d.open) d.showModal();
    if (window.Pick && $("rqPubClient")) Pick.select($("rqPubClient"), { searchPlaceholder: "Найти клиента" });
  });
  $("rqPubDlg").addEventListener("click", async (e) => {
    if (e.target.closest("[data-close-pub]")) { $("rqPubDlg").close(); return; }
    if (e.target.id === "rqPubEdit") { e.target.href = `/clients?id=${$("rqPubClient").value}`; return; }
    if (e.target.id !== "rqPubGo") return;
    e.target.disabled = true;
    const r = await fetch(`/api/clients/${$("rqPubClient").value}/publish`, { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ comment: $("rqPubNote").value }) });
    const d = await r.json().catch(() => ({}));
    e.target.disabled = false;
    if (!r.ok) { toast(d.detail || "Не получилось опубликовать"); return; }
    $("rqPubDlg").close();
    toast("Запрос опубликован — коллеги с подходящими объектами получат уведомление", 3500);
    page = 1; load();
  });

  async function load(append = false) {
    const p = new URLSearchParams({ page });
    if (pin) p.set("id", pin);
    let d;
    try { d = await (await fetch(`/api/requests?${p}`, { credentials: "same-origin" })).json(); }
    catch { $("rqCount").textContent = "Не получилось загрузить."; return; }
    if (d.locked) {   // без подписки: число запросов и приглашение
      $("rqCount").textContent = "";
      $("rqLocked").hidden = false;
      $("rqLocked").innerHTML = `<b>${(d.total || 0).toLocaleString("ru-RU")} запросов покупателей сейчас</b>
        <span>${d.new_24h ? `${d.new_24h} новых за сутки. ` : ""}Кто что ищет, бюджет, районы и номер агента — по подписке.</span>
        <a class="pill" href="${d.guest ? "/?login=1" : "/pay"}">${d.guest ? "Войти — неделя бесплатно" : "Оформить доступ"}</a>`;
      $("rqList").innerHTML = "";
      return;
    }
    admin = !!d.admin;
    pubState = d.publish || null;
    $("rqPub").hidden = !pubState;
    $("rqNums").innerHTML = `<span><b>${(d.total || 0).toLocaleString("ru-RU")}</b><small>${(d.total || 0) % 10 === 1 && (d.total || 0) % 100 !== 11 ? "запрос" : "запросов"}</small></span>
      ${d.new_24h ? `<span class="up"><b>+${d.new_24h}</b><small>за сутки</small></span>` : ""}`;
    const n = d.total || 0;
    const word = n % 10 === 1 && n % 100 !== 11 ? "запрос" : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? "запроса" : "запросов";
    $("rqCount").textContent = `${n.toLocaleString("ru-RU")} ${word} · свежие сверху`;
    const html = d.items.map((r, i) => itemHTML(r, i)).join("");
    if (append) $("rqList").insertAdjacentHTML("beforeend", html);
    else $("rqList").innerHTML = html || `<li class="rq-empty"><b>Запросов пока нет</b>Как только кто-то напишет «ищу / куплю» — запрос появится здесь.</li>`;
    $("rqMore").hidden = d.page >= d.pages;
    document.querySelectorAll(".rq-text.clamp").forEach((p) => {
      const b = p.nextElementSibling;
      if (p.scrollHeight > p.clientHeight + 4) b.hidden = false; else p.classList.remove("clamp");
    });
  }
  document.addEventListener("click", async (e) => {
    const t = e.target.closest("button, a");
    if (!t) return;
    if (t.hasAttribute("data-more")) { t.previousElementSibling.classList.remove("clamp"); t.hidden = true; return; }
    if (t.dataset.del) {
      if (!confirm("Удалить запрос? Повтор того же текста больше не появится.")) return;
      const r = await fetch("/api/requests", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: Number(t.dataset.del), delete: true }) });
      if (r.ok) { t.closest(".rq-item").remove(); toast("Запрос удалён"); } else toast("Не получилось удалить");
    }
  });
  $("rqMore").addEventListener("click", () => { page += 1; load(true); });
  load().then(() => { const el = document.querySelector(".rq-item.pinned"); if (el) el.scrollIntoView({ block: "center" }); });
})();
