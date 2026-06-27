# Фикстура ТОЛЬКО для репетиции деплой-пути — проверить инфру до собеса.
# На самом демо это место занимает приложение, собранное агентами (контракт тот же:
# :8000, main:app, GET /health -> 200).
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return "<h1>deploy path OK</h1><p>инфра жива, можно репетировать демо.</p>"
