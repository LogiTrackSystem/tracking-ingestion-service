"""simulaciones de viaje

Revision ID: e93dda63e26f
Revises: f3b93608a99d
Create Date: 2026-10-11 18:56:26.249161

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e93dda63e26f'
down_revision: Union[str, Sequence[str], None] = 'f3b93608a99d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "simulaciones_viaje",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("envio_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ruta_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vehiculo_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conductor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("estado", sa.String(length=20), server_default="preparando", nullable=False),
        sa.Column("codigo_envio", sa.String(length=12), nullable=True),
        sa.Column("origen", sa.String(length=255), nullable=True),
        sa.Column("destino", sa.String(length=255), nullable=True),
        sa.Column("tipo_carga", sa.String(length=20), nullable=True),
        sa.Column("placa", sa.String(length=20), nullable=True),
        sa.Column("conductor_nombre", sa.String(length=150), nullable=True),
        sa.Column("codigo_ruta", sa.String(length=12), nullable=True),
        sa.Column("ruta_maestra_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("puntos", postgresql.JSONB(), nullable=True),
        sa.Column("paradas", postgresql.JSONB(), nullable=True),
        sa.Column("distancia_total_km", sa.Float(), nullable=True),
        sa.Column("duracion_horas", sa.Float(), nullable=True),
        sa.Column("velocidad_promedio_kmh", sa.Float(), nullable=True),
        sa.Column("capacidad_tanque_l", sa.Float(), nullable=True),
        sa.Column("consumo_km_por_litro", sa.Float(), nullable=True),
        sa.Column("tipo_combustible", sa.String(length=20), nullable=True),
        sa.Column("combustible_estimado", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("combustible_l", sa.Float(), nullable=True),
        sa.Column("litros_recargados", sa.Float(), server_default=sa.text("0"), nullable=False),
        sa.Column("recargas", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("km_recorridos", sa.Float(), server_default=sa.text("0"), nullable=False),
        sa.Column("proxima_parada", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("odometro_inicio_km", sa.Float(), nullable=True),
        sa.Column("horas_motor", sa.Float(), nullable=True),
        sa.Column("latitud", sa.Float(), nullable=True),
        sa.Column("longitud", sa.Float(), nullable=True),
        sa.Column("velocidad_actual_kmh", sa.Float(), nullable=True),
        sa.Column("en_parada_hasta", sa.DateTime(timezone=True), nullable=True),
        sa.Column("motivo_parada", sa.String(length=30), nullable=True),
        sa.Column("iniciado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ultima_actualizacion", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hora_estimada_llegada", sa.DateTime(timezone=True), nullable=True),
        sa.Column("llegada_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("inicio_publicado", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("llegada_publicada", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("intentos", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("ultimo_error", sa.Text(), nullable=True),
        sa.Column("creado_en", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("actualizado_en", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ruta_id"),
    )
    op.create_index("ix_simulaciones_viaje_envio_id", "simulaciones_viaje", ["envio_id"])
    op.create_index("ix_simulaciones_viaje_vehiculo_id", "simulaciones_viaje", ["vehiculo_id"])
    op.create_index("ix_simulaciones_viaje_estado", "simulaciones_viaje", ["estado"])
    op.create_index(
        "uq_simulacion_envio_activa", "simulaciones_viaje", ["envio_id"], unique=True,
        postgresql_where=sa.text("estado IN ('preparando', 'en_curso', 'pausada')"),
    )

    op.create_table(
        "simulacion_eventos",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("simulacion_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("envio_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tipo", sa.String(length=30), nullable=False),
        sa.Column("descripcion", sa.Text(), nullable=True),
        sa.Column("km", sa.Float(), nullable=True),
        sa.Column("combustible_pct", sa.Float(), nullable=True),
        sa.Column("latitud", sa.Float(), nullable=True),
        sa.Column("longitud", sa.Float(), nullable=True),
        sa.Column("tiempo", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["simulacion_id"], ["simulaciones_viaje.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_simulacion_eventos_simulacion_id", "simulacion_eventos", ["simulacion_id"])
    op.create_index("ix_simulacion_eventos_envio_id", "simulacion_eventos", ["envio_id"])


def downgrade() -> None:
    op.drop_table("simulacion_eventos")
    op.drop_index("uq_simulacion_envio_activa", table_name="simulaciones_viaje")
    op.drop_index("ix_simulaciones_viaje_estado", table_name="simulaciones_viaje")
    op.drop_index("ix_simulaciones_viaje_vehiculo_id", table_name="simulaciones_viaje")
    op.drop_index("ix_simulaciones_viaje_envio_id", table_name="simulaciones_viaje")
    op.drop_table("simulaciones_viaje")