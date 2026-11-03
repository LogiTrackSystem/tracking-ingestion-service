import uuid

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, Float, ForeignKey, Identity, Index,
    Integer, Numeric, PrimaryKeyConstraint, String, Text, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from .database import Base


class LecturaTelemetria(Base):
    __tablename__ = "lecturas_telemetria"

    id = Column(BigInteger, Identity(), nullable=False)
    tiempo = Column(DateTime(timezone=True), nullable=False)
    vehiculo_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    latitud = Column(Numeric(9, 6), nullable=False)
    longitud = Column(Numeric(9, 6), nullable=False)
    velocidad_kmh = Column(Numeric(6, 2), nullable=True)
    nivel_combustible_pct = Column(Numeric(5, 2), nullable=True)
    temperatura_carga_c = Column(Numeric(5, 2), nullable=True)
    kilometraje_acumulado_km = Column(Numeric(10, 2), nullable=True)
    horas_motor = Column(Numeric(10, 2), nullable=True)
    codigo_obd2 = Column(Text, nullable=True)
    temperatura_motor_c = Column(Numeric(5, 2), nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("id", "tiempo"),
    )


class SimulacionViaje(Base):
    """Estado de un viaje simulado: un envío asignado que se desplaza por su ruta."""
    __tablename__ = "simulaciones_viaje"
    __table_args__ = (
        Index(
            "uq_simulacion_envio_activa", "envio_id", unique=True,
            postgresql_where=text("estado IN ('preparando', 'en_curso', 'pausada')"),
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    envio_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    ruta_id = Column(UUID(as_uuid=True), nullable=False, unique=True)
    vehiculo_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    conductor_id = Column(UUID(as_uuid=True), nullable=True)
    estado = Column(String(20), nullable=False, server_default="preparando", index=True)

    # Datos copiados al preparar el viaje
    codigo_envio = Column(String(20), nullable=True)  # incluye códigos de partes: ENV-00000032-P1
    origen = Column(String(255), nullable=True)
    destino = Column(String(255), nullable=True)
    tipo_carga = Column(String(20), nullable=True)
    placa = Column(String(20), nullable=True)
    conductor_nombre = Column(String(150), nullable=True)
    codigo_ruta = Column(String(12), nullable=True)
    ruta_maestra_id = Column(UUID(as_uuid=True), nullable=True)
    puntos = Column(JSONB, nullable=True)
    paradas = Column(JSONB, nullable=True)
    distancia_total_km = Column(Float, nullable=True)
    duracion_horas = Column(Float, nullable=True)
    velocidad_promedio_kmh = Column(Float, nullable=True)

    # Combustible
    capacidad_tanque_l = Column(Float, nullable=True)
    consumo_km_por_litro = Column(Float, nullable=True)
    tipo_combustible = Column(String(20), nullable=True)
    combustible_estimado = Column(Boolean, nullable=False, server_default=text("false"))
    combustible_l = Column(Float, nullable=True)
    litros_recargados = Column(Float, nullable=False, server_default=text("0"))
    recargas = Column(Integer, nullable=False, server_default=text("0"))

    # Progreso
    km_recorridos = Column(Float, nullable=False, server_default=text("0"))
    proxima_parada = Column(Integer, nullable=False, server_default=text("0"))
    odometro_inicio_km = Column(Float, nullable=True)
    horas_motor = Column(Float, nullable=True)
    latitud = Column(Float, nullable=True)
    longitud = Column(Float, nullable=True)
    velocidad_actual_kmh = Column(Float, nullable=True)
    en_parada_hasta = Column(DateTime(timezone=True), nullable=True)
    motivo_parada = Column(String(30), nullable=True)

    # Tramo en vacío (reposicionamiento): el vehículo sale de donde estaba, no del origen del envío
    reposicion_km = Column(Float, nullable=False, server_default=text("0"))       # distancia del tramo en vacío
    km_reposicion = Column(Float, nullable=False, server_default=text("0"))       # lo que lleva recorrido de ese tramo
    reposicion_datos = Column(JSONB, nullable=True)       # lo que informó Routing (distancia, fuente, motivo, geometría)
    puntos_reposicion = Column(JSONB, nullable=True)      # trazado [lat, lon] del tramo en vacío
    desde = Column(JSONB, nullable=True)                  # dónde estaba el vehículo al reservarlo (según Fleet)
    origen_info = Column(JSONB, nullable=True)            # punto de origen/destino del envío (coordenadas y país)
    destino_info = Column(JSONB, nullable=True)
    # Última ubicación real: se informa a Fleet al llegar (entrega) o al interrumpirse (interrupcion / restaurar)
    cierre_ubicacion = Column(String(15), nullable=True)
    ubicacion_informada = Column(Boolean, nullable=False, server_default=text("false"))
    intentos_ubicacion = Column(Integer, nullable=False, server_default=text("0"))

    # Tiempos y control
    iniciado_en = Column(DateTime(timezone=True), nullable=True)
    ultima_actualizacion = Column(DateTime(timezone=True), nullable=True)
    hora_estimada_llegada = Column(DateTime(timezone=True), nullable=True)
    llegada_en = Column(DateTime(timezone=True), nullable=True)
    inicio_publicado = Column(Boolean, nullable=False, server_default=text("false"))
    llegada_publicada = Column(Boolean, nullable=False, server_default=text("false"))
    intentos = Column(Integer, nullable=False, server_default=text("0"))
    ultimo_error = Column(Text, nullable=True)
    creado_en = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    actualizado_en = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    @property
    def fase(self):
        """"reposicion" mientras el vehículo va vacío hacia el origen; "viaje" cuando ya lleva la carga."""
        return "reposicion" if (self.reposicion_km or 0.0) - (self.km_reposicion or 0.0) > 1e-6 else "viaje"

    @property
    def combustible_pct(self):
        if not self.capacidad_tanque_l or self.combustible_l is None:
            return None
        return round(self.combustible_l / self.capacidad_tanque_l * 100, 1)

    @property
    def progreso_pct(self):
        if not self.distancia_total_km:
            return 0.0
        return round(min(100.0, (self.km_recorridos or 0.0) / self.distancia_total_km * 100), 1)

    @property
    def autonomia_km(self):
        if self.combustible_l is None or not self.consumo_km_por_litro:
            return None
        return round(self.combustible_l * self.consumo_km_por_litro, 1)

    @property
    def siguiente_parada(self):
        paradas = self.paradas or []
        indice = self.proxima_parada or 0
        if indice < len(paradas):
            p = paradas[indice]
            return {"nombre": p.get("nombre"), "km_desde_origen": p.get("km_desde_origen")}
        return None


class CargaCompartida(Base):
    """Un envío que viaja en el vehículo de OTRO envío (carga compartida). No tiene simulación propia: sube y baja
    según el kilómetro que recorre la simulación del vehículo anfitrión."""
    __tablename__ = "cargas_compartidas"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    envio_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    ruta_id = Column(UUID(as_uuid=True), nullable=False, unique=True)
    ruta_anfitriona_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    vehiculo_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    conductor_id = Column(UUID(as_uuid=True), nullable=True)
    placa = Column(String(20), nullable=True)
    conductor_nombre = Column(String(150), nullable=True)
    codigo_ruta = Column(String(12), nullable=True)
    origen_nombre = Column(String(255), nullable=True)
    destino_nombre = Column(String(255), nullable=True)
    km_recogida = Column(Float, nullable=False)
    km_entrega = Column(Float, nullable=False)
    estado = Column(String(20), nullable=False, server_default="pendiente", index=True)  # pendiente|a_bordo|entregada|cancelada|interrumpida
    inicio_publicado = Column(Boolean, nullable=False, server_default=text("false"))
    llegada_publicada = Column(Boolean, nullable=False, server_default=text("false"))
    # La carga no llegó a subir (el vehículo ya había pasado la recogida o terminó antes): hay que avisarle a Routing
    # con tracking.shared_load_missed para que la reasigne a otro vehículo.
    reencolar_pendiente = Column(Boolean, nullable=False, server_default=text("false"))
    iniciado_en = Column(DateTime(timezone=True), nullable=True)
    llegada_en = Column(DateTime(timezone=True), nullable=True)
    hora_estimada_llegada = Column(DateTime(timezone=True), nullable=True)
    creado_en = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    actualizado_en = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class SimulacionEvento(Base):
    """Bitácora del viaje: inicio, paradas, recargas, pausas, llegada."""
    __tablename__ = "simulacion_eventos"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    simulacion_id = Column(UUID(as_uuid=True), ForeignKey("simulaciones_viaje.id"), nullable=False, index=True)
    envio_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    tipo = Column(String(30), nullable=False)
    descripcion = Column(Text, nullable=True)
    km = Column(Float, nullable=True)
    combustible_pct = Column(Float, nullable=True)
    latitud = Column(Float, nullable=True)
    longitud = Column(Float, nullable=True)
    tiempo = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)