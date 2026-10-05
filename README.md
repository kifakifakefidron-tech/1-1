# СТРЕЛЫ ➳ поиск объектов

Сайт, где можно искать квартиры, новостройки, дома, участки и коммерцию Краснодара,
которые риелторы присылают в чаты WhatsApp / Telegram / MAX.

Как это работает:

1. **worker** каждые 1–2 минуты забирает новые сообщения из Wappi (по API — старое
   приложение продолжает получать сообщения как раньше).
2. Каждое сообщение разбирается: нейросеть DeepSeek (один запрос на сообщение) +
   проверка правилами. Если ключа DeepSeek нет — разбор только правилами.
3. Одинаковые объекты от разных агентов и из разных чатов склеиваются в один.
4. **web** — сам сайт: поиск, фильтры, карта, карточка объекта.
   Покупатели видят контакт СТРЕЛ, коллеги после ввода кода — телефоны агентов.

Всё хранится в одном файле базы `data/strely.db`.

---

## Установка на сервер (пошагово)

Нужно: сервер с Docker (там же, где работает старое приложение), доступ по SSH и
поддомен, например `poisk.arrowsrealty.ru`.

### 1. Поддомен

В панели, где настроен домен `arrowsrealty.ru`, добавьте запись:

| Тип | Имя | Значение |
|-----|-----|----------|
| A   | `poisk` | IP-адрес сервера |

Запись начинает работать через 5–30 минут.

### 2. Скачать проект на сервер

```bash
ssh root@IP-СЕРВЕРА
cd /opt
git clone https://github.com/<ваш-аккаунт>/<репозиторий>.git strely
cd strely
```

### 3. Настройки

```bash
cp .env.example .env
nano .env
```

Заполните:

- `DEEPSEEK_API_KEY` — ключ с platform.deepseek.com (лучше **новый**, старый ключ
  засвечен в репозитории whatshapp_ai_agents-new);
- `WAPPI_TOKEN` и `WAPPI_PROFILES` — из личного кабинета Wappi, формат
  `wa:id-профиля,tg:id-профиля`;
- `ACCESS_CODE` — код для коллег (можно по-русски);
- `PUBLIC_CONTACT` — куда пишут покупатели (ссылка WhatsApp СТРЕЛ);
- `SECRET_KEY` — вставьте результат команды `openssl rand -hex 32`.

Сохранить в nano: `Ctrl+O`, `Enter`, выйти: `Ctrl+X`.

### 4. Запуск

```bash
mkdir -p data && chown 1000:1000 data
docker compose up -d --build
docker compose ps
```

Оба сервиса (`web` и `worker`) должны быть в состоянии `running`.
Проверка: `curl -s http://127.0.0.1:8090/api/stats` — должен вернуться короткий ответ с цифрами.

Сайт слушает только `127.0.0.1:8090` — снаружи он доступен лишь через nginx.
Если порт 8090 на сервере уже занят, поменяйте левую цифру в `docker-compose.yml`
(`"127.0.0.1:8091:8090"`) и в настройке nginx ниже.

### 5. nginx и HTTPS

Создайте файл `/etc/nginx/sites-available/poisk.arrowsrealty.ru`:

```nginx
server {
    listen 80;
    server_name poisk.arrowsrealty.ru;

    location / {
        proxy_pass http://127.0.0.1:8090;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Включите и получите бесплатный сертификат:

```bash
ln -s /etc/nginx/sites-available/poisk.arrowsrealty.ru /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
certbot --nginx -d poisk.arrowsrealty.ru
```

Если старое приложение отдаёт nginx из Docker-контейнера, а не системный nginx —
этот блок `server` нужно добавить в его конфиг, а вместо `127.0.0.1` указать
адрес хоста (`172.17.0.1`). Если сомневаетесь — пришлите вывод `docker ps`.

Готово: откройте `https://poisk.arrowsrealty.ru`.

---

## Каждый день

| Что нужно | Команда (в папке `/opt/strely`) |
|-----------|---------------------------------|
| Посмотреть, что делает worker | `docker compose logs -f --tail 50 worker` |
| Обновить после изменений на GitHub | `git pull && docker compose up -d --build` |
| Перезапустить | `docker compose restart` |
| Остановить | `docker compose down` |
| Сделать копию базы | `cp data/strely.db data/strely-$(date +%F).db` |
| Статистика | `curl -s http://127.0.0.1:8090/api/stats` |

В логах worker каждые полторы минуты строка вида
`цикл: {'new_messages': 12, 'processed': 12, 'geocoded': 3, 'archived': 0}`.

Объект пропадает из поиска, если его не присылали 45 дней (`STALE_DAYS` в `.env`).

---

## Для разработчика

```bash
pip install -r requirements-dev.txt
pytest
uvicorn app.server:app --port 8090 --reload   # сайт
python -m app.worker                           # фоновый разбор
```

Подробности об устройстве и принятых решениях — в `CLAUDE.md`.
