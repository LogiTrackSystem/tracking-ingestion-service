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

    

class SimulacionLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    envio_id: uuid.UUID
    codigo_envio: Optional[str] = None
    ruta_id: uuid.UUID
    vehiculo_id: uuid.UUID
    conductor_id: Optional[uuid.UUID] = None
    placa: Optional[str] = None
    conductor_nombre: Optional[str] = None
    origen: Optional[str] = None
    destino: Optional[str] = None
    tipo_carga: Optional[str] = None
    codigo_ruta: Optional[str] = None
    estado: str
    progreso_pct: float
    km_recorridos: float
    distancia_total_km: Optional[float] = None
    duracion_horas: Optional[float] = None
    velocidad_actual_kmh: Optional[float] = None
    velocidad_promedio_kmh: Optional[float] = None
    latitud: Optional[float] = None
    longitud: Optional[float] = None
    combustible_l: Optional[float] = None
    combustible_pct: Optional[float] = None
    capacidad_tanque_l: Optional[float] = None
    consumo_km_por_litro: Optional[float] = None
    autonomia_km: Optional[float] = None
    combustible_estimado: bool
    litros_recargados: float
    recargas: int
    siguiente_parada: Optional[dict] = None
    paradas: Optional[list[dict]] = None
    en_parada_hasta: Optional[datetime] = None
    motivo_parada: Optional[str] = None
    iniciado_en: Optional[datetime] = None
    hora_estimada_llegada: Optional[datetime] = None
    llegada_en: Optional[datetime] = None
    ultima_actualizacion: Optional[datetime] = None
    intentos: int
    ultimo_error: Optional[str] = None


class SimulacionEventoLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tipo: str
    descripcion: Optional[str] = None
    km: Optional[float] = None
    combustible_pct: Optional[float] = None
    latitud: Optional[float] = None
    longitud: Optional[float] = None
    tiempo: datetime