from fastapi import FastAPI
from .routers import telemetria

app = FastAPI(title="Tracking Ingestion Service")
app.include_router(telemetria.router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "tracking-ingestion-service"}