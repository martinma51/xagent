"""User-uploaded slide template API.

Phase B endpoints — accept a .pptx, convert it to the template format used by
the rest of the slides product, and persist the result under the user's own
template directory so it shows up alongside built-in templates in the gallery
and in the deck editor's layout picker.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from ...core.tools.core.pptx_to_template import convert_pptx_to_template
from ...core.tools.core.slides_tool import (
    delete_user_slide_template,
    get_user_slide_template,
    get_user_templates_dir,
    list_user_slide_templates,
)
from ..auth_dependencies import get_current_user
from ..models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/user-slide-templates", tags=["user-slide-templates"])

MAX_UPLOAD_BYTES = 30 * 1024 * 1024  # 30 MB
MAX_TEMPLATES_PER_USER = 100
SLUG_RE = re.compile(r"[^a-z0-9_]+")

CANVAS_W = 1280
CANVAS_H = 720


# ===== Pydantic =====


class UserTemplateInfo(BaseModel):
    id: str
    name: str
    description: str = ""
    category: str = "User"
    page_count: int
    thumbnail_urls: List[str] = Field(default_factory=list)
    source: str = "user_uploaded_pptx"


# ===== Helpers =====


def _slugify(name: str) -> str:
    s = name.lower().strip().replace(" ", "_")
    s = SLUG_RE.sub("_", s)
    return s.strip("_") or "template"


def _build_template_id(user_id: int, name: str) -> str:
    base = _slugify(name)[:40]
    suffix = uuid.uuid4().hex[:8]
    return f"user_{user_id}_{base}_{suffix}"


def _thumbnail_url(template_id: str, page_idx: int) -> str:
    return f"/api/slide-templates/{template_id}/thumbnails/{page_idx}.png"


def _generate_thumbnails(template_dir: Path) -> None:
    """Render every page HTML in ``template_dir`` to a PNG via playwright.

    Runs synchronously — callers must hand off to a worker thread when used
    from an async request handler.
    """
    import json as _json

    from playwright.sync_api import sync_playwright

    meta = _json.loads((template_dir / "meta.json").read_text(encoding="utf-8"))
    thumbs = template_dir / "thumbnails"
    thumbs.mkdir(exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(
            viewport={"width": CANVAS_W, "height": CANVAS_H},
            device_scale_factor=2,
        )
        page = context.new_page()
        for entry in meta["pages"]:
            html_path = template_dir / entry["file"]
            if not html_path.exists():
                continue
            out_name = Path(entry["file"]).stem + ".png"
            page.goto(html_path.as_uri(), wait_until="networkidle")
            page.wait_for_timeout(400)
            page.screenshot(
                path=str(thumbs / out_name),
                clip={"x": 0, "y": 0, "width": CANVAS_W, "height": CANVAS_H},
            )
        browser.close()


def _summary_from_meta(template_id: str, summary: Dict[str, Any]) -> UserTemplateInfo:
    page_count = int(summary.get("page_count") or 0)
    return UserTemplateInfo(
        id=summary.get("id", template_id),
        name=summary.get("name", template_id),
        description=summary.get("description", ""),
        category=summary.get("category", "User"),
        page_count=page_count,
        thumbnail_urls=[_thumbnail_url(template_id, i) for i in range(page_count)],
        source=summary.get("source", "user_uploaded_pptx"),
    )


# ===== Endpoints =====


@router.get("/", response_model=List[UserTemplateInfo])
async def list_my_templates(
    current_user: User = Depends(get_current_user),
) -> List[UserTemplateInfo]:
    """List every template uploaded by the current user."""
    result = list_user_slide_templates(int(current_user.id))
    return [_summary_from_meta(t["id"], t) for t in result["templates"]]


@router.post("/upload", response_model=UserTemplateInfo)
async def upload_pptx_template(
    file: UploadFile = File(...),
    name: Optional[str] = Form(default=None),
    description: Optional[str] = Form(default=None),
    current_user: User = Depends(get_current_user),
) -> UserTemplateInfo:
    """Accept a .pptx upload and turn it into a user template."""
    user_id = int(current_user.id)

    # Quota guard
    existing = list_user_slide_templates(user_id)
    if len(existing["templates"]) >= MAX_TEMPLATES_PER_USER:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Template limit reached ({MAX_TEMPLATES_PER_USER}); delete some first."
            ),
        )

    # Filetype + size guard
    filename = file.filename or "uploaded.pptx"
    if not filename.lower().endswith(".pptx"):
        raise HTTPException(status_code=400, detail="Only .pptx files are accepted.")

    body = await file.read()
    if not body:
        raise HTTPException(status_code=400, detail="Empty upload.")
    if len(body) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File too large (limit {MAX_UPLOAD_BYTES // (1024 * 1024)} MB).",
        )

    display_name = (name or Path(filename).stem).strip() or "Untitled deck"
    template_id = _build_template_id(user_id, display_name)
    target_dir = get_user_templates_dir(user_id) / template_id

    # Drop the source pptx into a tmp file so python-pptx can read it.
    tmpdir = Path(tempfile.mkdtemp(prefix="pptx_upload_"))
    tmp_pptx = tmpdir / filename
    tmp_pptx.write_bytes(body)

    try:
        result = await asyncio.to_thread(
            convert_pptx_to_template,
            tmp_pptx,
            template_id,
            display_name,
            target_dir,
            description or "",
            "User",
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    if not result.get("success"):
        # Roll back any partial output dir
        shutil.rmtree(target_dir, ignore_errors=True)
        raise HTTPException(
            status_code=400,
            detail=result.get("error", "Failed to convert pptx."),
        )

    # Thumbnails via playwright — wrap in worker thread so we don't block.
    try:
        await asyncio.to_thread(_generate_thumbnails, target_dir)
    except Exception as exc:
        logger.exception("Thumbnail generation failed for %s: %s", template_id, exc)
        # Don't fail the upload — gallery falls back to no thumbnail.

    found = get_user_slide_template(user_id, template_id)
    if not found.get("success"):
        raise HTTPException(
            status_code=500, detail="Template wrote successfully but cannot be re-loaded."
        )
    meta = found["template"]
    return _summary_from_meta(
        template_id,
        {
            "id": template_id,
            "name": meta.get("name", display_name),
            "description": meta.get("description", description or ""),
            "category": meta.get("category", "User"),
            "page_count": meta.get("page_count", 0),
            "source": meta.get("source", "user_uploaded_pptx"),
        },
    )


@router.delete("/{template_id}")
async def delete_my_template(
    template_id: str,
    current_user: User = Depends(get_current_user),
) -> Dict[str, bool]:
    """Permanently delete one of the user's uploaded templates."""
    result = delete_user_slide_template(int(current_user.id), template_id)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error", "not found"))
    return {"success": True}
