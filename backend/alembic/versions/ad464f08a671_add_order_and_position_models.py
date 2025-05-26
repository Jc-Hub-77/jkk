"""add_order_and_position_models

Revision ID: ad464f08a671
Revises: fd1634d0b3fe
Create Date: 2025-05-26 23:13:49.651742

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ad464f08a671'
down_revision: Union[str, None] = 'fd1634d0b3fe'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
