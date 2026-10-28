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

## Tramo en vacío, ubicación real y cargas compartidas

- **Sale de donde está.** Si Routing informa de dónde sale el vehículo (`reposicion` en `route.assigned`), la simulación primero recorre ese tramo **en vacío** (`fase = reposicion`) y se detiene exactamente en el origen; desde ahí empieza el viaje con carga (`fase = viaje`). `tracking.trip_started` se publica al salir con la carga, no al salir en vacío. Si Fleet no tenía una ubicación fiable se usa la última lectura real del propio vehículo (consultando `GET /trayecto/` de Routing); si tampoco hay lectura, no se inventa un tramo y queda constancia.
- **Telemetría.** `telemetry.raw` suma `ruta_id` y `fase`; el odómetro es continuo entre el tramo en vacío, el viaje y los viajes siguientes.
- **Última ubicación.** Al llegar, Tracking le informa a Fleet el destino **antes** de publicar `tracking.trip_arrived`; si el viaje se interrumpe (`shipment.returned`, `shipment.cancelled`, entrega manual) informa dónde quedó el vehículo y **no** publica llegada; si el envío se cancela sin haberse movido, devuelve la ubicación que tenía. Si Fleet no responde se reintenta cada ciclo.
- **Cargas compartidas.** Un envío con `modo = compartida` no crea otra simulación: se guarda en `cargas_compartidas` y sube/baja según el kilómetro del vehículo anfitrión, publicando su propio `trip_started` y `trip_arrived`. Un anfitrión que no llega a su destino, o que ya pasó por la recogida cuando se registra la carga, deja la carga pendiente como `cancelada` y publica `tracking.shared_load_missed` para que Routing la reasigne; la que ya iba a bordo queda como `interrumpida` (requiere a un operador). Variables nuevas: `TIMEOUT_TRAYECTO_S`, `ESPERA_SIMULACION_S`, `INTENTOS_SIMULACION`, `DB_LOCK_TIMEOUT_MS`.
- **Seguimiento.** `GET /seguimiento/envio/{id}` agrega `fase`, `reposicion` y `carga_compartida`; para una carga compartida muestra la posición del vehículo y el avance de su tramo.

**Consume además:** `shipment.cancelled`. **Migración** `c47d9e12b3f6`. **Pruebas:** `python -m pytest tests`.

## Seguimiento para la cuenta de cliente

`GET /seguimiento/cliente/envio/{id}` (más `/trazado` y `/recorrido`) devuelve al **cliente** el estado, la ubicación real más reciente, el avance y la ruta de **su** envío, con la misma información de mapa que ve el personal pero sin vehículo (placa, conductor), combustible ni datos de otros envíos. El dueño se valida siempre contra Shipment con la identidad que inyecta el Gateway (`X-Cliente-Id`); un envío ajeno y uno inexistente responden igual (404). Sin viaje responde 200 con `estado_viaje = "sin_viaje"`; sin posición real `posicion.disponible = false` (no se inventa nada); si el viaje sigue en curso y la última posición pasa de `UBICACION_OBSOLETA_SEGUNDOS` (60) llega `posicion.desactualizada = true`. Una carga compartida solo muestra su tramo. El Gateway deja pasar al rol cliente únicamente por esta ruta. Detalle completo: `SEGUIMIENTO_CLIENTE.md`. **Pruebas:** `tests/test_seguimiento_cliente.py`.

