"""Candados reales en PostgreSQL. Se omite sin TEST_PG_URL: defina TEST_PG_URL=postgresql+psycopg2://usuario:clave@host/base_vacia
(nunca guarde esa URL en el repositorio)."""
import asyncio
import os
import time
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

URL = os.getenv("TEST_PG_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_PG_URL no definida: pruebas de candados en PostgreSQL omitidas")

from app import simulador  # noqa: E402
from app.database import Base  # noqa: E402
from app.models import SimulacionViaje  # noqa: E402


@pytest.fixture()
def pg(monkeypatch):
    engine = create_engine(URL)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    fabrica = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(simulador, "SessionLocal", fabrica)
    monkeypatch.setattr(simulador, "ESPERA_SIMULACION_S", 0.05)
    abiertas = []

    def sesion():
        s = fabrica()
        abiertas.append(s)
        return s

    yield sesion
    for s in abiertas:
        s.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


def _simulacion(sesion, estado="preparando"):
    s = sesion()
    envio = uuid.uuid4()
    s.add(SimulacionViaje(envio_id=envio, ruta_id=uuid.uuid4(), vehiculo_id=uuid.uuid4(), estado=estado))
    s.commit()
    return envio


def test_un_evento_del_envio_espera_sin_congelar_el_bucle_mientras_se_prepara_el_viaje(pg):
    envio = _simulacion(pg)
    s = pg()
    s.query(SimulacionViaje).filter(SimulacionViaje.envio_id == envio).with_for_update().one()   # _preparar espera HTTP con la fila tomada
    latidos = []

    async def principal():
        async def pulso():
            while True:
                latidos.append(time.monotonic())
                await asyncio.sleep(0.02)

        tarea_pulso = asyncio.create_task(pulso())
        evento = asyncio.create_task(simulador.procesar_envio_cancelado({"envio_id": str(envio)}))
        await asyncio.sleep(0.5)
        durante, pendiente = len(latidos), not evento.done()
        s.rollback()                                   # el ciclo termina de preparar y suelta la fila
        await asyncio.wait_for(evento, timeout=5)
        tarea_pulso.cancel()
        return durante, pendiente

    durante, pendiente = asyncio.run(principal())
    assert durante >= 10 and pendiente is True        # el bucle siguió vivo y el evento esperó sin perderse
    assert pg().query(SimulacionViaje).filter(SimulacionViaje.envio_id == envio).one().estado == "cancelada"


def test_si_la_simulacion_nunca_se_libera_el_evento_falla_en_vez_de_colgar(pg, monkeypatch):
    monkeypatch.setattr(simulador, "INTENTOS_SIMULACION", 3)
    envio = _simulacion(pg)
    s = pg()
    s.query(SimulacionViaje).filter(SimulacionViaje.envio_id == envio).with_for_update().one()
    with pytest.raises(RuntimeError):
        asyncio.run(simulador.procesar_aduana_retiene({"envio_id": str(envio)}))
    s.rollback()
