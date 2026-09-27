from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from ..database import get_db
from ..events import publish_event
from ..models import LecturaTelemetria
from ..schemas import LecturaTelemetriaCrear, LecturaTelemetriaLeer

router = APIRouter(prefix="/telemetria", tags=["Telemetria"])


@router.post("/", response_model=LecturaTelemetriaLeer, status_code=201)
async def crear_lectura(payload: LecturaTelemetriaCrear, db: Session = Depends(get_db)):
    tiempo = payload.tiempo or datetime.now(timezone.utc)

    lectura = LecturaTelemetria(
        tiempo=tiempo,
        vehiculo_id=payload.vehiculo_id,
        latitud=payload.latitud,
        longitud=payload.longitud,
        velocidad_kmh=payload.velocidad_kmh,
        nivel_combustible_pct=payload.nivel_combustible_pct,
        temperatura_carga_c=payload.temperatura_carga_c,
        kilometraje_acumulado_km=payload.kilometraje_acumulado_km,
        horas_motor=payload.horas_motor,
        codigo_obd2=payload.codigo_obd2,
        temperatura_motor_c=payload.temperatura_motor_c,
    )
    db.add(lectura)
    db.commit()
    db.refresh(lectura)

    await publish_event("telemetry.raw", {
        "vehiculo_id": str(lectura.vehiculo_id),
        "tiempo": lectura.tiempo,
        "latitud": lectura.latitud,
        "longitud": lectura.longitud,
        "velocidad_kmh": lectura.velocidad_kmh,
        "nivel_combustible_pct": lectura.nivel_combustible_pct,
        "temperatura_carga_c": lectura.temperatura_carga_c,
        "kilometraje_acumulado_km": lectura.kilometraje_acumulado_km,
        "horas_motor": lectura.horas_motor,
        "codigo_obd2": lectura.codigo_obd2,
        "temperatura_motor_c": lectura.temperatura_motor_c,
    })

    return lectura


@router.get("/{vehiculo_id}", response_model=List[LecturaTelemetriaLeer])
def listar_lecturas(
    vehiculo_id: UUID,
    desde: Optional[datetime] = Query(None),
    hasta: Optional[datetime] = Query(None),
    limite: int = Query(100, le=1000),
    db: Session = Depends(get_db),
):
    query = db.query(LecturaTelemetria).filter(LecturaTelemetria.vehiculo_id == vehiculo_id)
    if desde:
        query = query.filter(LecturaTelemetria.tiempo >= desde)
    if hasta:
        query = query.filter(LecturaTelemetria.tiempo <= hasta)
    return query.order_by(desc(LecturaTelemetria.tiempo)).limit(limite).all()