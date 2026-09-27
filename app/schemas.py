import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, ConfigDict


class LecturaTelemetriaCrear(BaseModel):
    vehiculo_id: uuid.UUID
    tiempo: Optional[datetime] = None
    latitud: Decimal
    longitud: Decimal
    velocidad_kmh: Optional[Decimal] = None
    nivel_combustible_pct: Optional[Decimal] = None
    temperatura_carga_c: Optional[Decimal] = None
    kilometraje_acumulado_km: Optional[Decimal] = None
    horas_motor: Optional[Decimal] = None
    codigo_obd2: Optional[str] = None
    temperatura_motor_c: Optional[Decimal] = None


class LecturaTelemetriaLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tiempo: datetime
    vehiculo_id: uuid.UUID
    latitud: Decimal
    longitud: Decimal
    velocidad_kmh: Optional[Decimal] = None
    nivel_combustible_pct: Optional[Decimal] = None
    temperatura_carga_c: Optional[Decimal] = None
    kilometraje_acumulado_km: Optional[Decimal] = None
    horas_motor: Optional[Decimal] = None
    codigo_obd2: Optional[str] = None
    temperatura_motor_c: Optional[Decimal] = None