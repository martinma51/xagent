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

import json as _json

from bs4 import BeautifulSoup
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
NON_WORD_RE = re.compile(r"[^\w]+", re.UNICODE)
DASH_RUN_RE = re.compile(r"_+")

CANVAS_W = 1280
CANVAS_H = 720

# Manual-slot defaults. White text + dark text-shadow so user-drawn slots stay
# legible over the dark backgrounds (chalkboards, gradients, photos) that
# pure-image pptx uploads tend to ship with.
SLOT_DEFAULT_STYLE = (
    "position: absolute; "
    "display: flex; align-items: center; justify-content: center; "
    "font-size: 28px; font-weight: 600; "
    "color: #ffffff; "
    "text-align: center; line-height: 1.2; "
    "text-shadow: 0 2px 8px rgba(0, 0, 0, 0.75); "
    "padding: 4px 8px; box-sizing: border-box; "
    "overflow: hidden; word-break: break-word;"
)

SLOT_NAME_RE = re.compile(r"^[\w][\w\-]{0,49}$", re.UNICODE)
SLOT_MAX_LEN_DEFAULT = 200
MAX_MANUAL_SLOTS_PER_PAGE = 30


# ===== Pydantic =====


class UserTemplateInfo(BaseModel):
    id: str
    name: str
    description: str = ""
    category: str = "User"
    page_count: int
    thumbnail_urls: List[str] = Field(default_factory=list)
    source: str = "user_uploaded_pptx"


class SlotCreateRequest(BaseModel):
    """Body for ``POST .../pages/{idx}/slots`` — define a new manual text box."""

    name: str = Field(..., description="Slot name (letters/digits/underscore, ≤50).")
    x: int = Field(..., ge=0, le=CANVAS_W)
    y: int = Field(..., ge=0, le=CANVAS_H)
    width: int = Field(..., ge=10, le=CANVAS_W)
    height: int = Field(..., ge=10, le=CANVAS_H)
    default_text: str = Field(default="Text", max_length=SLOT_MAX_LEN_DEFAULT)
    max_length: Optional[int] = Field(default=None, ge=1, le=2000)


# ===== HTML mutation helpers =====


def _slot_inline_style(x: int, y: int, width: int, height: int) -> str:
    pos = (
        f"left: {x}px; top: {y}px; width: {width}px; height: {height}px; "
    )
    return SLOT_DEFAULT_STYLE + " " + pos


def _inject_slot_div(
    html: str, *, slot_name: str, x: int, y: int, width: int, height: int, default_text: str
) -> str:
    """Append (or update) a manual text-frame `<div data-slot>` before `</body>`.

    Idempotent: if a slot with the same name already exists, its position and
    default text are overwritten in place. Otherwise a fresh element is added
    last so it stacks above the static slide image.
    """
    soup = BeautifulSoup(html, "html.parser")
    body = soup.find("body")
    if body is None:
        raise ValueError("template HTML has no <body>")
    existing = soup.select_one(f'[data-slot="{slot_name}"]')
    if existing is not None:
        existing["style"] = _slot_inline_style(x, y, width, height)
        existing["data-manual"] = "1"
        existing.clear()
        existing.append(default_text)
    else:
        div = soup.new_tag(
            "div",
            attrs={
                "data-slot": slot_name,
                "data-manual": "1",
                "style": _slot_inline_style(x, y, width, height),
            },
        )
        div.append(default_text)
        body.append(div)
    return str(soup)


def _remove_slot_div(html: str, slot_name: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    el = soup.select_one(f'[data-slot="{slot_name}"]')
    if el is not None:
        el.decompose()
    return str(soup)


def _regen_one_thumbnail(template_dir: Path, page_idx: int) -> None:
    """Re-render just one page's PNG thumbnail.

    Cheaper than the full ``_generate_thumbnails`` when only one page changed
    (typical for slot CRUD). Still ~1.5–2 s because it has to launch chromium.
    """
    from playwright.sync_api import sync_playwright

    meta = _json.loads((template_dir / "meta.json").read_text(encoding="utf-8"))
    pages = meta.get("pages", [])
    entry = next((p for p in pages if int(p.get("idx", -1)) == page_idx), None)
    if entry is None:
        return
    html_path = template_dir / entry["file"]
    if not html_path.exists():
        return
    thumbs = template_dir / "thumbnails"
    thumbs.mkdir(exist_ok=True)
    out_name = Path(entry["file"]).stem + ".png"

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(
            viewport={"width": CANVAS_W, "height": CANVAS_H},
            device_scale_factor=2,
        )
        page = context.new_page()
        page.goto(html_path.as_uri(), wait_until="networkidle")
        page.wait_for_timeout(400)
        page.screenshot(
            path=str(thumbs / out_name),
            clip={"x": 0, "y": 0, "width": CANVAS_W, "height": CANVAS_H},
        )
        browser.close()


# ===== Helpers =====


def _slugify(name: str) -> str:
    """Slugify a template name while preserving non-ASCII word chars (e.g. CJK).

    Python's ``\\w`` under the default ``re.UNICODE`` flag matches letters,
    digits, and underscore across every script — so Chinese, Japanese, Korean,
    Arabic, etc. all survive into the resulting id and remain human-readable.
    """
    s = name.strip().replace(" ", "_")
    s = NON_WORD_RE.sub("_", s)
    s = DASH_RUN_RE.sub("_", s).strip("_")
    # ``str.lower`` is a no-op on CJK; ASCII portions get normalised.
    return s.lower() or "template"


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


# ===== Manual-slot CRUD (Phase C') =====


def _resolve_owned_template_dir(user_id: int, template_id: str) -> Path:
    """Locate a user-owned template's directory or raise 404."""
    if not template_id.startswith(f"user_{user_id}_"):
        # Cheap guard — the id always carries the owner. Prevents cross-user
        # peeking even before the disk lookup.
        raise HTTPException(status_code=404, detail="template not found")
    tdir = get_user_templates_dir(user_id) / template_id
    if not tdir.exists() or not (tdir / "meta.json").exists():
        raise HTTPException(status_code=404, detail="template not found")
    return tdir


def _load_meta(tdir: Path) -> Dict[str, Any]:
    return _json.loads((tdir / "meta.json").read_text(encoding="utf-8"))


def _save_meta(tdir: Path, meta: Dict[str, Any]) -> None:
    (tdir / "meta.json").write_text(
        _json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _find_page_entry(meta: Dict[str, Any], page_idx: int) -> Dict[str, Any]:
    for entry in meta.get("pages", []):
        if int(entry.get("idx", -1)) == page_idx:
            return entry
    raise HTTPException(
        status_code=404, detail=f"page {page_idx} does not exist in this template"
    )


@router.post("/{template_id}/pages/{page_idx}/slots")
async def add_slot_to_page(
    template_id: str,
    page_idx: int,
    body: SlotCreateRequest,
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Add a manual text-frame slot to one page of a user-owned template.

    Mutates both the page HTML (injects a positioned ``<div data-slot>``) and
    the template's ``meta.json`` (registers the slot's schema). Regenerates
    that single page's thumbnail so the gallery preview reflects the change.
    """
    user_id = int(current_user.id)
    tdir = _resolve_owned_template_dir(user_id, template_id)

    # Validate slot name shape (letters/digits/underscore/hyphen, ≤50 chars).
    if not SLOT_NAME_RE.match(body.name):
        raise HTTPException(
            status_code=400,
            detail="slot name must be 1–50 chars of letters/digits/underscore/hyphen",
        )

    # Bounds: rectangle must stay inside the 1280×720 canvas.
    if body.x + body.width > CANVAS_W or body.y + body.height > CANVAS_H:
        raise HTTPException(
            status_code=400,
            detail=(
                f"slot rectangle ({body.x},{body.y}) {body.width}×{body.height} "
                f"falls outside the {CANVAS_W}×{CANVAS_H} canvas"
            ),
        )

    meta = _load_meta(tdir)
    entry = _find_page_entry(meta, page_idx)
    slots: Dict[str, Any] = entry.setdefault("slots", {})

    manual_count = sum(
        1 for s in slots.values() if isinstance(s, dict) and s.get("origin") == "manual"
    )
    if body.name not in slots and manual_count >= MAX_MANUAL_SLOTS_PER_PAGE:
        raise HTTPException(
            status_code=400,
            detail=f"page already has {MAX_MANUAL_SLOTS_PER_PAGE} manual slots — delete some first",
        )

    # Mutate the HTML.
    html_path = tdir / entry["file"]
    if not html_path.exists():
        raise HTTPException(status_code=500, detail=f"page HTML missing: {entry['file']}")
    html = html_path.read_text(encoding="utf-8")
    try:
        new_html = _inject_slot_div(
            html,
            slot_name=body.name,
            x=body.x,
            y=body.y,
            width=body.width,
            height=body.height,
            default_text=body.default_text,
        )
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    html_path.write_text(new_html, encoding="utf-8")

    # Register in meta with ``origin: "manual"`` so the editor can show a
    # delete affordance and so the (rectangle + style) survives reload.
    slots[body.name] = {
        "type": "text",
        "max_length": body.max_length or SLOT_MAX_LEN_DEFAULT,
        "required": False,
        "origin": "manual",
        "rect": {"x": body.x, "y": body.y, "width": body.width, "height": body.height},
        "default_text": body.default_text,
    }
    _save_meta(tdir, meta)

    # Refresh the single thumbnail (best-effort — non-fatal).
    try:
        await asyncio.to_thread(_regen_one_thumbnail, tdir, page_idx)
    except Exception as exc:
        logger.exception("thumbnail regen failed for %s page %s: %s", template_id, page_idx, exc)

    return {
        "success": True,
        "template_id": template_id,
        "page_idx": page_idx,
        "slot": {"name": body.name, **slots[body.name]},
    }


@router.delete("/{template_id}/pages/{page_idx}/slots/{slot_name}")
async def remove_slot_from_page(
    template_id: str,
    page_idx: int,
    slot_name: str,
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Remove a manual slot from one page of a user-owned template.

    Refuses to remove slots that weren't added by the user (``origin != "manual"``)
    — those come from the pptx layout and would orphan their HTML if removed.
    """
    user_id = int(current_user.id)
    tdir = _resolve_owned_template_dir(user_id, template_id)

    meta = _load_meta(tdir)
    entry = _find_page_entry(meta, page_idx)
    slots: Dict[str, Any] = entry.setdefault("slots", {})
    if slot_name not in slots:
        raise HTTPException(
            status_code=404, detail=f"page {page_idx} has no slot '{slot_name}'"
        )
    schema = slots[slot_name]
    if not (isinstance(schema, dict) and schema.get("origin") == "manual"):
        raise HTTPException(
            status_code=400,
            detail=f"slot '{slot_name}' was not manually added and cannot be deleted",
        )

    html_path = tdir / entry["file"]
    if html_path.exists():
        html = html_path.read_text(encoding="utf-8")
        html_path.write_text(_remove_slot_div(html, slot_name), encoding="utf-8")

    del slots[slot_name]
    _save_meta(tdir, meta)

    try:
        await asyncio.to_thread(_regen_one_thumbnail, tdir, page_idx)
    except Exception as exc:
        logger.exception("thumbnail regen failed for %s page %s: %s", template_id, page_idx, exc)

    return {"success": True, "template_id": template_id, "page_idx": page_idx, "slot_name": slot_name}
