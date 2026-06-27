# Фиксированный образ приложения. Агенты его НЕ редактируют — копируется поверх при деплое,
# чтобы контракт сборки был детерминирован. Приложение обязано слушать :8000 как main:app.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
