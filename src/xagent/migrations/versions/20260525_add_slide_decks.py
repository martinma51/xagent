"""add slide_decks table

Revision ID: 20260525_add_slide_decks
Revises: 20260523_add_workforce_core_tables
Create Date: 2026-05-25 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260525_add_slide_decks"
down_revision: Union[str, None] = "20260523_add_workforce_core_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector() -> sa.Inspector:
    from alembic import context

    return sa.inspect(context.get_bind())


def _table_exists(table_name: str) -> bool:
    return table_name in _inspector().get_table_names()


def upgrade() -> None:
    if _table_exists("slide_decks"):
        return
    op.create_table(
        "slide_decks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("template_id", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("topic", sa.String(length=2000), nullable=False, server_default=""),
        sa.Column("slot_values", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
    )
    op.create_index(
        op.f("ix_slide_decks_user_id"), "slide_decks", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_slide_decks_template_id"),
        "slide_decks",
        ["template_id"],
        unique=False,
    )


def downgrade() -> None:
    if not _table_exists("slide_decks"):
        return
    op.drop_index(op.f("ix_slide_decks_template_id"), table_name="slide_decks")
    op.drop_index(op.f("ix_slide_decks_user_id"), table_name="slide_decks")
    op.drop_table("slide_decks")
