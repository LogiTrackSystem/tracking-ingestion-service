"""Armado de viajes de prueba: una ruta recta entre dos puntos, con paradas, y el evento route.assigned correspondiente."""
import asyncio
import math
import uuid

from app import simulador
from app.geo import haversine_km


def recta(a, b, pasos=20):
    return [[a[0] + (b[0] - a[0]) * i / pasos, a[1] + (b[1] - a[1]) * i / pasos] for i in range(pasos + 1)]


def correr(c):
    return asyncio.run(c)


def codificar(puntos, precision=5):
    """Polyline codificada (misma convención que OSRM) para probar la decodificación real."""
    def valor(v):
        v = ~(v << 1) if v < 0 else (v << 1)
        salida = ""
        while v >= 0x20:
            salida += chr((0x20 | (v & 0x1F)) + 63)
            v >>= 5
        return salida + chr(v + 63)
    f = 10 ** precision
    la = lo = 0
    out = ""
    for lat, lon in puntos:
        a, b = round(lat * f), round(lon * f)
        out += valor(a - la) + valor(b - lo)
        la, lo = a, b
    return out


GIRARDOT = (4.3010, -74.8039)
BOGOTA = (4.7110, -74.0721)
CALI = (3.4516, -76.5320)
MEDELLIN = (6.2442, -75.5812)
MONTERIA = (8.7480, -75.8814)


class Viaje:
    def __init__(self, vecinos, origen, destino, km=None, paradas=None, vehiculo=None, nombre_origen="Origen", nombre_destino="Destino"):
        self.vecinos = vecinos
        self.envio_id = uuid.uuid4()
        self.ruta_id = uuid.uuid4()
        self.maestra_id = uuid.uuid4()
        self.vehiculo_id = vehiculo or uuid.uuid4()
        self.origen, self.destino = origen, destino
        self.km = km or round(haversine_km(*origen, *destino) * 1.0, 2)
        puntos = recta(origen, destino)
        vecinos.geometrias[str(self.maestra_id)] = puntos
        vecinos.rutas[str(self.maestra_id)] = {"distancia_km": self.km, "duracion_horas": self.km / 60.0, "paradas": paradas or []}
        self.nombre_origen, self.nombre_destino = nombre_origen, nombre_destino

    def payload(self, reposicion=None, **extra):
        return {
            "ruta_id": str(self.ruta_id), "envio_id": str(self.envio_id), "vehiculo_id": str(self.vehiculo_id),
            "conductor_id": str(uuid.uuid4()), "placa": "ABC123", "conductor_nombre": "Pedro", "codigo_ruta": "RUT-1",
            "ruta_maestra_id": str(self.maestra_id), "modo": "exclusiva",
            "origen": {"ubicacion_id": str(uuid.uuid4()), "nombre": self.nombre_origen, "latitud": self.origen[0],
                       "longitud": self.origen[1], "codigo_pais": "CO"},
            "destino": {"ubicacion_id": str(uuid.uuid4()), "nombre": self.nombre_destino, "latitud": self.destino[0],
                        "longitud": self.destino[1], "codigo_pais": "CO"},
            "reposicion": reposicion, **extra,
        }

    def asignar(self, reposicion=None, **extra):
        correr(simulador.procesar_route_assigned(self.payload(reposicion, **extra)))


def reposicion_desde(punto, hacia, fiable=True, km=None, fuente="osrm", tomada_en="2026-10-20T10:00:00+00:00"):
    km = km or round(haversine_km(*punto, *hacia) * 1.25, 2)
    return {
        "desde": {"latitud": punto[0], "longitud": punto[1], "codigo_pais": "CO", "fiable": fiable, "tomada_en": tomada_en,
                  "fuente": "entrega", "nombre": None},
        "hasta": {"latitud": hacia[0], "longitud": hacia[1]},
        "distancia_km": km, "duracion_horas": km / 60.0, "geometria": codificar(recta(punto, hacia, 6)),
        "fuente": fuente, "motivo": None,
    }


def paso(reloj, n=1, segundos=60):
    """Avanza el reloj y corre n ciclos completos del simulador."""
    for _ in range(n):
        reloj.avanzar(segundos)
        correr(simulador.tick())


def hasta(reloj, condicion, max_pasos=400, segundos=60):
    for _ in range(max_pasos):
        paso(reloj, 1, segundos)
        if condicion():
            return True
    return False
