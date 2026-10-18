"""simulaciones_viaje.codigo_envio a 20 caracteres (códigos de partes de pedidos divididos)

Revision ID: a8f2d6c1e407
Revises: e93dda63e26f
Create Date: 2026-10-19 15:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a8f2d6c1e407'
down_revision: Union[str, Sequence[str], None] = 'e93dda63e26f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Ensanchar la columna no altera los datos existentes
    op.alter_column('simulaciones_viaje', 'codigo_envio', existing_type=sa.String(length=12),
                    type_=sa.String(length=20), existing_nullable=True)


def downgrade() -> None:
    op.alter_column('simulaciones_viaje', 'codigo_envio', existing_type=sa.String(length=20),
                    type_=sa.String(length=12), existing_nullable=True)
