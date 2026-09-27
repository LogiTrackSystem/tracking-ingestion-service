# Tracking Ingestion Service

Microservicio de ingesta de telemetría GPS/IoT de la flota (posición, velocidad, nivel de combustible, temperatura de carga, y datos de mantenimiento del vehículo). Publica cada lectura como evento `telemetry.raw` en RabbitMQ para que otros servicios (Routing, Maintenance) la consuman.

## Requisitos

- Python 3.11+
- PostgreSQL con la extensión **TimescaleDB** habilitada
- RabbitMQ

## Setup

1. Crear la base de datos `tracking_db` con la extensión `timescaledb` activada (ver sección de infraestructura del proyecto).
2. Copiar `.env.example` a `.env` y completar con tus credenciales reales.
3. `python -m venv venv && venv\Scripts\activate`
4. `pip install -r requirements.txt`
5. `alembic upgrade head`
6. `uvicorn app.main:app --reload --port 8004`

## Endpoints

- `POST /telemetria/` — registra una lectura de telemetría y publica `telemetry.raw`.
- `GET /telemetria/{vehiculo_id}` — consulta el histórico de lecturas de un vehículo (filtros opcionales `desde`, `hasta`, `limite`).
- `GET /health` — healthcheck.

## Eventos

- **Publica:** `telemetry.raw`