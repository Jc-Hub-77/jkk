"""check_order_position_detection

Revision ID: 3d2b5a730765
Revises: ad464f08a671
Create Date: 2025-05-26 23:15:09.009560

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3d2b5a730765'
down_revision: Union[str, None] = 'ad464f08a671'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
