"""merge generation_status + task_visibility heads

Revision ID: 588d25a97afb
Revises: 20260525_add_task_visibility, 20260525c_add_generation_status_to_slide_decks
Create Date: 2026-05-26 14:35:41.661417

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '588d25a97afb'
down_revision: Union[str, None] = ('20260525_add_task_visibility', '20260525c_add_generation_status_to_slide_decks')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
