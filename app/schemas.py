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
    fase: Optional[str] = None                    # "reposicion" (va vacío al origen) | "viaje" (lleva la carga)
    reposicion: Optional[dict] = None             # tramo en vacío: km totales/recorridos y de dónde salió
    carga_compartida: Optional[dict] = None       # si el envío viaja en el vehículo de otro: tramo y estado


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


# ---------- Seguimiento para la cuenta de CLIENTE ----------
# Mismos nombres que el contrato del mapa del personal (posicion, progreso, tiempos, origen, destino, paradas, fase)
# para reutilizar el mismo componente, pero SIN vehículo (placa/conductor), combustible, flota ni datos internos.

class ClientePunto(BaseModel):
    nombre: Optional[str] = None
    latitud: Optional[float] = None
    longitud: Optional[float] = None


class ClientePosicion(BaseModel):
    disponible: bool                                  # False = todavía no hay ninguna ubicación real que mostrar
    latitud: Optional[float] = None
    longitud: Optional[float] = None
    velocidad_kmh: Optional[float] = None
    actualizada_en: Optional[datetime] = None
    desactualizada: bool = False                      # el viaje sigue en curso pero hace más de umbral_segundos que no llega una posición
    segundos_desde_actualizacion: Optional[int] = None
    umbral_segundos: int


class ClienteProgreso(BaseModel):
    porcentaje: float
    km_recorridos: float
    km_totales: Optional[float] = None
    km_restantes: Optional[float] = None


class ClienteTiempos(BaseModel):
    iniciado_en: Optional[datetime] = None
    hora_estimada_llegada: Optional[datetime] = None
    llegada_en: Optional[datetime] = None
    segundos_restantes: Optional[int] = None


class ClienteParadaActual(BaseModel):
    en_parada: bool
    motivo: Optional[str] = None


class ClienteParada(BaseModel):
    nombre: Optional[str] = None
    km_desde_origen: float
    latitud: Optional[float] = None
    longitud: Optional[float] = None
    estado: str  # pasada | proxima | pendiente


class SeguimientoClienteLeer(BaseModel):
    envio_id: uuid.UUID
    codigo_envio: Optional[str] = None
    estado_envio: str                                 # estado del envío en Shipment (pendiente, asignado, en_transito, ...)
    estado_viaje: str                                 # sin_viaje | preparando | en_curso | pausada | finalizada | cancelada
    mensaje: str
    es_pedido_dividido: bool = False                  # pedido con varias partes: se sigue cada parte por su propio id
    viaja_compartido: bool = False
    trazado_disponible: bool = False                  # ya se puede pedir /trazado
    origen: Optional[ClientePunto] = None
    destino: Optional[ClientePunto] = None
    posicion: ClientePosicion
    progreso: Optional[ClienteProgreso] = None
    tiempos: Optional[ClienteTiempos] = None
    parada_actual: Optional[ClienteParadaActual] = None
    paradas: list[ClienteParada] = []
    fase: Optional[str] = None                        # "reposicion" (va hacia el punto de recogida) | "viaje" (lleva la carga)


class TrazadoClienteLeer(BaseModel):
    envio_id: uuid.UUID
    codigo_envio: Optional[str] = None
    distancia_km: Optional[float] = None
    origen: ClientePunto
    destino: ClientePunto
    puntos: list[list[float]]
    paradas: list[ClienteParada] = []


class RecorridoClientePunto(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tiempo: datetime
    latitud: float
    longitud: float
