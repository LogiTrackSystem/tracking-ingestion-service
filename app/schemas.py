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



# ---------- Seguimiento (contrato para el mapa del frontend) ----------

class PuntoGeo(BaseModel):
    nombre: Optional[str] = None
    latitud: float
    longitud: float


class SeguimientoParada(BaseModel):
    nombre: Optional[str] = None
    km_desde_origen: float
    latitud: Optional[float] = None
    longitud: Optional[float] = None
    estado: str  # pasada | proxima | pendiente


class SeguimientoVehiculo(BaseModel):
    id: uuid.UUID
    placa: Optional[str] = None
    conductor_id: Optional[uuid.UUID] = None
    conductor_nombre: Optional[str] = None


class SeguimientoPosicion(BaseModel):
    latitud: Optional[float] = None
    longitud: Optional[float] = None
    velocidad_kmh: Optional[float] = None
    actualizada_en: Optional[datetime] = None


class SeguimientoProgreso(BaseModel):
    porcentaje: float
    km_recorridos: float
    km_totales: Optional[float] = None
    km_restantes: Optional[float] = None


class SeguimientoTiempos(BaseModel):
    iniciado_en: Optional[datetime] = None
    hora_estimada_llegada: Optional[datetime] = None
    llegada_en: Optional[datetime] = None
    segundos_restantes: Optional[int] = None


class SeguimientoCombustible(BaseModel):
    nivel_pct: Optional[float] = None
    litros: Optional[float] = None
    capacidad_l: Optional[float] = None
    autonomia_km: Optional[float] = None
    recargas: int
    litros_recargados: float
    estimado: bool


class SeguimientoParadaActual(BaseModel):
    en_parada: bool
    motivo: Optional[str] = None
    hasta: Optional[datetime] = None


class SeguimientoLeer(BaseModel):
    envio_id: uuid.UUID
    codigo_envio: Optional[str] = None
    codigo_ruta: Optional[str] = None
    estado_viaje: str
    mensaje: str
    factor_tiempo: float
    vehiculo: SeguimientoVehiculo
    origen: Optional[PuntoGeo] = None
    destino: Optional[PuntoGeo] = None
    posicion: SeguimientoPosicion
    progreso: SeguimientoProgreso
    tiempos: SeguimientoTiempos
    combustible: SeguimientoCombustible
    parada_actual: SeguimientoParadaActual
    siguiente_parada: Optional[dict] = None
    paradas: list[SeguimientoParada] = []


class SeguimientoResumen(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    envio_id: uuid.UUID
    codigo_envio: Optional[str] = None
    vehiculo_id: uuid.UUID
    placa: Optional[str] = None
    estado: str
    origen: Optional[str] = None
    destino: Optional[str] = None
    latitud: Optional[float] = None
    longitud: Optional[float] = None
    velocidad_actual_kmh: Optional[float] = None
    progreso_pct: float
    combustible_pct: Optional[float] = None
    hora_estimada_llegada: Optional[datetime] = None


class TrazadoLeer(BaseModel):
    envio_id: uuid.UUID
    codigo_envio: Optional[str] = None
    codigo_ruta: Optional[str] = None
    distancia_km: Optional[float] = None
    origen: PuntoGeo
    destino: PuntoGeo
    puntos: list[list[float]]
    paradas: list[SeguimientoParada] = []


class RecorridoPunto(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tiempo: datetime
    latitud: float
    longitud: float
    velocidad_kmh: Optional[float] = None
    nivel_combustible_pct: Optional[float] = None
    kilometraje_acumulado_km: Optional[float] = None