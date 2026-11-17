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


---

<!-- entrega-final -->

## Descripción

Simulación del viaje, recepción de telemetría y consultas de seguimiento para el mapa, por envío y por cuenta de cliente.

- **Puerto local:** 8004   ·   **Despliegue activo:** <https://logitrack-tracking-aq3a.onrender.com> (`/health` y `/metrics` públicos)
- **Imagen Docker:** `<usuario-dockerhub>/logitrack-tracking`   ·   **Repositorio de CI/CD:** `LogiTrackSystem/pipelines`

## Variables de entorno

Copia `.env.example` como `.env` y completa los valores. El `.env` **no se sube** al repositorio.

| Variable | Para qué sirve |
|---|---|
| `DATABASE_URL` | Conexión a la base PostgreSQL del servicio (`postgresql://usuario:clave@host:5432/<base>`). Es un secreto. |
| `RABBITMQ_URL` | URL AMQP del bus de eventos RabbitMQ (CloudAMQP en producción). Es un secreto. |
| `ROUTING_SERVICE_URL` | URL base de Routing Service, sin barra final (en local `http://localhost:<puerto>`). |
| `FLEET_SERVICE_URL` | URL base de Fleet Service, sin barra final (en local `http://localhost:<puerto>`). |
| `SHIPMENT_SERVICE_URL` | URL base de Shipment Service, sin barra final (en local `http://localhost:<puerto>`). |
| `SIM_TICK_SEGUNDOS` | Cada cuántos segundos avanza la simulación del viaje. |
| `SIM_FACTOR_TIEMPO` | Factor de aceleración del tiempo simulado. |
| `SIM_COMBUSTIBLE_INICIAL_PCT` | Nivel inicial de combustible (%) de un viaje simulado. |
| `INTERNAL_API_KEY` | Clave compartida entre el gateway y los servicios: exige la cabecera `X-Internal-Key` en todas las rutas salvo `/health` y `/metrics`. Vacía = sin restricción (solo local). Es un secreto. |
| `UMBRAL_MISMO_LUGAR_KM` | Por debajo de esta distancia el vehículo "ya está en el origen". |
| `DB_LOCK_TIMEOUT_MS` | Espera máxima (ms) por un candado de fila antes de fallar; 0 = sin límite. |
| `TIMEOUT_TRAYECTO_S` | Espera máxima (s) por Routing `/trayecto`. |
| `ESPERA_SIMULACION_S` | Espera (s) entre reintentos cuando la simulación del envío está tomada. |
| `INTENTOS_SIMULACION` | Número de esos reintentos. |

## Documentación de la API

- **Local** (sin `INTERNAL_API_KEY`): <http://localhost:8004/docs> (Swagger) y `http://localhost:8004/openapi.json`.
- **Producción:** el servicio solo atiende peticiones que llegan por el gateway (con `INTERNAL_API_KEY` activa su `/docs` no es público); la documentación de todas las rutas está en <https://logitrack-gateway.onrender.com/docs>.

## Base de datos

- Migraciones con Alembic (`alembic/`): `alembic upgrade head` (el `Dockerfile` ya lo ejecuta al arrancar).
- `db/esquema.sql`: esquema completo de `tracking_db` generado desde las migraciones.
- Datos de ejemplo: este servicio se alimenta de eventos y llamadas de otros servicios; se generan ejecutando el flujo completo (por ejemplo `_pruebas/casos.py` del proyecto).
- Respaldo y restauración: ver `db/README.md`.

## Pruebas

Con el entorno virtual activo y `pip install -r requirements-dev.txt`:

```
pytest tests/unit                    # 2 pruebas unitarias
pytest tests/integration             # 1 prueba de integración
pytest --cov=app --cov-report=term   # toda la suite con cobertura
```

| Prueba | Qué verifica |
|---|---|
| Unitaria 1 | `geo.haversine_km` |
| Unitaria 2 | `geo.posicion_en` |
| Integración | registro de una lectura de telemetría y su consulta por vehículo |

Archivos: `tests/unit/` y `tests/integration/` (test_haversine.py, test_posicion_en.py, test_flujo_telemetria.py). Cobertura de `app/` medida: **≈ 83 %**; en cada push y Pull Request GitHub Actions la recalcula y guarda `coverage.xml` como artefacto (`cobertura-tracking`).

## Métricas y monitoreo

Expone `GET /metrics` (formato Prometheus: `http_requests_total` y `http_request_duration_*`). Prometheus y el dashboard de Grafana están en el repositorio `logitrack-monitoring`.

## CI/CD

- `.github/workflows/pr.yml`: en cada push (rama distinta de `main`) y en cada Pull Request a `main` ejecuta el CI reutilizable (lint, pruebas con cobertura, `docker build` y prueba de arranque).
- `.github/workflows/deploy.yml`: al fusionar en `main`, y solo con las pruebas en verde, publica la imagen en Docker Hub y despliega en Render (con verificación de `/health` y rollback manual).
- Detalle y estrategia de ramas: `CICD.md` en el repositorio `LogiTrackSystem/pipelines`.

