import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import SimulacionEvento, SimulacionViaje
from ..schemas import SimulacionEventoLeer, SimulacionLeer

router = APIRouter(prefix="/simulaciones", tags=["Simulaciones"])


def _simulacion_del_envio(db: Session, envio_id: uuid.UUID) -> SimulacionViaje:
    sim = (
        db.query(SimulacionViaje)
        .filter(SimulacionViaje.envio_id == envio_id)
        .order_by(SimulacionViaje.creado_en.desc())
        .first()
    )
    if sim is None:
        raise HTTPException(status_code=404, detail="Ese envío no tiene un viaje simulado")
    return sim


@router.get("/", response_model=list[SimulacionLeer])
def listar_simulaciones(
    estado: Optional[str] = None,
    limite: int = Query(50, le=200),
    db: Session = Depends(get_db),
):
    query = db.query(SimulacionViaje)
    if estado:
        query = query.filter(SimulacionViaje.estado == estado.strip().lower())
    return query.order_by(SimulacionViaje.creado_en.desc()).limit(limite).all()


@router.get("/envio/{envio_id}", response_model=SimulacionLeer)
def obtener_simulacion_de_envio(envio_id: uuid.UUID, db: Session = Depends(get_db)):
    return _simulacion_del_envio(db, envio_id)


@router.get("/envio/{envio_id}/eventos", response_model=list[SimulacionEventoLeer])
def eventos_del_viaje(envio_id: uuid.UUID, db: Session = Depends(get_db)):
    sim = _simulacion_del_envio(db, envio_id)
    return (
        db.query(SimulacionEvento)
        .filter(SimulacionEvento.simulacion_id == sim.id)
        .order_by(SimulacionEvento.tiempo.asc())
        .all()
    )