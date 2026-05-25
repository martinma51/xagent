"""Slide Deck CRUD API.

Persists user-owned decks (template + filled slot values) so refreshes,
sessions, and devices don't lose work.  No deck content is ever read by a
different user — every endpoint scopes queries by ``current_user.id``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth_dependencies import get_current_user
from ..models.database import get_db
from ..models.deck import SlideDeck
from ..models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/decks", tags=["decks"])

MAX_DECKS_PER_USER = 200


# ===== Pydantic Models =====


class DeckInfo(BaseModel):
    """Brief deck info used in the list view."""

    id: int
    template_id: str
    title: str
    topic: str
    page_count: int = Field(
        ..., description="Number of pages with at least one filled slot."
    )
    created_at: datetime
    updated_at: datetime


class DeckDetail(DeckInfo):
    """Full deck with slot values, used in the editor."""

    slot_values: Dict[int, Dict[str, str]] = Field(default_factory=dict)


class DeckCreateRequest(BaseModel):
    template_id: str = Field(..., min_length=1, max_length=120)
    title: str = Field(default="", max_length=200)
    topic: str = Field(default="", max_length=2000)
    slot_values: Dict[int, Dict[str, str]] = Field(default_factory=dict)


class DeckUpdateRequest(BaseModel):
    """All fields optional; only supplied fields are written."""

    title: Optional[str] = Field(default=None, max_length=200)
    topic: Optional[str] = Field(default=None, max_length=2000)
    slot_values: Optional[Dict[int, Dict[str, str]]] = None


# ===== Helpers =====


def _normalise_slot_values(raw: Any) -> Dict[int, Dict[str, str]]:
    """Coerce JSON-stored dict (with string-keyed pages) to int-keyed pages."""
    if not isinstance(raw, dict):
        return {}
    out: Dict[int, Dict[str, str]] = {}
    for k, v in raw.items():
        if not isinstance(v, dict):
            continue
        try:
            idx = int(k)
        except (TypeError, ValueError):
            continue
        clean: Dict[str, str] = {}
        for slot_name, slot_val in v.items():
            if isinstance(slot_name, str) and isinstance(slot_val, str):
                clean[slot_name] = slot_val
        out[idx] = clean
    return out


def _count_filled_pages(slot_values: Dict[int, Dict[str, str]]) -> int:
    return sum(1 for page in slot_values.values() if any(v.strip() for v in page.values()))


def _to_info(deck: SlideDeck) -> DeckInfo:
    slot_values = _normalise_slot_values(deck.slot_values)
    return DeckInfo(
        id=deck.id,
        template_id=deck.template_id,
        title=deck.title or "",
        topic=deck.topic or "",
        page_count=_count_filled_pages(slot_values),
        created_at=deck.created_at,
        updated_at=deck.updated_at,
    )


def _to_detail(deck: SlideDeck) -> DeckDetail:
    slot_values = _normalise_slot_values(deck.slot_values)
    return DeckDetail(
        id=deck.id,
        template_id=deck.template_id,
        title=deck.title or "",
        topic=deck.topic or "",
        page_count=_count_filled_pages(slot_values),
        created_at=deck.created_at,
        updated_at=deck.updated_at,
        slot_values=slot_values,
    )


def _get_owned_deck(
    deck_id: int, db: Session, current_user: User
) -> SlideDeck:
    deck = db.query(SlideDeck).filter(SlideDeck.id == deck_id).first()
    if deck is None or deck.user_id != int(current_user.id):
        raise HTTPException(status_code=404, detail="deck not found")
    return deck


# ===== Endpoints =====


@router.get("/", response_model=List[DeckInfo])
async def list_decks(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    template_id: Optional[str] = Query(
        None, description="Optionally filter to one template's decks."
    ),
    limit: int = Query(50, ge=1, le=200),
) -> List[DeckInfo]:
    """List the current user's saved decks, newest first."""
    q = db.query(SlideDeck).filter(SlideDeck.user_id == int(current_user.id))
    if template_id:
        q = q.filter(SlideDeck.template_id == template_id)
    decks = q.order_by(SlideDeck.updated_at.desc()).limit(limit).all()
    return [_to_info(d) for d in decks]


@router.post("/", response_model=DeckDetail)
async def create_deck(
    body: DeckCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DeckDetail:
    """Create a new deck.  Used the first time auto-save fires on a draft."""
    # cheap defence against runaway creation (auto-save shouldn't spam create,
    # but a frontend bug shouldn't be able to fill the DB either)
    count = (
        db.query(SlideDeck).filter(SlideDeck.user_id == int(current_user.id)).count()
    )
    if count >= MAX_DECKS_PER_USER:
        raise HTTPException(
            status_code=400,
            detail=f"deck limit reached ({MAX_DECKS_PER_USER}); delete some first",
        )

    raw_values: Dict[str, Dict[str, str]] = {
        str(k): v for k, v in (body.slot_values or {}).items()
    }
    deck = SlideDeck(
        user_id=int(current_user.id),
        template_id=body.template_id,
        title=body.title or "",
        topic=body.topic or "",
        slot_values=raw_values,
    )
    db.add(deck)
    db.commit()
    db.refresh(deck)
    return _to_detail(deck)


@router.get("/{deck_id}", response_model=DeckDetail)
async def get_deck(
    deck_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DeckDetail:
    return _to_detail(_get_owned_deck(deck_id, db, current_user))


@router.put("/{deck_id}", response_model=DeckDetail)
async def update_deck(
    deck_id: int,
    body: DeckUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DeckDetail:
    deck = _get_owned_deck(deck_id, db, current_user)
    if body.title is not None:
        deck.title = body.title
    if body.topic is not None:
        deck.topic = body.topic
    if body.slot_values is not None:
        deck.slot_values = {str(k): v for k, v in body.slot_values.items()}
    db.commit()
    db.refresh(deck)
    return _to_detail(deck)


@router.delete("/{deck_id}")
async def delete_deck(
    deck_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, bool]:
    deck = _get_owned_deck(deck_id, db, current_user)
    db.delete(deck)
    db.commit()
    return {"success": True}
