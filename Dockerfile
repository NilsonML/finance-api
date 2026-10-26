FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Зависимости отдельным слоем — пересобираются, только когда меняется requirements.txt
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN useradd --create-home --uid 1000 appuser && mkdir -p /app/data && chown -R appuser /app
USER appuser

EXPOSE 8000
# --host 0.0.0.0: слушать все сетевые интерфейсы контейнера. С 127.0.0.1 (по умолчанию)
# сервер был бы доступен только изнутри контейнера, и снаружи до него не достучаться.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
