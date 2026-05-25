"""replace slide_decks.slot_values with pages

Revision ID: 20260525b_replace_slot_values_with_pages
Revises: 20260525_add_slide_decks
Create Date: 2026-05-25 18:00:00.000000

Phase A of the slides product: a deck no longer has a 1:1 page mapping to its
originating template — instead it stores an ordered list of page entries, each
referencing a layout by ``"<template_id>:<page_idx>"``.  The ``template_id`` row
column is kept as a soft origin tag.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260525b_replace_slot_values_with_pages"
down_revision: Union[str, None] = "20260525_add_slide_decks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector() -> sa.Inspector:
    from alembic import context

    return sa.inspect(context.get_bind())


def _column_names(table_name: str) -> set[str]:
    if table_name not in _inspector().get_table_names():
        return set()
    return {c["name"] for c in _inspector().get_columns(table_name)}


def upgrade() -> None:
    cols = _column_names("slide_decks")
    if not cols:
        # table doesn't exist (fresh install will run earlier migration)
        return

    if "pages" not in cols:
        # SQLite-friendly: add nullable, backfill, then we don't enforce non-null
        # via DB constraint because batch_alter_table on SQLite is heavy.
        op.add_column(
            "slide_decks",
            sa.Column("pages", sa.JSON(), nullable=False, server_default="[]"),
        )

    if "slot_values" in cols:
        with op.batch_alter_table("slide_decks") as batch_op:
            batch_op.drop_column("slot_values")


def downgrade() -> None:
    cols = _column_names("slide_decks")
    if not cols:
        return

    if "slot_values" not in cols:
        op.add_column(
            "slide_decks",
            sa.Column("slot_values", sa.JSON(), nullable=False, server_default="{}"),
        )

    if "pages" in cols:
        with op.batch_alter_table("slide_decks") as batch_op:
            batch_op.drop_column("pages")
