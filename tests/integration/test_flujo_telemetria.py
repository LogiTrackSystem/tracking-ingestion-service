"""Integración: una lectura enviada por API se guarda y se consulta por vehículo."""
import uuid

from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.routers import telemetria


def test_lectura_de_telemetria_se_guarda_y_se_consulta(db, monkeypatch):
    # Arrange: la publicación a RabbitMQ se simula
    publicados = []

    async def publicar(nombre, payload):
        publicados.append(nombre)

    monkeypatch.setattr(telemetria, "publish_event", publicar)
    app.dependency_overrides[get_db] = lambda: db
    vehiculo = str(uuid.uuid4())
    try:
        cliente = TestClient(app)
        # Act
        creada = cliente.post("/telemetria/", json={"vehiculo_id": vehiculo, "latitud": 4.71, "longitud": -74.07, "velocidad_kmh": 60})
        lecturas = cliente.get(f"/telemetria/{vehiculo}")
    finally:
        app.dependency_overrides.clear()
    # Assert
    assert creada.status_code == 201 and publicados == ["telemetry.raw"]
    assert len(lecturas.json()) == 1 and float(lecturas.json()[0]["velocidad_kmh"]) == 60
