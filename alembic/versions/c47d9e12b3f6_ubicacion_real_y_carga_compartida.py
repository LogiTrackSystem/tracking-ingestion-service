"""tramo en vacío, ubicación real y cargas compartidas

Revision ID: c47d9e12b3f6
Revises: a8f2d6c1e407
Create Date: 2026-10-21 11:00:00.000000

Solo agrega columnas con valor por defecto y una tabla nueva. Las simulaciones ya existentes quedan marcadas con la
ubicación "informada" para que no se reporte a Fleet un viaje anterior a esta versión.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'c47d9e12b3f6'
down_revision: Union[str, Sequence[str], None] = 'a8f2d6c1e407'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    t = "simulaciones_viaje"
    op.add_column(t, sa.Column("reposicion_km", sa.Float(), server_default=sa.text("0"), nullable=False))
    op.add_column(t, sa.Column("km_reposicion", sa.Float(), server_default=sa.text("0"), nullable=False))
    for nombre in ("reposicion_datos", "puntos_reposicion", "desde", "origen_info", "destino_info"):
        op.add_column(t, sa.Column(nombre, postgresql.JSONB(), nullable=True))
    op.add_column(t, sa.Column("cierre_ubicacion", sa.String(length=15), nullable=True))
    op.add_column(t, sa.Column("ubicacion_informada", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column(t, sa.Column("intentos_ubicacion", sa.Integer(), server_default=sa.text("0"), nullable=False))
    op.execute("UPDATE simulaciones_viaje SET ubicacion_informada = true")

    op.create_table(
        "cargas_compartidas",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("envio_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ruta_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ruta_anfitriona_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vehiculo_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conductor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("placa", sa.String(length=20), nullable=True),
        sa.Column("conductor_nombre", sa.String(length=150), nullable=True),
        sa.Column("codigo_ruta", sa.String(length=12), nullable=True),
        sa.Column("origen_nombre", sa.String(length=255), nullable=True),
        sa.Column("destino_nombre", sa.String(length=255), nullable=True),
        sa.Column("km_recogida", sa.Float(), nullable=False),
        sa.Column("km_entrega", sa.Float(), nullable=False),
        sa.Column("estado", sa.String(length=20), server_default="pendiente", nullable=False),
        sa.Column("inicio_publicado", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("llegada_publicada", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("reencolar_pendiente", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("iniciado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("llegada_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hora_estimada_llegada", sa.DateTime(timezone=True), nullable=True),
        sa.Column("creado_en", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("actualizado_en", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ruta_id"),
    )
    op.create_index("ix_cargas_compartidas_envio_id", "cargas_compartidas", ["envio_id"])
    op.create_index("ix_cargas_compartidas_ruta_anfitriona_id", "cargas_compartidas", ["ruta_anfitriona_id"])
    op.create_index("ix_cargas_compartidas_vehiculo_id", "cargas_compartidas", ["vehiculo_id"])
    op.create_index("ix_cargas_compartidas_estado", "cargas_compartidas", ["estado"])


def downgrade() -> None:
    op.drop_table("cargas_compartidas")
    t = "simulaciones_viaje"
    for nombre in ("intentos_ubicacion", "ubicacion_informada", "cierre_ubicacion", "destino_info", "origen_info",
                   "desde", "puntos_reposicion", "reposicion_datos", "km_reposicion", "reposicion_km"):
        op.drop_column(t, nombre)
