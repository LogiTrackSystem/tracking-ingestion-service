from sqlalchemy import Column, BigInteger, Numeric, DateTime, Text, PrimaryKeyConstraint, Identity
from sqlalchemy.dialects.postgresql import UUID
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