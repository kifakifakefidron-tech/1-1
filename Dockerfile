FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/app/data/strely.db

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY web ./web

RUN useradd --create-home --uid 1000 strely && mkdir -p /app/data && chown strely /app/data
USER strely

EXPOSE 8090
CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8090", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]
