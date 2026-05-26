"""Add generation_status + generation_error columns to slide_decks.

These power the Phase D5 async-generate flow: when a deck is created via
``POST /api/decks/generate`` the row is persisted immediately with
``generation_status='pending'`` and the LLM fill runs in a BackgroundTask.
The new /generate/{id} frontend page polls the deck and redirects to the
editor once status transitions to ``'done'`` (or shows the error message
when it goes to ``'error'``).

Existing rows from before this migration default to NULL, which the
frontend treats as ``'done'`` (no waiting screen needed).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260525c_add_generation_status_to_slide_decks"
down_revision: Union[str, None] = "20260525b_replace_slot_values_with_pages"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "slide_decks",
        sa.Column("generation_status", sa.String(length=24), nullable=True),
    )
    op.add_column(
        "slide_decks",
        sa.Column("generation_error", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("slide_decks", "generation_error")
    op.drop_column("slide_decks", "generation_status")
