import os

import httpx
from dotenv import load_dotenv

load_dotenv()

ROUTING_SERVICE_URL = os.getenv("ROUTING_SERVICE_URL")
FLEET_SERVICE_URL = os.getenv("FLEET_SERVICE_URL")
SHIPMENT_SERVICE_URL = os.getenv("SHIPMENT_SERVICE_URL")


class DatosNoDisponibles(Exception):
    """Otro servicio no respondió o devolvió un error: se reintenta en el siguiente ciclo."""


async def _get(base: str | None, ruta: str) -> dict:
    if not base:
        raise DatosNoDisponibles(f"falta configurar la URL del servicio para {ruta}")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{base}{ruta}")
    except httpx.HTTPError as exc:
        raise DatosNoDisponibles(f"{base} no responde ({type(exc).__name__})")
    if resp.status_code != 200:
        raise DatosNoDisponibles(f"{base}{ruta} respondió HTTP {resp.status_code}")
    return resp.json()


async def obtener_envio(envio_id) -> dict:
    return await _get(SHIPMENT_SERVICE_URL, f"/envios/{envio_id}")


async def obtener_ruta_maestra(ruta_maestra_id) -> dict:
    return await _get(ROUTING_SERVICE_URL, f"/rutas-maestras/{ruta_maestra_id}")


async def obtener_geometria(ruta_maestra_id) -> dict:
    return await _get(ROUTING_SERVICE_URL, f"/rutas-maestras/{ruta_maestra_id}/geometria")


async def obtener_vehiculo(vehiculo_id) -> dict:
    return await _get(FLEET_SERVICE_URL, f"/vehiculos/{vehiculo_id}")