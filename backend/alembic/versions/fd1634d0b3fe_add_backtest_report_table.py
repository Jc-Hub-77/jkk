"""add_backtest_report_table

Revision ID: fd1634d0b3fe
Revises: 9feeb80d8c05
Create Date: 2025-05-26 23:08:55.507245

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fd1634d0b3fe'
down_revision: Union[str, None] = '9feeb80d8c05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
