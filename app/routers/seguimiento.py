import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import CargaCompartida, LecturaTelemetria, SimulacionViaje
from ..schemas import RecorridoPunto, SeguimientoLeer, SeguimientoResumen, TrazadoLeer
from ..simulador import ESTADOS_ACTIVOS, FACTOR_TIEMPO

router = APIRouter(prefix="/seguimiento", tags=["Seguimiento"])

ACTIVOS_EN_MAPA = ("en_curso", "pausada")


def _sin_viaje():
    return HTTPException(
        status_code=404,
        detail={
            "codigo": "SIN_VIAJE",
            "mensaje": "El envío aún no tiene un viaje: puede estar pendiente o esperando vehículo.",
        },
    )


def _viaje_de_envio(db: Session, envio_id: uuid.UUID) -> SimulacionViaje:
    sim = (
        db.query(SimulacionViaje)
        .filter(SimulacionViaje.envio_id == envio_id)
        .order_by(SimulacionViaje.creado_en.desc())
        .first()
    )
    if sim is None:
        # Un envío que viaja en el vehículo de otro no tiene viaje propio: se usa el del vehículo anfitrión
        carga = _carga_de_envio(db, envio_id)
        if carga is not None:
            sim = db.query(SimulacionViaje).filter(SimulacionViaje.ruta_id == carga.ruta_anfitriona_id).first()
        if sim is None:
            raise _sin_viaje()
    return sim


def _en_parada(sim: SimulacionViaje, ahora: datetime) -> bool:
    return sim.estado == "en_curso" and sim.en_parada_hasta is not None and sim.en_parada_hasta > ahora


def _mensaje(sim: SimulacionViaje, en_parada: bool) -> str:
    if sim.estado == "preparando":
        if sim.ultimo_error and sim.ultimo_error.startswith("RETENIDO_ADUANA"):
            return "Retenido en aduana: el viaje saldrá cuando se libere"
        return "Preparando el viaje"
    if sim.estado == "pausada":
        return "Detenido por retención en aduana"
    if sim.estado == "en_curso":
        if sim.fase == "reposicion":
            return "Detenido para recargar combustible" if en_parada else "Va en vacío hacia el punto de recogida"
        return "Detenido para recargar combustible" if en_parada else "En camino"
    if sim.estado == "finalizada":
        return "Llegó a su destino"
    return "Viaje cancelado"


def _paradas(sim: SimulacionViaje) -> list[dict]:
    resultado = []
    siguiente = sim.proxima_parada or 0
    for i, p in enumerate(sim.paradas or []):
        if sim.estado == "finalizada" or i < siguiente:
            estado = "pasada"
        elif i == siguiente and sim.estado in ESTADOS_ACTIVOS:
            estado = "proxima"
        else:
            estado = "pendiente"
        resultado.append({
            "nombre": p.get("nombre"),
            "km_desde_origen": p.get("km_desde_origen"),
            "latitud": p.get("latitud"),
            "longitud": p.get("longitud"),
            "estado": estado,
        })
    return resultado


def _extremos(sim: SimulacionViaje):
    puntos = sim.puntos or []
    if len(puntos) < 2:
        return None, None
    origen = {"nombre": sim.origen, "latitud": float(puntos[0][0]), "longitud": float(puntos[0][1])}
    destino = {"nombre": sim.destino, "latitud": float(puntos[-1][0]), "longitud": float(puntos[-1][1])}
    return origen, destino


def _armar(sim: SimulacionViaje) -> dict:
    ahora = datetime.now(timezone.utc)
    en_parada = _en_parada(sim, ahora)
    total = sim.distancia_total_km
    recorridos = sim.km_recorridos or 0.0
    origen, destino = _extremos(sim)
    segundos = None
    if sim.estado == "en_curso" and sim.hora_estimada_llegada is not None:
        segundos = max(0, int((sim.hora_estimada_llegada - ahora).total_seconds()))
    return {
        "envio_id": sim.envio_id,
        "codigo_envio": sim.codigo_envio,
        "codigo_ruta": sim.codigo_ruta,
        "estado_viaje": sim.estado,
        "mensaje": _mensaje(sim, en_parada),
        "factor_tiempo": FACTOR_TIEMPO,
        "vehiculo": {
            "id": sim.vehiculo_id,
            "placa": sim.placa,
            "conductor_id": sim.conductor_id,
            "conductor_nombre": sim.conductor_nombre,
        },
        "origen": origen,
        "destino": destino,
        "posicion": {
            "latitud": sim.latitud,
            "longitud": sim.longitud,
            "velocidad_kmh": sim.velocidad_actual_kmh,
            "actualizada_en": sim.ultima_actualizacion,
        },
        "progreso": {
            "porcentaje": sim.progreso_pct,
            "km_recorridos": round(recorridos, 2),
            "km_totales": total,
            "km_restantes": round(max(0.0, total - recorridos), 2) if total else None,
        },
        "tiempos": {
            "iniciado_en": sim.iniciado_en,
            "hora_estimada_llegada": sim.hora_estimada_llegada,
            "llegada_en": sim.llegada_en,
            "segundos_restantes": segundos,
        },
        "combustible": {
            "nivel_pct": sim.combustible_pct,
            "litros": round(sim.combustible_l, 1) if sim.combustible_l is not None else None,
            "capacidad_l": sim.capacidad_tanque_l,
            "autonomia_km": sim.autonomia_km,
            "recargas": sim.recargas or 0,
            "litros_recargados": round(sim.litros_recargados or 0.0, 1),
            "estimado": bool(sim.combustible_estimado),
        },
        "parada_actual": {
            "en_parada": en_parada,
            "motivo": sim.motivo_parada if en_parada else None,
            "hasta": sim.en_parada_hasta if en_parada else None,
        },
        "siguiente_parada": sim.siguiente_parada if sim.estado in ESTADOS_ACTIVOS else None,
        "paradas": _paradas(sim),
        "fase": sim.fase if sim.estado in ESTADOS_ACTIVOS else None,
        "reposicion": _reposicion(sim),
        "carga_compartida": None,
    }


def _reposicion(sim: SimulacionViaje):
    if not sim.reposicion_datos:
        return None
    datos = sim.reposicion_datos
    return {
        "km_totales": round(sim.reposicion_km or 0.0, 2),
        "km_recorridos": round(sim.km_reposicion or 0.0, 2),
        "fuente": datos.get("fuente"),
        "resuelta": datos.get("resuelta"),
        "motivo": datos.get("motivo"),
        "salio_de": ({"latitud": sim.desde.get("latitud"), "longitud": sim.desde.get("longitud"),
                      "nombre": sim.desde.get("nombre"), "origen_del_dato": sim.desde.get("fuente")}
                     if sim.desde and sim.desde.get("latitud") is not None else None),
    }


_ESTADO_CARGA = {"pendiente": "preparando", "a_bordo": "en_curso", "entregada": "finalizada",
                 "cancelada": "cancelada", "interrumpida": "cancelada"}
_MENSAJE_CARGA = {
    "pendiente": "Esperando que el vehículo llegue al punto de recogida",
    "a_bordo": "En camino, a bordo de un vehículo compartido",
    "entregada": "Llegó a su destino",
    "cancelada": "Viaje cancelado: se reasignará o lo resolverá un operador",
    "interrumpida": "El vehículo no terminó su recorrido con la carga a bordo: requiere atención de un operador",
}


def _armar_compartida(db: Session, fila: CargaCompartida) -> dict:
    """Seguimiento de un envío que viaja en el vehículo de otro: se muestra la posición del vehículo y el avance
    SOLO del tramo que le corresponde a este envío."""
    host = db.query(SimulacionViaje).filter(SimulacionViaje.ruta_id == fila.ruta_anfitriona_id).first()
    tramo = max(fila.km_entrega - fila.km_recogida, 0.0)
    km_host = 0.0
    if host is not None:
        km_host = host.distancia_total_km if (host.estado == "finalizada" and fila.estado == "entregada") else (host.km_recorridos or 0.0)
    recorridos = 0.0 if fila.estado == "pendiente" else (tramo if fila.estado == "entregada" else min(max(km_host - fila.km_recogida, 0.0), tramo))
    segundos = None
    if fila.estado == "a_bordo" and host is not None and host.estado == "en_curso":
        faltan = max(tramo - recorridos, 0.0)
        segundos = int(faltan / max(host.velocidad_promedio_kmh or 60.0, 1.0) * 3600.0 / FACTOR_TIEMPO)
    return {
        "envio_id": fila.envio_id,
        "codigo_envio": None,
        "codigo_ruta": fila.codigo_ruta,
        "estado_viaje": _ESTADO_CARGA.get(fila.estado, fila.estado),
        "mensaje": _MENSAJE_CARGA.get(fila.estado, fila.estado),
        "factor_tiempo": FACTOR_TIEMPO,
        "vehiculo": {"id": fila.vehiculo_id, "placa": fila.placa, "conductor_id": fila.conductor_id,
                     "conductor_nombre": fila.conductor_nombre},
        "origen": None, "destino": None,
        "posicion": {
            "latitud": host.latitud if host is not None else None,
            "longitud": host.longitud if host is not None else None,
            "velocidad_kmh": host.velocidad_actual_kmh if host is not None else None,
            "actualizada_en": host.ultima_actualizacion if host is not None else None,
        },
        "progreso": {"porcentaje": round(recorridos / tramo * 100, 1) if tramo else 0.0, "km_recorridos": round(recorridos, 2),
                     "km_totales": round(tramo, 2), "km_restantes": round(max(tramo - recorridos, 0.0), 2)},
        "tiempos": {"iniciado_en": fila.iniciado_en, "hora_estimada_llegada": fila.hora_estimada_llegada,
                    "llegada_en": fila.llegada_en, "segundos_restantes": segundos},
        "combustible": {"nivel_pct": None, "litros": None, "capacidad_l": None, "autonomia_km": None,
                        "recargas": 0, "litros_recargados": 0.0, "estimado": True},
        "parada_actual": {"en_parada": False, "motivo": None, "hasta": None},
        "siguiente_parada": None,
        "paradas": [],
        "fase": None,
        "reposicion": None,
        "carga_compartida": {
            "estado": fila.estado, "recogida": fila.origen_nombre, "entrega": fila.destino_nombre,
            "km_recogida": fila.km_recogida, "km_entrega": fila.km_entrega, "ruta_anfitriona_id": fila.ruta_anfitriona_id,
        },
    }


def _carga_de_envio(db: Session, envio_id: uuid.UUID):
    return (
        db.query(CargaCompartida)
        .filter(CargaCompartida.envio_id == envio_id)
        .order_by(CargaCompartida.creado_en.desc())
        .first()
    )


@router.get("/flota", response_model=list[SeguimientoResumen])
def flota_en_viaje(db: Session = Depends(get_db)):
    """Todos los vehículos que están en viaje ahora (en camino o detenidos), para un mapa general."""
    return (
        db.query(SimulacionViaje)
        .filter(SimulacionViaje.estado.in_(ACTIVOS_EN_MAPA))
        .order_by(SimulacionViaje.creado_en.asc())
        .all()
    )


@router.get("/codigo/{codigo_envio}", response_model=SeguimientoLeer)
def seguimiento_por_codigo(codigo_envio: str, db: Session = Depends(get_db)):
    sim = (
        db.query(SimulacionViaje)
        .filter(SimulacionViaje.codigo_envio == codigo_envio.strip().upper())
        .order_by(SimulacionViaje.creado_en.desc())
        .first()
    )
    if sim is None:
        raise _sin_viaje()
    return _armar(sim)


@router.get("/vehiculo/{vehiculo_id}", response_model=SeguimientoLeer)
def seguimiento_por_vehiculo(vehiculo_id: uuid.UUID, db: Session = Depends(get_db)):
    sim = (
        db.query(SimulacionViaje)
        .filter(SimulacionViaje.vehiculo_id == vehiculo_id, SimulacionViaje.estado.in_(ESTADOS_ACTIVOS))
        .order_by(SimulacionViaje.creado_en.desc())
        .first()
    )
    if sim is None:
        raise HTTPException(
            status_code=404,
            detail={"codigo": "SIN_VIAJE_ACTIVO", "mensaje": "Ese vehículo no está en un viaje ahora mismo."},
        )
    return _armar(sim)


@router.get("/envio/{envio_id}", response_model=SeguimientoLeer)
def seguimiento_de_envio(envio_id: uuid.UUID, db: Session = Depends(get_db)):
    sim = db.query(SimulacionViaje).filter(SimulacionViaje.envio_id == envio_id).order_by(SimulacionViaje.creado_en.desc()).first()
    if sim is None:
        carga = _carga_de_envio(db, envio_id)
        if carga is not None:
            return _armar_compartida(db, carga)
    return _armar(_viaje_de_envio(db, envio_id))


@router.get("/envio/{envio_id}/trazado", response_model=TrazadoLeer)
def trazado_del_envio(envio_id: uuid.UUID, db: Session = Depends(get_db)):
    """Trazado completo de la ruta: se pide una sola vez y se dibuja; la posición se actualiza con el endpoint liviano."""
    sim = _viaje_de_envio(db, envio_id)
    origen, destino = _extremos(sim)
    if origen is None:
        raise HTTPException(
            status_code=409,
            detail={"codigo": "VIAJE_EN_PREPARACION", "mensaje": "El viaje todavía se está preparando; intente en unos segundos."},
        )
    carga = _carga_de_envio(db, envio_id) if sim.envio_id != envio_id else None
    return {
        "envio_id": envio_id,
        "codigo_envio": sim.codigo_envio if carga is None else None,
        "codigo_ruta": sim.codigo_ruta,
        "distancia_km": sim.distancia_total_km if carga is None else round(carga.km_entrega - carga.km_recogida, 2),
        "origen": origen,
        "destino": destino,
        "puntos": [[float(p[0]), float(p[1])] for p in sim.puntos],
        "paradas": _paradas(sim),
    }


@router.get("/envio/{envio_id}/recorrido", response_model=list[RecorridoPunto])
def recorrido_del_envio(
    envio_id: uuid.UUID,
    limite: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    """Lecturas ya registradas del viaje (de más antigua a más reciente), para dibujar la estela del vehículo."""
    sim = _viaje_de_envio(db, envio_id)
    if sim.iniciado_en is None:
        return []
    consulta = db.query(LecturaTelemetria).filter(
        LecturaTelemetria.vehiculo_id == sim.vehiculo_id,
        LecturaTelemetria.tiempo >= sim.iniciado_en,
    )
    fin = sim.llegada_en or (sim.ultima_actualizacion if sim.estado not in ESTADOS_ACTIVOS else None)
    if fin is not None:
        consulta = consulta.filter(LecturaTelemetria.tiempo <= fin)
    filas = consulta.order_by(desc(LecturaTelemetria.tiempo)).limit(limite).all()
    filas.reverse()
    return filas