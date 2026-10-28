"""Seguimiento de un envío para la cuenta de CLIENTE.

Misma fuente de datos que el mapa del personal (SimulacionViaje / CargaCompartida / lecturas_telemetria, a través de
las mismas funciones de seguimiento.py), pero con tres garantías propias:

1. El dueño se valida SIEMPRE contra Shipment con la identidad que puso el Gateway (X-Cliente-Id, tomada del token).
   Nunca se acepta un cliente_id enviado por el frontend. Un envío ajeno y uno inexistente responden idéntico (404).
2. La respuesta es reducida: no trae vehículo (placa, conductor), combustible, paradas de reposición ni datos de otros
   envíos. Un envío que viaja en el vehículo de otro solo muestra SU tramo.
3. Nunca se inventa nada: sin posición real, `posicion.disponible` es False y no se dibuja ningún punto.

El Gateway solo deja pasar al rol cliente por /seguimiento/cliente/envio/{uuid}[/trazado|/recorrido]; el resto de
/seguimiento, /telemetria y /simulaciones sigue siendo solo del personal.
"""
import os
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import desc
from sqlalchemy.orm import Session

from .. import clientes_http, simulador
from ..database import get_db
from ..geo import acumulados, posicion_en
from ..models import CargaCompartida, LecturaTelemetria, SimulacionViaje
from ..schemas import RecorridoClientePunto, SeguimientoClienteLeer, TrazadoClienteLeer
from ..simulador import ESTADOS_ACTIVOS
from .seguimiento import _carga_de_envio, _en_parada, _extremos, _paradas

router = APIRouter(prefix="/seguimiento/cliente/envio", tags=["Seguimiento (cliente)"])

# Si el viaje está en curso y la última posición es más vieja que esto, el cliente ve el aviso de "ubicación desactualizada".
UMBRAL_OBSOLETA_S = int(os.getenv("UBICACION_OBSOLETA_SEGUNDOS", "60"))

_MENSAJE_SIN_VIAJE = {
    "programado": "Envío programado: todavía no sale.",
    "pendiente": "Estamos asignando un vehículo a su envío.",
    "sin_asignacion": "Todavía no hay un vehículo disponible; se asignará en cuanto sea posible.",
    "asignado": "Vehículo asignado: el viaje está por comenzar.",
    "cancelado": "Envío cancelado.",
    "devuelto": "Envío devuelto.",
    "entregado": "Envío entregado.",
    "retenido_aduana": "Retenido en aduana: el viaje saldrá cuando se libere.",
}
_ESTADO_CARGA = {"pendiente": "preparando", "a_bordo": "en_curso", "entregada": "finalizada",
                 "cancelada": "cancelada", "interrumpida": "cancelada"}
_MENSAJE_CARGA = {
    "pendiente": "Esperando que el vehículo llegue al punto de recogida.",
    "a_bordo": "En camino.",
    "entregada": "Llegó a su destino.",
    "cancelada": "Viaje cancelado: se reasignará o lo resolverá un operador.",
    "interrumpida": "El viaje se interrumpió: un operador lo está atendiendo.",
}


# ---------- Autorización ----------

def _no_encontrado() -> HTTPException:
    return HTTPException(status_code=404, detail={"codigo": "ENVIO_NO_ENCONTRADO", "mensaje": "Envío no encontrado"})


def _uuid_o_none(valor: Optional[str]):
    try:
        return uuid.UUID(valor) if valor else None
    except ValueError:
        return None


async def envio_propio(envio_id: uuid.UUID, request: Request) -> dict:
    """Devuelve el envío (según Shipment) solo si pertenece al cliente autenticado; si no, 404 sin más datos."""
    if request.headers.get("x-user-rol") != "cliente":
        raise HTTPException(status_code=403, detail={"codigo": "SIN_PERMISO", "mensaje": "Esta consulta es solo para cuentas de cliente"})
    cliente_id = _uuid_o_none(request.headers.get("x-cliente-id"))
    if cliente_id is None:
        raise HTTPException(status_code=403, detail={"codigo": "SIN_PERMISO", "mensaje": "La cuenta de cliente no está ligada a un cliente"})
    try:
        envio = await clientes_http.obtener_envio_de_cliente(envio_id, cliente_id, _uuid_o_none(request.headers.get("x-user-id")))
    except clientes_http.EnvioNoEncontrado:
        raise _no_encontrado()
    except clientes_http.DatosNoDisponibles:
        raise HTTPException(status_code=503, detail={"codigo": "SEGUIMIENTO_NO_DISPONIBLE",
                                                     "mensaje": "No se pudo verificar el envío en este momento; intente de nuevo."})
    # Doble verificación (defensa en profundidad): aunque Shipment ya filtró, se comprueba dueño e identificador.
    if str(envio.get("cliente_id")) != str(cliente_id) or str(envio.get("id")) != str(envio_id):
        raise _no_encontrado()
    return envio


# ---------- Armado de la vista reducida ----------

def _punto(nombre, lat=None, lon=None) -> dict:
    return {"nombre": nombre, "latitud": lat, "longitud": lon}


def _viaje_y_carga(db: Session, envio_id: uuid.UUID):
    sim = (db.query(SimulacionViaje).filter(SimulacionViaje.envio_id == envio_id)
           .order_by(SimulacionViaje.creado_en.desc()).first())
    if sim is not None:
        return sim, None, None
    carga = _carga_de_envio(db, envio_id)
    if carga is None:
        return None, None, None
    host = db.query(SimulacionViaje).filter(SimulacionViaje.ruta_id == carga.ruta_anfitriona_id).first()
    return None, carga, host


def _subtramo(puntos, desde_km: float, hasta_km: float, total_km: float) -> list[list[float]]:
    """Parte de la polilínea del anfitrión que recorre ESTA carga (de su recogida a su entrega)."""
    if not puntos or len(puntos) < 2 or not total_km or hasta_km <= desde_km:
        return []
    acum = acumulados(puntos)
    largo = acum[-1]
    if largo <= 0:
        return []
    f1 = max(0.0, min(1.0, desde_km / total_km))
    f2 = max(0.0, min(1.0, hasta_km / total_km))
    a, b = f1 * largo, f2 * largo
    salida = [list(posicion_en(puntos, acum, f1))]
    salida += [[float(p[0]), float(p[1])] for p, d in zip(puntos, acum) if a < d < b]
    salida.append(list(posicion_en(puntos, acum, f2)))
    return salida


def _extremos_compartida(carga: CargaCompartida, host: Optional[SimulacionViaje]):
    """Origen y destino de SU tramo (no los del anfitrión)."""
    if host is None or not host.puntos or len(host.puntos) < 2 or not host.distancia_total_km:
        return _punto(carga.origen_nombre), _punto(carga.destino_nombre)
    acum = acumulados(host.puntos)
    total = host.distancia_total_km
    a = posicion_en(host.puntos, acum, carga.km_recogida / total)
    b = posicion_en(host.puntos, acum, carga.km_entrega / total)
    return _punto(carga.origen_nombre, *a), _punto(carga.destino_nombre, *b)


def _posicion(lat, lon, velocidad, actualizada_en, viaje_en_curso: bool, ahora) -> dict:
    disponible = lat is not None and lon is not None
    segundos = None
    if actualizada_en is not None:
        segundos = max(0, int((ahora - actualizada_en).total_seconds()))
    obsoleta = bool(disponible and viaje_en_curso and (segundos is None or segundos > UMBRAL_OBSOLETA_S))
    return {
        "disponible": disponible,
        "latitud": lat, "longitud": lon,
        "velocidad_kmh": velocidad if disponible else None,
        "actualizada_en": actualizada_en if disponible else None,
        "desactualizada": obsoleta,
        "segundos_desde_actualizacion": segundos if disponible else None,
        "umbral_segundos": UMBRAL_OBSOLETA_S,
    }


def _mensaje_propio(sim: SimulacionViaje, en_parada: bool) -> str:
    if sim.estado == "preparando":
        if sim.ultimo_error and sim.ultimo_error.startswith("RETENIDO_ADUANA"):
            return _MENSAJE_SIN_VIAJE["retenido_aduana"]
        return "Preparando el viaje."
    if sim.estado == "pausada":
        return "Detenido por retención en aduana."
    if sim.estado == "en_curso":
        if sim.fase == "reposicion":
            return "El vehículo se dirige al punto de recogida."
        return "Detenido en una parada." if en_parada else "En camino."
    if sim.estado == "finalizada":
        return "Llegó a su destino."
    return "Viaje cancelado."


def _vista_sin_viaje(envio: dict, ahora) -> dict:
    estado = envio.get("estado", "")
    dividido = bool(envio.get("partes_total")) and not envio.get("envio_padre_id")
    mensaje = ("Su pedido se dividió en partes: siga cada parte por separado." if dividido
               else _MENSAJE_SIN_VIAJE.get(estado, "Todavía no hay seguimiento disponible para este envío."))
    return {
        "envio_id": envio["id"], "codigo_envio": envio.get("codigo_envio"), "estado_envio": estado,
        "estado_viaje": "sin_viaje", "mensaje": mensaje, "es_pedido_dividido": dividido,
        "viaja_compartido": False, "trazado_disponible": False,
        "origen": _punto(envio.get("origen")), "destino": _punto(envio.get("destino")),
        "posicion": _posicion(None, None, None, None, False, ahora),
        "progreso": None, "tiempos": None, "parada_actual": None, "paradas": [], "fase": None,
    }


def _vista_propia(envio: dict, sim: SimulacionViaje, ahora) -> dict:
    en_parada = _en_parada(sim, ahora)
    origen, destino = _extremos(sim)
    recorridos = sim.km_recorridos or 0.0
    total = sim.distancia_total_km
    segundos = None
    if sim.estado == "en_curso" and sim.hora_estimada_llegada is not None:
        segundos = max(0, int((sim.hora_estimada_llegada - ahora).total_seconds()))
    en_curso = sim.estado == "en_curso"
    return {
        "envio_id": envio["id"], "codigo_envio": sim.codigo_envio or envio.get("codigo_envio"),
        "estado_envio": envio.get("estado", ""), "estado_viaje": sim.estado,
        "mensaje": _mensaje_propio(sim, en_parada), "es_pedido_dividido": False, "viaja_compartido": False,
        "trazado_disponible": origen is not None,
        "origen": origen or _punto(sim.origen), "destino": destino or _punto(sim.destino),
        "posicion": _posicion(sim.latitud, sim.longitud, sim.velocidad_actual_kmh, sim.ultima_actualizacion, en_curso, ahora),
        "progreso": {"porcentaje": sim.progreso_pct, "km_recorridos": round(recorridos, 2), "km_totales": total,
                     "km_restantes": round(max(0.0, total - recorridos), 2) if total else None},
        "tiempos": {"iniciado_en": sim.iniciado_en, "hora_estimada_llegada": sim.hora_estimada_llegada,
                    "llegada_en": sim.llegada_en, "segundos_restantes": segundos},
        "parada_actual": {"en_parada": en_parada, "motivo": sim.motivo_parada if en_parada else None},
        "paradas": _paradas(sim),
        "fase": sim.fase if sim.estado in ESTADOS_ACTIVOS else None,
    }


def _vista_compartida(envio: dict, carga: CargaCompartida, host: Optional[SimulacionViaje], ahora) -> dict:
    """Envío que viaja en el vehículo de otro: se muestra la posición de ese vehículo y el avance SOLO de su tramo."""
    tramo = max(carga.km_entrega - carga.km_recogida, 0.0)
    km_host = 0.0
    if host is not None:
        km_host = host.distancia_total_km if (host.estado == "finalizada" and carga.estado == "entregada") else (host.km_recorridos or 0.0)
    recorridos = 0.0 if carga.estado == "pendiente" else (tramo if carga.estado == "entregada" else min(max(km_host - carga.km_recogida, 0.0), tramo))
    en_curso = host is not None and host.estado == "en_curso" and carga.estado in ("pendiente", "a_bordo")
    segundos = None
    if carga.estado == "a_bordo" and host is not None and host.estado == "en_curso":
        faltan = max(tramo - recorridos, 0.0)
        segundos = int(faltan / max(host.velocidad_promedio_kmh or 60.0, 1.0) * 3600.0 / simulador.FACTOR_TIEMPO)
    origen, destino = _extremos_compartida(carga, host)
    return {
        "envio_id": envio["id"], "codigo_envio": envio.get("codigo_envio"), "estado_envio": envio.get("estado", ""),
        "estado_viaje": _ESTADO_CARGA.get(carga.estado, carga.estado),
        "mensaje": _MENSAJE_CARGA.get(carga.estado, "En camino."),
        "es_pedido_dividido": False, "viaja_compartido": True, "trazado_disponible": origen["latitud"] is not None,
        "origen": origen, "destino": destino,
        "posicion": _posicion(host.latitud if host else None, host.longitud if host else None,
                              host.velocidad_actual_kmh if host else None, host.ultima_actualizacion if host else None, en_curso, ahora),
        "progreso": {"porcentaje": round(recorridos / tramo * 100, 1) if tramo else 0.0, "km_recorridos": round(recorridos, 2),
                     "km_totales": round(tramo, 2), "km_restantes": round(max(tramo - recorridos, 0.0), 2)},
        "tiempos": {"iniciado_en": carga.iniciado_en, "hora_estimada_llegada": carga.hora_estimada_llegada,
                    "llegada_en": carga.llegada_en, "segundos_restantes": segundos},
        "parada_actual": None, "paradas": [], "fase": None,
    }


def _ajustar_por_estado_del_envio(vista: dict) -> dict:
    """Shipment manda sobre el mensaje cuando el envío ya terminó de otra forma o tiene una incidencia."""
    estado = vista["estado_envio"]
    if estado in ("cancelado", "devuelto"):
        vista["mensaje"] = _MENSAJE_SIN_VIAJE[estado]
    elif estado == "con_incidencia" and vista["estado_viaje"] in ("en_curso", "pausada"):
        vista["mensaje"] = "Hay una incidencia con su envío; un operador la está atendiendo."
    return vista


# ---------- Endpoints ----------

@router.get("/{envio_id}", response_model=SeguimientoClienteLeer)
def seguimiento_del_cliente(envio_id: uuid.UUID, envio: dict = Depends(envio_propio), db: Session = Depends(get_db)):
    """Estado, ubicación y avance de UN envío del cliente autenticado. Sin viaje todavía → 200 con estado_viaje
    "sin_viaje" y la explicación; envío ajeno o inexistente → 404 idéntico."""
    ahora = simulador._ahora()
    sim, carga, host = _viaje_y_carga(db, envio_id)
    if sim is not None:
        vista = _vista_propia(envio, sim, ahora)
    elif carga is not None:
        vista = _vista_compartida(envio, carga, host, ahora)
    else:
        vista = _vista_sin_viaje(envio, ahora)
    return _ajustar_por_estado_del_envio(vista)


@router.get("/{envio_id}/trazado", response_model=TrazadoClienteLeer)
def trazado_del_cliente(envio_id: uuid.UUID, envio: dict = Depends(envio_propio), db: Session = Depends(get_db)):
    """Ruta planificada de SU envío (para dibujarla una sola vez). La posición se refresca con el endpoint liviano."""
    sim, carga, host = _viaje_y_carga(db, envio_id)
    if sim is None and carga is None:
        raise HTTPException(status_code=404, detail={"codigo": "SIN_VIAJE", "mensaje": "El envío aún no tiene un viaje."})
    if sim is not None:
        origen, destino = _extremos(sim)
        if origen is None:
            raise HTTPException(status_code=409, detail={"codigo": "VIAJE_EN_PREPARACION", "mensaje": "El viaje todavía se está preparando; intente en unos segundos."})
        return {"envio_id": envio_id, "codigo_envio": sim.codigo_envio or envio.get("codigo_envio"),
                "distancia_km": sim.distancia_total_km, "origen": origen, "destino": destino,
                "puntos": [[float(p[0]), float(p[1])] for p in sim.puntos], "paradas": _paradas(sim)}
    puntos = _subtramo(host.puntos if host is not None else None, carga.km_recogida, carga.km_entrega,
                       host.distancia_total_km if host is not None else 0.0)
    if not puntos:
        raise HTTPException(status_code=409, detail={"codigo": "VIAJE_EN_PREPARACION", "mensaje": "El viaje todavía se está preparando; intente en unos segundos."})
    origen, destino = _extremos_compartida(carga, host)
    return {"envio_id": envio_id, "codigo_envio": envio.get("codigo_envio"),
            "distancia_km": round(max(carga.km_entrega - carga.km_recogida, 0.0), 2),
            "origen": origen, "destino": destino, "puntos": puntos, "paradas": []}


@router.get("/{envio_id}/recorrido", response_model=list[RecorridoClientePunto])
def recorrido_del_cliente(
    envio_id: uuid.UUID,
    limite: int = Query(500, ge=1, le=2000),
    envio: dict = Depends(envio_propio),
    db: Session = Depends(get_db),
):
    """Estela de SU envío: solo las lecturas desde que el vehículo lleva su carga (nada del tramo en vacío ni de
    viajes anteriores del vehículo)."""
    sim, carga, host = _viaje_y_carga(db, envio_id)
    if sim is not None:
        if sim.iniciado_en is None:        # todavía va en vacío hacia la recogida
            return []
        vehiculo_id, desde = sim.vehiculo_id, sim.iniciado_en
        hasta = sim.llegada_en or (sim.ultima_actualizacion if sim.estado not in ESTADOS_ACTIVOS else None)
    elif carga is not None:
        if carga.iniciado_en is None:      # la carga todavía no subió
            return []
        vehiculo_id, desde = carga.vehiculo_id, carga.iniciado_en
        hasta = carga.llegada_en
        if hasta is None and carga.estado in ("cancelada", "interrumpida") and host is not None:
            hasta = host.ultima_actualizacion
    else:
        return []
    consulta = db.query(LecturaTelemetria).filter(LecturaTelemetria.vehiculo_id == vehiculo_id, LecturaTelemetria.tiempo >= desde)
    if hasta is not None:
        consulta = consulta.filter(LecturaTelemetria.tiempo <= hasta)
    filas = consulta.order_by(desc(LecturaTelemetria.tiempo)).limit(limite).all()
    filas.reverse()
    return filas
