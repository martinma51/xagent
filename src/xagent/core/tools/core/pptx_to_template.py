"""PPTX → slide-template converter.

Reads a ``.pptx`` upload and emits one absolute-positioned HTML page per slide
on a 1280×720 canvas, plus a ``meta.json`` that matches the schema used by the
bundled templates.  Every text frame becomes a ``data-slot`` element so the
deck editor's slot system applies the same way as for hand-authored templates.

Fidelity is intentionally limited — we cover text + simple shapes + pictures.
Charts, SmartArt, tables, and animations are dropped.  The result is meant to
be a fair starting point that the user can edit further in the deck editor,
not a pixel-perfect reproduction of the source deck.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass
from html import escape as html_escape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.util import Emu

logger = logging.getLogger(__name__)

CANVAS_W = 1280
CANVAS_H = 720
SLOT_NAME_RE = re.compile(r"[^a-zA-Z0-9_]+")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


@dataclass
class _Scale:
    sx: float
    sy: float

    def x(self, emu: Optional[int]) -> int:
        return int(round((emu or 0) * self.sx))

    def y(self, emu: Optional[int]) -> int:
        return int(round((emu or 0) * self.sy))


def _scaler(prs: Presentation) -> _Scale:
    sw = prs.slide_width or Emu(9144000)
    sh = prs.slide_height or Emu(6858000)
    return _Scale(CANVAS_W / sw, CANVAS_H / sh)


def _rgb_to_css(rgb: Optional[RGBColor]) -> Optional[str]:
    if rgb is None:
        return None
    try:
        return f"#{str(rgb)}"
    except Exception:
        return None


def _safe_call(fn):
    """Run a getter that may raise (python-pptx throws for missing colors etc.)."""
    try:
        return fn()
    except Exception:
        return None


def _font_size_px(font) -> Optional[int]:
    """Return font size in px (rounded).  PPTX font.size is in EMU (Pt subclass)."""
    if font is None or font.size is None:
        return None
    # font.size is a Pt-like int; Pt has .pt attribute
    try:
        return int(round(font.size.pt * 1.333))  # 1pt ≈ 1.333px at 96dpi
    except AttributeError:
        try:
            return int(round(int(font.size) / 9525))  # EMU → px fallback
        except Exception:
            return None


def _para_align_css(align) -> Optional[str]:
    if align is None:
        return None
    name = getattr(align, "name", str(align)).lower()
    if "center" in name:
        return "center"
    if "right" in name:
        return "right"
    if "justif" in name:
        return "justify"
    return "left"


# ---------------------------------------------------------------------------
# slot naming
# ---------------------------------------------------------------------------


def _slug_from_text(text: str, fallback: str) -> str:
    """Turn the first few words of a text frame into a stable slot slug."""
    if not text or not text.strip():
        return fallback
    words = re.findall(r"[A-Za-z0-9]+", text.lower())[:3]
    if not words:
        return fallback
    slug = "_".join(words)[:32]
    return SLOT_NAME_RE.sub("_", slug) or fallback


# ---------------------------------------------------------------------------
# shape emission
# ---------------------------------------------------------------------------


def _emit_text_shape(
    shape, scale: _Scale, slot_name: str
) -> Tuple[str, Dict[str, Any], str]:
    """Convert a text-frame shape to an HTML div with data-slot.

    Returns ``(html, slot_spec, default_text)``.  ``slot_spec`` matches the
    schema used in template meta.json.
    """
    tf = shape.text_frame
    paragraphs = list(tf.paragraphs)
    # First non-empty run carries the dominant style.
    dominant_run = None
    for p in paragraphs:
        for r in p.runs:
            if r.text and r.text.strip():
                dominant_run = r
                break
        if dominant_run is not None:
            break
    font = dominant_run.font if dominant_run is not None else None

    # Compose style
    style_parts: List[str] = [
        "position: absolute",
        f"left: {scale.x(shape.left)}px",
        f"top: {scale.y(shape.top)}px",
        f"width: {scale.x(shape.width)}px",
        f"height: {scale.y(shape.height)}px",
        "overflow: hidden",
        "white-space: pre-wrap",
        "word-wrap: break-word",
    ]
    if font is not None:
        fs = _font_size_px(font)
        if fs:
            style_parts.append(f"font-size: {fs}px")
        if font.name:
            style_parts.append(f"font-family: '{font.name}', system-ui, sans-serif")
        if font.bold:
            style_parts.append("font-weight: 700")
        if font.italic:
            style_parts.append("font-style: italic")
        color = _safe_call(lambda: font.color.rgb)
        css_color = _rgb_to_css(color)
        if css_color:
            style_parts.append(f"color: {css_color}")

    # Alignment from the first paragraph
    if paragraphs:
        align = _para_align_css(paragraphs[0].alignment)
        if align:
            style_parts.append(f"text-align: {align}")

    # Line-height: rough heuristic, helps multi-line text look right
    style_parts.append("line-height: 1.25")

    text = tf.text or ""
    style = "; ".join(style_parts)

    # Build HTML
    html = (
        f'<div style="{style}" data-slot="{html_escape(slot_name)}">'
        f"{html_escape(text)}</div>"
    )

    spec: Dict[str, Any] = {
        "type": "text",
        "required": False,
        "max_length": max(len(text) * 2, 80),
        "hint": text[:60] if text.strip() else None,
    }
    return html, spec, text


def _emit_picture_shape(shape, scale: _Scale) -> Optional[str]:
    """Embed a picture as a base64 ``<img>`` so the template is self-contained."""
    try:
        image = shape.image
        blob = image.blob
        content_type = image.content_type or "image/png"
    except Exception:
        return None
    b64 = base64.b64encode(blob).decode("ascii")
    style_parts = [
        "position: absolute",
        f"left: {scale.x(shape.left)}px",
        f"top: {scale.y(shape.top)}px",
        f"width: {scale.x(shape.width)}px",
        f"height: {scale.y(shape.height)}px",
        "object-fit: cover",
    ]
    style = "; ".join(style_parts)
    return (
        f'<img src="data:{content_type};base64,{b64}" '
        f'style="{style}" alt="" />'
    )


def _emit_basic_shape(shape, scale: _Scale) -> Optional[str]:
    """Best-effort render of rectangles / ellipses / lines as styled divs."""
    fill_color = _safe_call(lambda: shape.fill.fore_color.rgb)
    line_color = _safe_call(lambda: shape.line.color.rgb)
    line_width_emu = _safe_call(lambda: shape.line.width)
    line_w_px = int(round((line_width_emu or 0) / 12700)) if line_width_emu else 0
    bg = _rgb_to_css(fill_color)
    border = _rgb_to_css(line_color)
    if not bg and not border:
        return None
    style_parts = [
        "position: absolute",
        f"left: {scale.x(shape.left)}px",
        f"top: {scale.y(shape.top)}px",
        f"width: {scale.x(shape.width)}px",
        f"height: {scale.y(shape.height)}px",
    ]
    if bg:
        style_parts.append(f"background-color: {bg}")
    if border and line_w_px > 0:
        style_parts.append(f"border: {line_w_px}px solid {border}")
    # Rounded rect / ellipse hint
    st = getattr(shape, "shape_type", None)
    name = getattr(getattr(shape, "auto_shape_type", None), "name", "") or ""
    if name and "ROUNDED" in name.upper():
        style_parts.append("border-radius: 12px")
    if st == MSO_SHAPE_TYPE.AUTO_SHAPE and "OVAL" in name.upper():
        style_parts.append("border-radius: 9999px")
    return f'<div style="{"; ".join(style_parts)}"></div>'


def _slide_background_color(slide) -> Optional[str]:
    fill = _safe_call(lambda: slide.background.fill)
    if fill is None:
        return None
    rgb = _safe_call(lambda: fill.fore_color.rgb)
    return _rgb_to_css(rgb)


# ---------------------------------------------------------------------------
# slide rendering
# ---------------------------------------------------------------------------


def _render_slide(
    slide,
    slide_idx: int,
    scale: _Scale,
) -> Tuple[str, Dict[str, Dict[str, Any]]]:
    """Render one slide to (html_string, {slot_name: spec_dict})."""
    bg_color = _slide_background_color(slide) or "#ffffff"
    body_chunks: List[str] = []
    slots: Dict[str, Dict[str, Any]] = {}
    seen_slot_names: set[str] = set()

    # text shapes first (background), pictures + shapes in source order
    for i, shape in enumerate(slide.shapes):
        try:
            shape_type = getattr(shape, "shape_type", None)
            if shape.has_text_frame and (shape.text_frame.text or "").strip():
                base_slot = _slug_from_text(shape.text_frame.text, f"text_{i}")
                slot_name = base_slot
                counter = 2
                while slot_name in seen_slot_names:
                    slot_name = f"{base_slot}_{counter}"
                    counter += 1
                seen_slot_names.add(slot_name)
                html, spec, _ = _emit_text_shape(shape, scale, slot_name)
                body_chunks.append(html)
                slots[slot_name] = spec
                continue
            if shape_type == MSO_SHAPE_TYPE.PICTURE:
                emitted = _emit_picture_shape(shape, scale)
                if emitted:
                    body_chunks.append(emitted)
                continue
            # rectangles, lines, ellipses, etc.
            emitted = _emit_basic_shape(shape, scale)
            if emitted:
                body_chunks.append(emitted)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning(
                "pptx_to_template: skipping shape on slide %d: %s", slide_idx, exc
            )
            continue

    html = (
        "<!DOCTYPE html>\n"
        f'<html lang="en" data-template-source="pptx" data-page="{slide_idx}">\n'
        "<head>\n"
        '<meta charset="utf-8"/>\n'
        "<style>\n"
        f"  body {{ margin: 0; width: {CANVAS_W}px; height: {CANVAS_H}px; "
        f"background-color: {bg_color}; position: relative; overflow: hidden; "
        f'font-family: system-ui, "Helvetica Neue", sans-serif; color: #111; }}\n'
        "</style>\n"
        "</head>\n"
        "<body>\n"
        + "\n".join(body_chunks)
        + "\n</body>\n</html>\n"
    )
    return html, slots


# ---------------------------------------------------------------------------
# top-level conversion
# ---------------------------------------------------------------------------


def convert_pptx_to_template(
    pptx_path: Path,
    template_id: str,
    template_name: str,
    output_dir: Path,
    description: str = "",
    category: str = "User",
) -> Dict[str, Any]:
    """Convert a .pptx into a slide-template directory layout.

    Args:
        pptx_path: Source .pptx file.
        template_id: Globally unique id (the resulting directory name).
        template_name: Human-readable name surfaced in the gallery.
        output_dir: Where to write meta.json + page_N_*.html + thumbnails/.
        description: Optional blurb for the gallery card.
        category: Category label (defaults to "User").

    Returns:
        ``{"success": True, "template_id", "output_dir", "page_count"}`` on
        success, or ``{"success": False, "error"}`` on failure.
    """
    try:
        prs = Presentation(str(pptx_path))
    except Exception as exc:
        return {"success": False, "error": f"failed to open pptx: {exc}"}

    n_slides = len(prs.slides)
    if n_slides == 0:
        return {"success": False, "error": "pptx contains no slides"}

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "thumbnails").mkdir(exist_ok=True)

    scale = _scaler(prs)
    pages_meta: List[Dict[str, Any]] = []

    for idx, slide in enumerate(prs.slides):
        html, slots = _render_slide(slide, idx, scale)
        file_name = f"page_{idx}_uploaded.html"
        (output_dir / file_name).write_text(html, encoding="utf-8")
        pages_meta.append(
            {
                "idx": idx,
                "file": file_name,
                "layout": f"slide_{idx + 1}",
                "slots": slots,
            }
        )

    meta = {
        "id": template_id,
        "name": template_name,
        "description": description or f"Imported from {pptx_path.name}",
        "category": category,
        "version": "1.0.0",
        "page_count": n_slides,
        "canvas": {"width": CANVAS_W, "height": CANVAS_H},
        "pages": pages_meta,
        "source": "user_uploaded_pptx",
    }
    (output_dir / "meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return {
        "success": True,
        "template_id": template_id,
        "output_dir": str(output_dir),
        "page_count": n_slides,
    }
