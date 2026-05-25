"""Slide-deck persistence model.

One row per saved deck.  The full slot-value payload lives in a JSON column
so the schema stays in lock-step with whatever shape the LLM auto-fill
produces — no migration needed when a template adds or renames slots.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class SlideDeck(Base):  # type: ignore[misc]
    """A user-owned, template-derived slide deck."""

    __tablename__ = "slide_decks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    template_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    topic: Mapped[str] = mapped_column(String(2000), nullable=False, default="")

    # Per-page slot values, shape: {"<page_idx>": {"<slot>": "<text>", ...}, ...}
    # JSON keeps us schema-flexible across template changes.
    slot_values: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.now,
        onupdate=datetime.now,
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<SlideDeck id={self.id} user={self.user_id} "
            f"template='{self.template_id}' title='{self.title[:30]}'>"
        )
