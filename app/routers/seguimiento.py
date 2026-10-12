import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import LecturaTelemetria, SimulacionViaje
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
    }


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
    return {
        "envio_id": sim.envio_id,
        "codigo_envio": sim.codigo_envio,
        "codigo_ruta": sim.codigo_ruta,
        "distancia_km": sim.distancia_total_km,
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