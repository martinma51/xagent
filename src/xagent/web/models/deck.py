"""Slide-deck persistence model.

One row per saved deck.  A deck is an ordered list of pages; each page picks a
layout from any template and carries its own slot values.  ``template_id``
remains as a soft origin tag (the template the deck was started from) but the
pages themselves can mix layouts across templates.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class SlideDeck(Base):  # type: ignore[misc]
    """A user-owned slide deck, materialised as a list of layout-referenced pages."""

    __tablename__ = "slide_decks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Soft origin tag — which template the deck was created from.  Pages may
    # later be added from other templates, so this is no longer authoritative
    # for the deck's content; treat it as metadata for "Based on …" labels.
    template_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    topic: Mapped[str] = mapped_column(String(2000), nullable=False, default="")

    # Ordered list of pages.  Shape:
    #   [{"layout_id": "<template_id>:<page_idx>", "slot_values": {<name>: <text>}}, ...]
    pages: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
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
            f"template='{self.template_id}' pages={len(self.pages or [])}>"
        )
