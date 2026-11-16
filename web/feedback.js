// 16.11 (Артём): «Идеи и обратная связь» в разделе «Поддержка» (/contacts#feedback).
// Сообщение уходит админу во вкладку «Жалобы» (POST /api/feedback). Писать могут только вошедшие — видно, кому ответить.
(() => {
  const $ = (id) => document.getElementById(id);
  const form = $("fbForm");
  if (!form) return;
  const text = $("fbText"), err = $("fbErr"), send = $("fbSend");

  function show(which) {
    form.hidden = which !== "form";
    $("fbGuest").hidden = which !== "guest";
    $("fbDone").hidden = which !== "done";
  }
  function fail(msg) { err.textContent = msg; err.hidden = !msg; }

  fetch("/api/me", { credentials: "same-origin" })
    .then((r) => r.json()).then((d) => show(d && d.me ? "form" : "guest"))
    .catch(() => show("form"));

  // пришли из меню «Поддержка» или после входа (?support=1) — сразу к форме
  if (location.hash === "#feedback") setTimeout(() => $("feedback").scrollIntoView({ block: "start" }), 60);

  text.addEventListener("input", () => {
    $("fbCount").textContent = text.value.length;
    if (!err.hidden && text.value.trim().length >= 10) fail("");
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const body = { kind: fd.get("kind"), text: text.value.trim(), page: String(fd.get("page") || "").trim(),
      call_me: !!fd.get("call_me") };
    if (body.text.length < 10) { fail("Напишите чуть подробнее — хотя бы одно предложение."); text.focus(); return; }
    fail("");
    send.disabled = true;
    send.textContent = "Отправляем…";
    try {
      const r = await fetch("/api/feedback", { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const d = await r.json().catch(() => ({}));
      if (r.status === 401) { show("guest"); return; }
      if (!r.ok) { fail(d.detail || d.error || "Не получилось отправить — попробуйте ещё раз."); return; }
      form.reset();
      $("fbCount").textContent = "0";
      show("done");
      $("fbDone").scrollIntoView({ block: "nearest", behavior: "smooth" });
    } catch {
      fail("Нет связи с сервером — проверьте интернет и нажмите ещё раз.");
    } finally {
      send.disabled = false;
      send.textContent = "Отправить";
    }
  });

  $("fbMore").addEventListener("click", () => { show("form"); text.focus(); });
})();
