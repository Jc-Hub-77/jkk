"""alembic_troubleshoot_explicit_imports

Revision ID: 08eb12a31042
Revises: 3d2b5a730765
Create Date: 2025-05-26 23:19:47.039383

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '08eb12a31042'
down_revision: Union[str, None] = '3d2b5a730765'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
