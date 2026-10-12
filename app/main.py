from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from .consumidor import iniciar_consumidor
from .events import cerrar_publicador
from .routers import seguimiento, simulaciones, telemetria
from .simulador import TICK_SEGUNDOS, tick


@asynccontextmanager
async def lifespan(app: FastAPI):
    conexion = await iniciar_consumidor()
    programador = AsyncIOScheduler()
    programador.add_job(
        tick, "interval", seconds=TICK_SEGUNDOS,
        id="tick_simulacion", max_instances=1, coalesce=True,
    )
    programador.start()
    yield
    programador.shutdown(wait=False)
    await conexion.close()
    await cerrar_publicador()


app = FastAPI(title="Tracking Ingestion Service", lifespan=lifespan)
app.include_router(telemetria.router)
app.include_router(simulaciones.router)
app.include_router(seguimiento.router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "tracking-ingestion-service"}