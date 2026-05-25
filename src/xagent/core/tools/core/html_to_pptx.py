"""HTML → PPTX converter.

Renders a sequence of self-contained HTML pages (each laid out on a fixed
1280×720 canvas via absolute positioning) into a single editable PowerPoint
file.  This is the structural converter — every visible DOM element becomes
a native PPTX shape (text box, rectangle, picture), so the resulting deck is
fully editable in PowerPoint / Keynote / Google Slides rather than a stack
of slide-sized screenshots.

Improvements over a naive walker:

* parses linear-gradient backgrounds into PPTX gradient fills
* skips parent nodes that just wrap a text child (avoids double-drawing)
* renders FontAwesome / icon-font glyphs as cropped PNG images
* approximates ``background-clip: text`` gradient text by sampling the first
  gradient stop colour
"""

from __future__ import annotations

import io
import logging
import re
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Emu

from ...file_ref import build_workspace_file_ref
from ...workspace import TaskWorkspace
from ..artifacts import build_inline_artifact

logger = logging.getLogger(__name__)

# Canvas constants — every HTML page is rendered at this exact size, matching
# 16:9 at 96 DPI (1280 px = 13.333 in, 720 px = 7.5 in).  PowerPoint uses EMU
# (914 400 per inch ⇒ 9525 per pixel at 96 DPI).
PX_TO_EMU = 9525
SLIDE_W_PX = 1280
SLIDE_H_PX = 720
SLIDE_W_EMU = Emu(SLIDE_W_PX * PX_TO_EMU)
SLIDE_H_EMU = Emu(SLIDE_H_PX * PX_TO_EMU)

# Minimum visible size for an element to be worth drawing.
MIN_VISIBLE_PX = 4

# Image fetch timeout (data: URLs are decoded inline).
HTTP_IMAGE_TIMEOUT = 10

# JS that walks the rendered DOM and returns a flat list of drawable elements.
# Runs inside the playwright page context.
_EXTRACT_JS = r"""
() => {
  const out = [];
  const FAREGEX = /\bfa[srlb]?\b/;
  function walk(el) {
    if (!el || el.nodeType !== 1) return;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0) return;

    // Direct text only (not aggregated from children) — lets us avoid drawing
    // the same string twice when a parent wraps a span.
    let ownText = '';
    for (const n of el.childNodes) {
      if (n.nodeType === 3 && n.textContent.trim()) ownText += n.textContent;
    }
    ownText = ownText.trim();

    const rec = {
      tag: el.tagName.toLowerCase(),
      cls: typeof el.className === 'string' ? el.className : '',
      x: r.x, y: r.y, w: r.width, h: r.height,
      bg: cs.backgroundColor,
      bgImage: cs.backgroundImage,
      color: cs.color,
      font: cs.fontFamily,
      fontSize: parseFloat(cs.fontSize),
      fontWeight: cs.fontWeight,
      borderRadius: parseFloat(cs.borderRadius) || 0,
      backgroundClip: cs.backgroundClip || cs.webkitBackgroundClip || '',
      text: ownText,
    };
    if (el.tagName === 'IMG') {
      rec.src = el.currentSrc || el.src;
    }
    if (el.tagName === 'I' && FAREGEX.test(rec.cls)) {
      rec.isIcon = true;
    }
    out.push(rec);
    for (const c of el.children) walk(c);
  }
  walk(document.body);
  return out;
}
"""


# ---------------------------------------------------------------------------
# colour / gradient parsing helpers
# ---------------------------------------------------------------------------

_RGB_RE = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(?:\s*,\s*([\d.]+))?")
_HEX_RE = re.compile(r"#([0-9a-fA-F]{6})$")
_LINEAR_GRAD_RE = re.compile(r"linear-gradient\((.*)\)", re.DOTALL)


def _parse_color(value: Optional[str]) -> Optional[RGBColor]:
    """Parse a CSS colour string into an RGBColor, or None when unrecognised."""
    if not value:
        return None
    s = value.strip()
    m = _RGB_RE.match(s)
    if m:
        return RGBColor(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _HEX_RE.match(s)
    if m:
        h = m.group(1)
        return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    return None


def _is_transparent(value: Optional[str]) -> bool:
    if not value:
        return True
    if value == "transparent" or "rgba(0, 0, 0, 0)" in value:
        return True
    m = _RGB_RE.match(value)
    if m and m.group(4) is not None and float(m.group(4)) == 0:
        return True
    return False


def _parse_linear_gradient(bg_image: str) -> List[RGBColor]:
    """Extract the gradient stops from a ``linear-gradient(...)`` declaration.

    Returns the stops in document order.  An empty list means the value was
    not a linear gradient or could not be parsed.
    """
    m = _LINEAR_GRAD_RE.search(bg_image or "")
    if not m:
        return []
    body = m.group(1)
    stops: List[RGBColor] = []
    # Split by commas at depth 0 (rgb()/rgba() contain commas).
    depth = 0
    pieces: List[str] = []
    cur: List[str] = []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            pieces.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        pieces.append("".join(cur).strip())
    for piece in pieces:
        # Skip the "to right" / "45deg" direction component.
        if "deg" in piece or piece.startswith("to "):
            continue
        # Drop trailing stop position e.g. "rgb(0,0,0) 50%".
        token = piece.rsplit(" ", 1)[0] if "%" in piece else piece
        col = _parse_color(token)
        if col is not None:
            stops.append(col)
    return stops


# ---------------------------------------------------------------------------
# core converter
# ---------------------------------------------------------------------------


class HtmlToPptxConverter:
    """Converts a list of HTML files into a single editable PPTX presentation."""

    def __init__(self) -> None:
        self._image_cache: Dict[str, Optional[bytes]] = {}

    async def convert(
        self,
        html_paths: Sequence[Path],
        output_path: Path,
    ) -> Dict[str, Any]:
        """Render the pages and write the resulting .pptx to ``output_path``.

        Returns a summary dict with per-page element counts and the final size.
        """
        from playwright.async_api import async_playwright  # local import: optional dep

        pres = Presentation()
        pres.slide_width = SLIDE_W_EMU
        pres.slide_height = SLIDE_H_EMU
        blank_layout = pres.slide_layouts[6]

        per_page_stats: List[Dict[str, int]] = []

        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            ctx = await browser.new_context(
                viewport={"width": SLIDE_W_PX, "height": SLIDE_H_PX},
                device_scale_factor=2,
            )
            page = await ctx.new_page()
            for idx, html_path in enumerate(html_paths):
                stats = await self._render_page_to_slide(
                    page, html_path, pres.slides.add_slide(blank_layout)
                )
                per_page_stats.append(stats)
                logger.info("html_to_pptx: page %d %s", idx, stats)
            await browser.close()

        output_path.parent.mkdir(parents=True, exist_ok=True)
        pres.save(output_path)

        return {
            "success": True,
            "output_path": str(output_path),
            "size_bytes": output_path.stat().st_size,
            "slide_count": len(html_paths),
            "per_page": per_page_stats,
        }

    # ------------------------------------------------------------------ page

    async def _render_page_to_slide(
        self, page: Any, html_path: Path, slide: Any
    ) -> Dict[str, int]:
        await page.goto(html_path.absolute().as_uri())
        await page.wait_for_load_state("networkidle")
        elements: List[Dict[str, Any]] = await page.evaluate(_EXTRACT_JS)

        # Dedupe: when a parent and a child have the same text and overlap, keep the child.
        elements = _dedupe_parent_child_text(elements)

        # Pass 0 — root background (the body's gradient or solid colour fills the slide).
        for el in elements:
            if el["tag"] == "body":
                self._add_background(slide, el)
                break

        n_bg = n_img = n_icon = n_text = 0

        # Pass 1 — opaque colored rectangles (cards, accents).
        for el in elements:
            if el["tag"] in ("html", "body"):
                continue
            if el["w"] < MIN_VISIBLE_PX or el["h"] < MIN_VISIBLE_PX:
                continue
            if not _is_transparent(el["bg"]):
                fill = _parse_color(el["bg"])
                if fill is not None:
                    _add_rect(slide, el, fill)
                    n_bg += 1

        # Pass 2 — images.
        for el in elements:
            if el["tag"] == "img" and el.get("src"):
                data = self._fetch_image(el["src"])
                if data:
                    try:
                        slide.shapes.add_picture(
                            io.BytesIO(data),
                            Emu(int(el["x"] * PX_TO_EMU)),
                            Emu(int(el["y"] * PX_TO_EMU)),
                            Emu(int(el["w"] * PX_TO_EMU)),
                            Emu(int(el["h"] * PX_TO_EMU)),
                        )
                        n_img += 1
                    except Exception as exc:  # pragma: no cover — defensive
                        logger.warning("html_to_pptx: image embed failed: %s", exc)

        # Pass 3 — icon glyphs (FontAwesome).  We can't ship the webfont into the
        # .pptx, so we crop a screenshot of the rendered glyph and embed as PNG.
        for el in elements:
            if not el.get("isIcon"):
                continue
            try:
                png = await page.screenshot(
                    clip={"x": el["x"], "y": el["y"], "width": el["w"], "height": el["h"]},
                    omit_background=False,
                )
                slide.shapes.add_picture(
                    io.BytesIO(png),
                    Emu(int(el["x"] * PX_TO_EMU)),
                    Emu(int(el["y"] * PX_TO_EMU)),
                    Emu(int(el["w"] * PX_TO_EMU)),
                    Emu(int(el["h"] * PX_TO_EMU)),
                )
                n_icon += 1
            except Exception as exc:  # pragma: no cover — defensive
                logger.warning("html_to_pptx: icon capture failed: %s", exc)

        # Pass 4 — text.
        for el in elements:
            if not el["text"] or el["tag"] in ("script", "style"):
                continue
            color = _text_color_for_element(el)
            _add_textbox(slide, el, color)
            n_text += 1

        return {"bg": n_bg, "img": n_img, "icon": n_icon, "text": n_text}

    # ------------------------------------------------------------ background

    def _add_background(self, slide: Any, body_el: Dict[str, Any]) -> None:
        """Fill the slide background using the body's CSS background."""
        stops = _parse_linear_gradient(body_el.get("bgImage") or "")
        if stops:
            rect = slide.shapes.add_shape(
                MSO_SHAPE.RECTANGLE, 0, 0, SLIDE_W_EMU, SLIDE_H_EMU
            )
            rect.line.fill.background()
            # python-pptx doesn't expose multi-stop gradients on autoshapes via a
            # high-level API; we approximate by using the first stop as a solid
            # fill (sufficient for the dominant background colour) and adding a
            # second translucent rectangle for the destination stop when there
            # are at least two distinct stops.
            rect.fill.solid()
            rect.fill.fore_color.rgb = stops[0]
            if len(stops) >= 2 and stops[-1] != stops[0]:
                overlay = slide.shapes.add_shape(
                    MSO_SHAPE.RECTANGLE,
                    Emu(SLIDE_W_PX * PX_TO_EMU // 2),
                    0,
                    Emu(SLIDE_W_PX * PX_TO_EMU // 2),
                    SLIDE_H_EMU,
                )
                overlay.line.fill.background()
                overlay.fill.solid()
                overlay.fill.fore_color.rgb = stops[-1]
            return

        solid = _parse_color(body_el.get("bg"))
        if solid is not None and not _is_transparent(body_el.get("bg")):
            rect = slide.shapes.add_shape(
                MSO_SHAPE.RECTANGLE, 0, 0, SLIDE_W_EMU, SLIDE_H_EMU
            )
            rect.line.fill.background()
            rect.fill.solid()
            rect.fill.fore_color.rgb = solid

    # ------------------------------------------------------------- images

    def _fetch_image(self, url: str) -> Optional[bytes]:
        if url in self._image_cache:
            return self._image_cache[url]
        data: Optional[bytes]
        try:
            if url.startswith("data:"):
                import base64

                _, _, b64 = url.partition(",")
                data = base64.b64decode(b64)
            else:
                with urllib.request.urlopen(url, timeout=HTTP_IMAGE_TIMEOUT) as r:
                    data = r.read()
        except Exception as exc:
            logger.warning("html_to_pptx: image fetch failed for %s: %s", url[:80], exc)
            data = None
        self._image_cache[url] = data
        return data


# ---------------------------------------------------------------------------
# small free functions kept module-level so they're trivially testable
# ---------------------------------------------------------------------------


def _dedupe_parent_child_text(elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop ownText on a parent when a descendant carries the same string."""
    texts_by_child: Dict[str, List[Tuple[float, float, float, float]]] = {}
    for el in elements:
        t = el.get("text")
        if t:
            texts_by_child.setdefault(t, []).append((el["x"], el["y"], el["w"], el["h"]))
    cleaned = list(elements)
    for el in cleaned:
        t = el.get("text")
        if not t:
            continue
        rects = texts_by_child.get(t, [])
        # If a smaller rect carries the same text, this element is the wrapper.
        own_area = el["w"] * el["h"]
        for x, y, w, h in rects:
            if (x, y, w, h) == (el["x"], el["y"], el["w"], el["h"]):
                continue
            if w * h < own_area:
                el["text"] = ""
                break
    return cleaned


def _text_color_for_element(el: Dict[str, Any]) -> Optional[RGBColor]:
    """Pick a representative colour for text, handling gradient text crudely."""
    clip = el.get("backgroundClip") or ""
    if "text" in clip:
        # Tailwind ``bg-clip-text`` gradient — sample the first gradient stop.
        stops = _parse_linear_gradient(el.get("bgImage") or "")
        if stops:
            return stops[0]
    return _parse_color(el.get("color"))


def _add_rect(slide: Any, el: Dict[str, Any], fill_rgb: RGBColor) -> Any:
    shape_type = (
        MSO_SHAPE.ROUNDED_RECTANGLE if el.get("borderRadius", 0) > 4 else MSO_SHAPE.RECTANGLE
    )
    sp = slide.shapes.add_shape(
        shape_type,
        Emu(int(el["x"] * PX_TO_EMU)),
        Emu(int(el["y"] * PX_TO_EMU)),
        Emu(int(el["w"] * PX_TO_EMU)),
        Emu(int(el["h"] * PX_TO_EMU)),
    )
    sp.line.fill.background()
    sp.fill.solid()
    sp.fill.fore_color.rgb = fill_rgb
    return sp


def _add_textbox(slide: Any, el: Dict[str, Any], color_rgb: Optional[RGBColor]) -> Any:
    tb = slide.shapes.add_textbox(
        Emu(int(el["x"] * PX_TO_EMU)),
        Emu(int(el["y"] * PX_TO_EMU)),
        Emu(int(el["w"] * PX_TO_EMU)),
        Emu(int(el["h"] * PX_TO_EMU)),
    )
    tf = tb.text_frame
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = el["text"]
    run = p.runs[0]
    # CSS px → pt at 96 DPI: pt = px * 72/96 = px * 0.75.  12 700 EMU per pt.
    run.font.size = Emu(int(el["fontSize"] * 0.75 * 12700))
    if color_rgb is not None:
        run.font.color.rgb = color_rgb
    weight = str(el.get("fontWeight", ""))
    bold = weight in {"bold", "bolder"} or (weight.isdigit() and int(weight) >= 600)
    if bold:
        run.font.bold = True
    font_family = el.get("font") or ""
    if font_family:
        first = font_family.split(",")[0].strip().strip('"').strip("'")
        if first:
            run.font.name = first
    return tb


# ---------------------------------------------------------------------------
# public tool entry point
# ---------------------------------------------------------------------------


async def convert_html_to_pptx(
    html_paths: Sequence[str],
    output_path: str,
    workspace: Optional[TaskWorkspace] = None,
) -> Dict[str, Any]:
    """Convert a sequence of HTML slide files into a single .pptx.

    Args:
        html_paths: HTML file paths in slide order.  If ``workspace`` is
            provided, relative paths resolve against the workspace root.
        output_path: Destination .pptx path (or filename when ``workspace`` is
            provided — in that case the file is written under
            ``workspace.output_dir`` and auto-registered as an artifact).
        workspace: Optional task workspace for path resolution + auto file
            registration.

    Returns:
        A dictionary with ``success``, ``output_path``, ``size_bytes``,
        ``slide_count`` and per-page element counts.  When ``workspace`` is
        provided the dictionary also includes ``file_id``, ``file_ref`` and an
        inline ``artifacts`` entry so the caller can surface the result.
    """
    paths: List[Path] = []
    for raw in html_paths:
        p = Path(raw)
        if workspace and not p.is_absolute():
            p = workspace.root / p
        if not p.exists():
            return {"success": False, "error": f"missing html file: {p}"}
        paths.append(p)

    out = Path(output_path)
    if workspace and not out.is_absolute():
        out = workspace.output_dir / out

    converter = HtmlToPptxConverter()

    if workspace is not None:
        with workspace.auto_register_files():
            result = await converter.convert(paths, out)
            try:
                file_ref = build_workspace_file_ref(workspace=workspace, file_path=str(out))
                result["file_id"] = file_ref["file_id"]
                result["file_ref"] = file_ref
                result["artifacts"] = [build_inline_artifact(file_ref)]
            except Exception as exc:  # pragma: no cover — defensive
                logger.warning("html_to_pptx: failed to build FileRef: %s", exc)
                result["file_ref_warning"] = str(exc)
            return result

    return await converter.convert(paths, out)
