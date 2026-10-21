import os

import httpx
from .interno import cabeceras_internas
from dotenv import load_dotenv

load_dotenv()

ROUTING_SERVICE_URL = os.getenv("ROUTING_SERVICE_URL")
FLEET_SERVICE_URL = os.getenv("FLEET_SERVICE_URL")
SHIPMENT_SERVICE_URL = os.getenv("SHIPMENT_SERVICE_URL")


class DatosNoDisponibles(Exception):
    """Otro servicio no respondió o devolvió un error: se reintenta en el siguiente ciclo."""


# Routing puede tardar hasta ~21 s en responder /trayecto cuando OSRM no contesta (2 intentos x 10 s + pausa) antes de
# devolver su tramo "estimado"; Tracking debe esperar más que eso o la simulación se quedaría preparando para siempre.
TIMEOUT_TRAYECTO_S = float(os.getenv("TIMEOUT_TRAYECTO_S", "30"))


async def _get(base: str | None, ruta: str, timeout: float = 10.0) -> dict:
    if not base:
        raise DatosNoDisponibles(f"falta configurar la URL del servicio para {ruta}")
    try:
        async with httpx.AsyncClient(timeout=timeout, headers=cabeceras_internas()) as client:
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

async def obtener_trayecto(desde_lat, desde_lon, hasta_lat, hasta_lon) -> dict:
    """Tramo por carretera entre dos puntos (Routing). Sirve para el recorrido en vacío cuando Fleet no sabe dónde está."""
    ruta = (f"/trayecto/?desde_lat={desde_lat}&desde_lon={desde_lon}&hasta_lat={hasta_lat}&hasta_lon={hasta_lon}")
    return await _get(ROUTING_SERVICE_URL, ruta, timeout=TIMEOUT_TRAYECTO_S)


async def informar_ubicacion_vehiculo(vehiculo_id, cuerpo: dict) -> dict:
    """Le dice a Fleet dónde quedó realmente el vehículo (llegada o interrupción). Fleet descarta lo que sea más viejo
    que lo que ya tiene. Lanza DatosNoDisponibles si no se pudo: se reintenta en el siguiente ciclo."""
    if not FLEET_SERVICE_URL:
        raise DatosNoDisponibles("falta configurar FLEET_SERVICE_URL para informar la ubicación")
    try:
        async with httpx.AsyncClient(timeout=10.0, headers=cabeceras_internas()) as client:
            resp = await client.put(f"{FLEET_SERVICE_URL}/vehiculos/{vehiculo_id}/ubicacion", json=cuerpo)
    except httpx.HTTPError as exc:
        raise DatosNoDisponibles(f"Fleet no responde ({type(exc).__name__})")
    if resp.status_code == 404:
        return {"aplicada": False, "motivo": "VEHICULO_NO_ENCONTRADO"}  # no tiene sentido reintentar
    if resp.status_code != 200:
        raise DatosNoDisponibles(f"Fleet respondió HTTP {resp.status_code} al guardar la ubicación")
    return resp.json()
