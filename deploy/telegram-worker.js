// Посредник к Telegram Bot API для сервера в России (api.telegram.org там часто недоступен).
// Cloudflare Workers, бесплатный тариф (100 000 запросов в сутки — нам с запасом).
//
// Как включить:
//  1. dash.cloudflare.com → Workers & Pages → Create → Worker → вставить этот код → Deploy.
//  2. Worker → Settings → Variables → добавить секрет BOT_TOKEN = токен бота (тот же, что в .env на сервере).
//  3. На сервере в .env: TELEGRAM_API_BASE=https://<имя-воркера>.<ваш-поддомен>.workers.dev
//     и docker compose up -d. Проверка: в админке/журнале бота пропадут ошибки соединения.
// Пропускает ТОЛЬКО запросы нашего бота (/bot<токен>/…) — чужим посредник не послужит.
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const ours = env.BOT_TOKEN && (url.pathname.startsWith(`/bot${env.BOT_TOKEN}/`)
      || url.pathname.startsWith(`/file/bot${env.BOT_TOKEN}/`));
    if (!ours) return new Response("not found", { status: 404 });
    const init = { method: request.method, headers: { "Content-Type": request.headers.get("Content-Type") || "application/json" } };
    if (request.method !== "GET" && request.method !== "HEAD") init.body = await request.arrayBuffer();
    return fetch(`https://api.telegram.org${url.pathname}${url.search}`, init);
  },
};
