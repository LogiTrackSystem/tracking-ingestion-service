"""Pruebas de Tracking con SQLite en memoria, reloj controlado y servicios vecinos simulados."""
import itertools
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.pop("INTERNAL_API_KEY", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@compiles(JSONB, "sqlite")
def _jsonb_en_sqlite(tipo, compilador, **kw):
    return "JSON"


from app import clientes_http, simulador  # noqa: E402
from app.database import Base  # noqa: E402
from app import models  # noqa: E402

_ids = itertools.count(1)


@event.listens_for(models.LecturaTelemetria, "before_insert")
def _asignar_id(mapper, connection, target):
    if target.id is None:   # SQLite no autoincrementa una clave compuesta
        target.id = next(_ids)


_CAMPOS_FECHA = ("ultima_actualizacion", "iniciado_en", "llegada_en", "hora_estimada_llegada", "en_parada_hasta",
                 "creado_en", "actualizado_en", "tiempo")


def _con_zona(objeto, *args):
    """SQLite devuelve fechas sin zona horaria; PostgreSQL las devuelve con zona. Se iguala para las pruebas."""
    for campo in _CAMPOS_FECHA:
        valor = objeto.__dict__.get(campo)
        if isinstance(valor, datetime) and valor.tzinfo is None:
            objeto.__dict__[campo] = valor.replace(tzinfo=timezone.utc)


for _modelo in (models.SimulacionViaje, models.CargaCompartida, models.LecturaTelemetria):
    event.listen(_modelo, "load", _con_zona)
    event.listen(_modelo, "refresh", _con_zona)


class Reloj:
    def __init__(self):
        self.t = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.t

    def avanzar(self, segundos):
        self.t += timedelta(seconds=segundos)


@pytest.fixture()
def reloj(monkeypatch):
    r = Reloj()
    monkeypatch.setattr(simulador, "_ahora", r)
    monkeypatch.setattr(simulador.random, "uniform", lambda a, b: 1.0)   # velocidad determinista
    return r


@pytest.fixture()
def sesion_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    fabrica = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(simulador, "SessionLocal", fabrica)
    yield fabrica
    engine.dispose()


@pytest.fixture()
def db(sesion_factory):
    s = sesion_factory()
    yield s
    s.close()


@pytest.fixture()
def bus(monkeypatch):
    """Todo lo que Tracking publica, en orden, junto con las llamadas a Fleet (para verificar el orden)."""
    registro = []

    async def publicar(nombre, payload):
        registro.append(("evento", nombre, payload))

    monkeypatch.setattr(simulador, "publish_event", publicar)
    return registro


class Vecinos:
    """Shipment, Routing y Fleet simulados."""

    def __init__(self, bus):
        self.bus = bus
        self.envios = {}
        self.rutas = {}
        self.geometrias = {}
        self.fleet_caido = False
        self.ubicaciones = []   # lo que Tracking le informó a Fleet
        self.trayectos = []
        self.trayecto_respuesta = None

    async def obtener_envio(self, envio_id):
        return self.envios.get(str(envio_id), {"estado": "asignado", "codigo_envio": "ENV-1", "origen": "Origen",
                                               "destino": "Destino", "tipo_carga": "general"})

    async def obtener_ruta_maestra(self, ruta_maestra_id):
        return self.rutas[str(ruta_maestra_id)]

    async def obtener_geometria(self, ruta_maestra_id):
        return {"puntos": self.geometrias[str(ruta_maestra_id)]}

    async def obtener_vehiculo(self, vehiculo_id):
        return {"tipo": "camion", "capacidad_tanque_l": 400, "consumo_km_por_litro": 4.0, "tipo_combustible": "diesel"}

    async def obtener_trayecto(self, a, b, c, d):
        self.trayectos.append((a, b, c, d))
        if isinstance(self.trayecto_respuesta, Exception):
            raise self.trayecto_respuesta
        return self.trayecto_respuesta

    async def informar_ubicacion_vehiculo(self, vehiculo_id, cuerpo):
        if self.fleet_caido:
            raise clientes_http.DatosNoDisponibles("Fleet no responde")
        self.ubicaciones.append((str(vehiculo_id), cuerpo))
        self.bus.append(("fleet", "ubicacion", cuerpo))
        return {"aplicada": True}


@pytest.fixture()
def vecinos(monkeypatch, bus):
    v = Vecinos(bus)
    for nombre in ("obtener_envio", "obtener_ruta_maestra", "obtener_geometria", "obtener_vehiculo",
                   "obtener_trayecto", "informar_ubicacion_vehiculo"):
        monkeypatch.setattr(clientes_http, nombre, getattr(v, nombre))
    return v
