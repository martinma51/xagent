"""Slide Template API Endpoints.

REST API for browsing the bundled deck templates, fetching per-page slot
schemas, serving rendered thumbnails, and rendering a filled deck into a
downloadable .pptx file.

No database persistence in this version — templates are read straight off
disk via :func:`xagent.core.tools.core.slides_tool` and decks are rendered to
a per-request temp directory.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from ...core.tools.core.slides_tool import (
    auto_fill_slots,
    create_deck_from_template,
    get_slide_template,
    list_slide_templates,
    list_user_slide_templates,
    render_page_html,
)
from ..auth_dependencies import get_current_user
from ..models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/slide-templates", tags=["slide-templates"])


# ===== Pydantic Models =====


class SlideTemplateInfo(BaseModel):
    """Summary view of one deck template (used by the gallery)."""

    id: str = Field(..., description="Template id (matches its directory).")
    name: str = Field(..., description="Human-readable name.")
    description: str = Field(default="", description="One-paragraph blurb.")
    category: str = Field(default="", description="High-level category.")
    page_count: int = Field(..., description="Number of slides in the template.")
    thumbnail_urls: List[str] = Field(
        default_factory=list,
        description="API URLs that serve the per-page preview PNGs in order.",
    )
    is_user_uploaded: bool = Field(
        default=False,
        description="True when the template was uploaded by the current user.",
    )


class SlotSpec(BaseModel):
    """Schema for a single fillable slot on a template page."""

    type: str = "text"
    required: bool = False
    max_length: Optional[int] = None
    hint: Optional[str] = None


class SlidePageInfo(BaseModel):
    idx: int
    layout: str
    file: str
    slots: Dict[str, SlotSpec] = Field(default_factory=dict)


class SlideTemplateDetail(SlideTemplateInfo):
    """Full template view with per-page slot schemas (used by the editor)."""

    version: Optional[str] = None
    canvas: Dict[str, int] = Field(default_factory=dict)
    pages: List[SlidePageInfo] = Field(default_factory=list)


class RenderDeckRequest(BaseModel):
    """Body of POST /{template_id}/render."""

    slot_values: Dict[int, Dict[str, str]] = Field(
        default_factory=dict,
        description=(
            "Per-page slot values, keyed by page index. "
            "Pages or slots omitted here fall back to the template default text."
        ),
    )
    filename: Optional[str] = Field(
        default=None,
        description="Optional download filename (without .pptx extension).",
    )


class PreviewPageRequest(BaseModel):
    """Body of POST /{template_id}/preview/{page_idx}."""

    slot_values: Dict[str, str] = Field(
        default_factory=dict,
        description="Slot values for this one page; omitted slots use defaults.",
    )


class AutoFillRequest(BaseModel):
    """Body of POST /{template_id}/auto-fill."""

    topic: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Free-text description of the deck the LLM should write.",
    )


class AutoFillResponse(BaseModel):
    """Response body of POST /{template_id}/auto-fill."""

    slot_values: Dict[int, Dict[str, str]] = Field(default_factory=dict)
    model: str = ""


# ===== Helpers =====


def _thumbnail_url(template_id: str, page_idx: int) -> str:
    return f"/api/slide-templates/{template_id}/thumbnails/{page_idx}.png"


def _attach_thumbnail_urls(template_id: str, info: Dict[str, Any]) -> Dict[str, Any]:
    """Replace local filesystem thumbnail paths with the API URLs."""
    page_count = info.get("page_count", 0)
    info["thumbnail_urls"] = [_thumbnail_url(template_id, i) for i in range(page_count)]
    info.pop("thumbnails", None)
    return info


# ===== Endpoints =====


@router.get("/", response_model=List[SlideTemplateInfo])
async def list_templates(
    current_user: User = Depends(get_current_user),
    category: Optional[str] = Query(None, description="Filter by category."),
) -> List[SlideTemplateInfo]:
    """List every available deck template (optionally filtered by category)."""
    result = list_slide_templates(category=category, user_id=int(current_user.id))
    user_ids = {
        t["id"]
        for t in list_user_slide_templates(int(current_user.id))["templates"]
    }
    return [
        SlideTemplateInfo(
            **_attach_thumbnail_urls(t["id"], t),
            is_user_uploaded=t["id"] in user_ids,
        )
        for t in result["templates"]
    ]


@router.get("/{template_id}", response_model=SlideTemplateDetail)
async def get_template(
    template_id: str,
    current_user: User = Depends(get_current_user),
) -> SlideTemplateDetail:
    """Return one template's full schema (page list + slot definitions)."""
    found = get_slide_template(template_id, user_id=int(current_user.id))
    if not found["success"]:
        raise HTTPException(status_code=404, detail=found.get("error", "not found"))
    meta = found["template"]
    pages_raw = meta.get("pages", [])
    return SlideTemplateDetail(
        id=meta["id"],
        name=meta.get("name", meta["id"]),
        description=meta.get("description", ""),
        category=meta.get("category", ""),
        page_count=meta.get("page_count", len(pages_raw)),
        version=meta.get("version"),
        canvas=meta.get("canvas", {}),
        pages=[SlidePageInfo(**p) for p in pages_raw],
        thumbnail_urls=[
            _thumbnail_url(template_id, i) for i in range(len(pages_raw))
        ],
    )


@router.get("/{template_id}/thumbnails/{filename}")
async def get_thumbnail(
    template_id: str,
    filename: str,
) -> FileResponse:
    """Serve a single PNG thumbnail for one template page.

    Public route — these are static previews bundled with the product and
    rendered into ``<img>`` tags by the frontend, which cannot attach bearer
    tokens.  Templates themselves still require auth to list / inspect.

    ``filename`` may be either ``<idx>.png`` (positional) or the literal
    template-side filename like ``page_0_cover.png``.
    """
    # Search built-ins first, then every user's upload dir.  Template ids in
    # user uploads include a random suffix so cross-user collisions are
    # vanishingly rare; this keeps the route public so <img> tags can fetch
    # without bearer-token plumbing.
    found = get_slide_template(template_id)
    if not found["success"]:
        from ...core.tools.core.slides_tool import get_user_templates_root

        root = get_user_templates_root()
        if root.exists():
            for user_dir in root.iterdir():
                if not user_dir.is_dir():
                    continue
                candidate_dir = user_dir / template_id
                if (candidate_dir / "meta.json").exists():
                    found = {
                        "success": True,
                        "template": {"_dir": str(candidate_dir)},
                    }
                    break
        if not found["success"]:
            raise HTTPException(status_code=404, detail="template not found")

    template_dir = Path(found["template"]["_dir"])
    thumbs_dir = template_dir / "thumbnails"
    if not thumbs_dir.is_dir():
        raise HTTPException(status_code=404, detail="template has no thumbnails")

    # Positional lookup: "0.png" → page 0 → the first PNG in sorted order.
    candidate: Optional[Path] = None
    stem = Path(filename).stem
    if stem.isdigit():
        idx = int(stem)
        sorted_pngs = sorted(thumbs_dir.glob("*.png"))
        if 0 <= idx < len(sorted_pngs):
            candidate = sorted_pngs[idx]
    else:
        direct = thumbs_dir / filename
        if direct.exists():
            candidate = direct

    if candidate is None or not candidate.exists():
        raise HTTPException(status_code=404, detail="thumbnail not found")

    return FileResponse(candidate, media_type="image/png")


@router.post("/{template_id}/auto-fill", response_model=AutoFillResponse)
async def auto_fill_template(
    template_id: str,
    body: AutoFillRequest,
    _current_user: User = Depends(get_current_user),
) -> AutoFillResponse:
    """Have the configured LLM write a full set of slot values for one topic.

    The frontend calls this from the editor's ✨ "Auto-fill with AI" entry —
    user types a topic, every page's slots come back populated, the iframe
    previews refresh through the existing live-preview pipeline.
    """
    result = await auto_fill_slots(template_id, body.topic)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "auto-fill failed"))
    return AutoFillResponse(
        slot_values=result.get("slot_values", {}),
        model=result.get("model", ""),
    )


@router.post("/{template_id}/preview/{page_idx}", response_class=HTMLResponse)
async def preview_page(
    template_id: str,
    page_idx: int,
    body: PreviewPageRequest,
    _current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    """Return filled HTML for a single page (used by the live iframe preview).

    The frontend POSTs every change here and drops the response into an
    ``<iframe srcDoc>`` so the user sees their content composited against the
    template's CSS in real time.
    """
    result = render_page_html(template_id, page_idx, body.slot_values)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error", "not found"))
    return HTMLResponse(content=result["html"])


@router.post("/{template_id}/render")
async def render_template(
    template_id: str,
    body: RenderDeckRequest,
    _current_user: User = Depends(get_current_user),
) -> FileResponse:
    """Fill the template's slots and return the resulting .pptx file.

    The deck is rendered into a per-request temp directory and streamed back
    immediately.  No persistence — refresh the editor and re-render to get a
    new file.
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="slide_render_"))
    safe_name = (body.filename or template_id).strip().replace("/", "_") or template_id
    out_path = tmpdir / f"{safe_name}.pptx"

    result = await create_deck_from_template(
        template_id=template_id,
        slot_values=body.slot_values,
        output_pptx_path=str(out_path),
        workspace=None,
        keep_html=False,
    )
    if not result.get("success"):
        raise HTTPException(
            status_code=400,
            detail={"error": result.get("error", "render failed"), "errors": result.get("errors")},
        )
    if not out_path.exists():
        raise HTTPException(status_code=500, detail="render reported success but no file produced")

    return FileResponse(
        out_path,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=f"{safe_name}.pptx",
    )
