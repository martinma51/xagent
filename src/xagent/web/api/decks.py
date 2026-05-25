"""Slide Deck CRUD API.

Persists user-owned decks so refreshes, sessions, and devices don't lose work.
Every endpoint scopes queries by ``current_user.id`` — no deck is ever readable
by anyone other than its owner.

Phase A: a deck is a variable-length ordered list of pages.  Each page picks
any layout (``<template_id>:<page_idx>``) and carries its own slot values, so
users can compose decks from any combination of layouts across templates.
"""

from __future__ import annotations

import logging
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...core.tools.core.slides_tool import (
    export_deck_pages_to_pptx,
    render_layout_html,
    resolve_layout,
    template_to_initial_pages,
)
from ..auth_dependencies import get_current_user
from ..models.database import get_db
from ..models.deck import SlideDeck
from ..models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/decks", tags=["decks"])

MAX_DECKS_PER_USER = 200
MAX_PAGES_PER_DECK = 50


# ===== Pydantic Models =====


class DeckPageEntry(BaseModel):
    """One page of a deck: a layout reference plus its filled slot values."""

    layout_id: str = Field(
        ...,
        min_length=1,
        max_length=160,
        description="Layout id in the form '<template_id>:<page_idx>'.",
    )
    slot_values: Dict[str, str] = Field(default_factory=dict)


class DeckInfo(BaseModel):
    """Brief deck info for the gallery / list views."""

    id: int
    template_id: str = Field(
        ..., description="Soft origin tag — template the deck was started from."
    )
    title: str
    topic: str
    page_count: int
    created_at: datetime
    updated_at: datetime


class DeckDetail(DeckInfo):
    """Full deck with its ordered pages, used by the editor."""

    pages: List[DeckPageEntry] = Field(default_factory=list)


class DeckCreateRequest(BaseModel):
    """Two creation modes, mutually exclusive but at least one must be given:

    - ``template_id`` only — bootstrap pages from every page of that template.
    - ``pages`` explicit — use the supplied page list verbatim.  ``template_id``
      remains as a soft origin tag for "Based on …" labels.
    """

    template_id: Optional[str] = Field(default=None, max_length=120)
    title: str = Field(default="", max_length=200)
    topic: str = Field(default="", max_length=2000)
    pages: Optional[List[DeckPageEntry]] = Field(default=None, max_length=MAX_PAGES_PER_DECK)


class DeckUpdateRequest(BaseModel):
    """All fields optional; only supplied fields are written."""

    title: Optional[str] = Field(default=None, max_length=200)
    topic: Optional[str] = Field(default=None, max_length=2000)
    pages: Optional[List[DeckPageEntry]] = Field(default=None, max_length=MAX_PAGES_PER_DECK)


class PreviewPageRequest(BaseModel):
    """Override the deck's persisted slot values for one preview render."""

    slot_values: Dict[str, str] = Field(default_factory=dict)


class RenderDeckRequest(BaseModel):
    filename: Optional[str] = Field(default=None, max_length=120)


# ===== Helpers =====


def _normalise_pages(raw: Any) -> List[Dict[str, Any]]:
    """Coerce a stored ``pages`` JSON value into the canonical shape.

    Strips garbage entries silently; any element that isn't a dict with a
    string ``layout_id`` is dropped.  Slot values are filtered to string-typed
    name/value pairs.
    """
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        layout_id = entry.get("layout_id")
        if not isinstance(layout_id, str) or not layout_id:
            continue
        slot_values: Dict[str, str] = {}
        for k, v in (entry.get("slot_values") or {}).items():
            if isinstance(k, str) and isinstance(v, str):
                slot_values[k] = v
        out.append({"layout_id": layout_id, "slot_values": slot_values})
    return out


def _validate_layout_refs(pages: List[DeckPageEntry], user_id: int) -> None:
    """Raise 400 if any layout_id in the deck doesn't resolve."""
    for i, p in enumerate(pages):
        resolved = resolve_layout(p.layout_id, user_id=user_id)
        if not resolved.get("success"):
            raise HTTPException(
                status_code=400,
                detail=f"page {i}: {resolved.get('error', 'invalid layout_id')}",
            )


def _to_info(deck: SlideDeck) -> DeckInfo:
    pages = _normalise_pages(deck.pages)
    return DeckInfo(
        id=deck.id,
        template_id=deck.template_id or "",
        title=deck.title or "",
        topic=deck.topic or "",
        page_count=len(pages),
        created_at=deck.created_at,
        updated_at=deck.updated_at,
    )


def _to_detail(deck: SlideDeck) -> DeckDetail:
    pages = _normalise_pages(deck.pages)
    return DeckDetail(
        id=deck.id,
        template_id=deck.template_id or "",
        title=deck.title or "",
        topic=deck.topic or "",
        page_count=len(pages),
        created_at=deck.created_at,
        updated_at=deck.updated_at,
        pages=[DeckPageEntry(**p) for p in pages],
    )


def _get_owned_deck(deck_id: int, db: Session, current_user: User) -> SlideDeck:
    deck = db.query(SlideDeck).filter(SlideDeck.id == deck_id).first()
    if deck is None or deck.user_id != int(current_user.id):
        raise HTTPException(status_code=404, detail="deck not found")
    return deck


def _resolve_page_or_404(deck: SlideDeck, page_idx: int) -> Dict[str, Any]:
    pages = _normalise_pages(deck.pages)
    if page_idx < 0 or page_idx >= len(pages):
        raise HTTPException(status_code=404, detail="page index out of range")
    return pages[page_idx]


# ===== CRUD =====


@router.get("/", response_model=List[DeckInfo])
async def list_decks(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    template_id: Optional[str] = Query(
        None, description="Optionally filter to decks tagged with one origin template."
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
    """Create a new deck.

    Two modes:
    - ``template_id`` only — bootstrap pages from every page of that template.
    - ``pages`` explicit — use the supplied list verbatim; ``template_id`` is
      kept as a soft origin tag (may be empty).
    """
    count = (
        db.query(SlideDeck).filter(SlideDeck.user_id == int(current_user.id)).count()
    )
    if count >= MAX_DECKS_PER_USER:
        raise HTTPException(
            status_code=400,
            detail=f"deck limit reached ({MAX_DECKS_PER_USER}); delete some first",
        )

    template_id = (body.template_id or "").strip()
    pages_list: List[Dict[str, Any]]

    if body.pages is not None:
        _validate_layout_refs(body.pages, user_id=int(current_user.id))
        pages_list = [
            {"layout_id": p.layout_id, "slot_values": dict(p.slot_values)}
            for p in body.pages
        ]
    elif template_id:
        bootstrap = template_to_initial_pages(
            template_id, user_id=int(current_user.id)
        )
        if not bootstrap.get("success"):
            raise HTTPException(status_code=400, detail=bootstrap.get("error", "bad template"))
        pages_list = bootstrap["pages"]
    else:
        raise HTTPException(
            status_code=400,
            detail="must provide either template_id or pages",
        )

    deck = SlideDeck(
        user_id=int(current_user.id),
        template_id=template_id,
        title=body.title or "",
        topic=body.topic or "",
        pages=pages_list,
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
    if body.pages is not None:
        _validate_layout_refs(body.pages, user_id=int(current_user.id))
        deck.pages = [
            {"layout_id": p.layout_id, "slot_values": dict(p.slot_values)}
            for p in body.pages
        ]
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


# ===== Preview + Render =====


@router.post("/{deck_id}/preview/{page_idx}", response_class=HTMLResponse)
async def preview_deck_page(
    deck_id: int,
    page_idx: int,
    body: PreviewPageRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Return filled HTML for one page of a deck.

    The frontend POSTs an updated ``slot_values`` snapshot on every keystroke
    so the live iframe stays in sync without persisting on each edit.  If the
    body's slot_values is empty, the persisted values are used instead.
    """
    deck = _get_owned_deck(deck_id, db, current_user)
    entry = _resolve_page_or_404(deck, page_idx)
    slot_values = body.slot_values if body.slot_values else entry["slot_values"]
    result = render_layout_html(
        entry["layout_id"], slot_values, user_id=int(current_user.id)
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "render failed"))
    return HTMLResponse(content=result["html"])


@router.post("/{deck_id}/render")
async def render_deck(
    deck_id: int,
    body: RenderDeckRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    """Render the deck's current persisted state into a .pptx and stream it back."""
    deck = _get_owned_deck(deck_id, db, current_user)
    pages = _normalise_pages(deck.pages)
    if not pages:
        raise HTTPException(status_code=400, detail="deck has no pages")

    tmpdir = Path(tempfile.mkdtemp(prefix="deck_render_"))
    safe_name = (body.filename or deck.title or f"deck_{deck.id}").strip()
    safe_name = safe_name.replace("/", "_") or f"deck_{deck.id}"
    out_path = tmpdir / f"{safe_name}.pptx"

    result = await export_deck_pages_to_pptx(
        pages, str(out_path), keep_html=False, user_id=int(current_user.id)
    )
    if not result.get("success"):
        raise HTTPException(
            status_code=400,
            detail={
                "error": result.get("error", "render failed"),
                "errors": result.get("errors"),
            },
        )
    if not out_path.exists():
        raise HTTPException(
            status_code=500, detail="render reported success but no file produced"
        )

    return FileResponse(
        out_path,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=f"{safe_name}.pptx",
    )
