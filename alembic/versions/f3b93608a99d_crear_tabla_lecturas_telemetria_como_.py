
"""crear tabla lecturas_telemetria como tabla PostgreSQL normal

Revision ID: f3b93608a99d
Revises:
Create Date: 2026-09-27 09:49:48.957759
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# Identificadores de la migración
revision: str = "f3b93608a99d"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Crear tabla de lecturas de telemetría."""

    op.create_table(
        "lecturas_telemetria",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("tiempo", sa.DateTime(timezone=True), nullable=False),
        sa.Column("vehiculo_id", sa.UUID(), nullable=False),
        sa.Column("latitud", sa.Numeric(precision=9, scale=6), nullable=False),
        sa.Column("longitud", sa.Numeric(precision=9, scale=6), nullable=False),
        sa.Column("velocidad_kmh", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("nivel_combustible_pct", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("temperatura_carga_c", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("kilometraje_acumulado_km", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("horas_motor", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("codigo_obd2", sa.Text(), nullable=True),
        sa.Column("temperatura_motor_c", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.PrimaryKeyConstraint("id", "tiempo"),
    )

    op.create_index(
        op.f("ix_lecturas_telemetria_vehiculo_id"),
        "lecturas_telemetria",
        ["vehiculo_id"],
        unique=False,
    )


def downgrade() -> None:
    """Eliminar tabla de lecturas de telemetría."""

    op.drop_index(
        op.f("ix_lecturas_telemetria_vehiculo_id"),
        table_name="lecturas_telemetria",
    )
    op.drop_table("lecturas_telemetria")
