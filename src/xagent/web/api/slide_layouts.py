"""Slide Layout API.

A "layout" is one page of one template, addressed by ``"<template_id>:<idx>"``.
Decks (Phase A) are composed by picking any layouts from this flat list and
arranging them in any order — they are not bound to a single template.

This endpoint is what the deck editor's "Add page" picker calls to list all
available layouts grouped by template.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ...core.tools.core.slides_tool import list_slide_layouts
from ..auth_dependencies import get_current_user
from ..models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/slide-layouts", tags=["slide-layouts"])


class SlotSpec(BaseModel):
    type: str = "text"
    required: bool = False
    max_length: Optional[int] = None
    hint: Optional[str] = None


class SlideLayoutInfo(BaseModel):
    id: str = Field(..., description="Layout id, e.g. 'midnight_tech:0'.")
    template_id: str
    template_name: str
    template_category: str = ""
    page_idx: int
    layout: str = Field(default="", description="Layout name from the template's meta.")
    thumbnail_url: str
    slots: Dict[str, SlotSpec] = Field(default_factory=dict)


def _thumbnail_url(template_id: str, page_idx: int) -> str:
    return f"/api/slide-templates/{template_id}/thumbnails/{page_idx}.png"


@router.get("/", response_model=List[SlideLayoutInfo])
async def list_layouts(
    current_user: User = Depends(get_current_user),
    template_id: Optional[str] = Query(
        None, description="Optionally filter to layouts from one template."
    ),
    category: Optional[str] = Query(
        None, description="Optionally filter by template category."
    ),
) -> List[SlideLayoutInfo]:
    """Return every layout across every template as a flat list."""
    result = list_slide_layouts(user_id=int(current_user.id))
    layouts: List[Dict[str, Any]] = result["layouts"]
    if template_id:
        layouts = [l for l in layouts if l["template_id"] == template_id]
    if category:
        layouts = [
            l for l in layouts if l["template_category"].lower() == category.lower()
        ]
    return [
        SlideLayoutInfo(
            id=l["id"],
            template_id=l["template_id"],
            template_name=l["template_name"],
            template_category=l.get("template_category", ""),
            page_idx=l["page_idx"],
            layout=l.get("layout", ""),
            thumbnail_url=_thumbnail_url(l["template_id"], l["page_idx"]),
            slots=l.get("slots", {}),
        )
        for l in layouts
    ]
