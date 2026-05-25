"""Slide-deck orchestrator.

Bridges the bundled deck templates (see ``slide_templates/<id>/``) with the
HTML→PPTX converter in :mod:`html_to_pptx`.  Three things happen here:

1. **Discovery** — :func:`list_slide_templates` scans every directory returned
   by :func:`xagent.config.get_slide_template_dirs` and surfaces each template's
   ``meta.json`` plus the list of pre-rendered thumbnails.
2. **Slot filling** — :func:`render_deck` reads a template, substitutes any
   element marked ``data-slot="X"`` with caller-supplied content, and writes
   the resulting per-slide HTML files into a target directory.
3. **Export** — :func:`export_deck_to_pptx` is a thin wrapper over the
   converter so callers don't need to import two modules.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from bs4 import BeautifulSoup

from ....config import get_slide_template_dirs
from ...file_ref import build_workspace_file_ref
from ...workspace import TaskWorkspace
from ..artifacts import build_inline_artifact
from .html_to_pptx import convert_html_to_pptx

logger = logging.getLogger(__name__)

META_FILE = "meta.json"
THUMBNAIL_DIR = "thumbnails"


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------


def _iter_template_dirs() -> List[Path]:
    """Return every concrete template directory across all configured roots.

    A "template directory" is any subdirectory of a configured root that
    contains a ``meta.json`` file.  Later roots win when ids collide, matching
    the documented load order for skill libraries.
    """
    seen: Dict[str, Path] = {}
    for root in get_slide_template_dirs():
        if not root.exists():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            if not (child / META_FILE).exists():
                continue
            seen[child.name] = child
    return list(seen.values())


def _load_template_meta(template_dir: Path) -> Dict[str, Any]:
    meta = json.loads((template_dir / META_FILE).read_text(encoding="utf-8"))
    meta["_dir"] = str(template_dir)
    # surface thumbnail paths so callers can render previews without rescanning
    thumb_dir = template_dir / THUMBNAIL_DIR
    if thumb_dir.exists():
        meta["thumbnails"] = [str(p) for p in sorted(thumb_dir.glob("*.png"))]
    else:
        meta["thumbnails"] = []
    return meta


def list_slide_templates(category: Optional[str] = None) -> Dict[str, Any]:
    """Return summaries of every available slide-deck template.

    Args:
        category: Optional case-insensitive filter on the template's
            ``category`` field.

    Returns:
        ``{"success": True, "templates": [...]}`` where each template entry
        contains ``id``, ``name``, ``description``, ``category``,
        ``page_count`` and ``thumbnails``.  Full per-page slot schemas are
        omitted from the summary; fetch them with :func:`get_slide_template`.
    """
    summaries: List[Dict[str, Any]] = []
    for tdir in _iter_template_dirs():
        try:
            meta = _load_template_meta(tdir)
        except Exception as exc:
            logger.warning("slides_tool: skipping unreadable template %s: %s", tdir, exc)
            continue
        if category and (meta.get("category", "").lower() != category.lower()):
            continue
        summaries.append(
            {
                "id": meta.get("id", tdir.name),
                "name": meta.get("name", tdir.name),
                "description": meta.get("description", ""),
                "category": meta.get("category", ""),
                "page_count": meta.get("page_count", len(meta.get("pages", []))),
                "thumbnails": meta.get("thumbnails", []),
            }
        )
    return {"success": True, "templates": summaries}


def get_slide_template(template_id: str) -> Dict[str, Any]:
    """Return the full ``meta.json`` (including slot schemas) for one template.

    Args:
        template_id: The ``id`` declared in the template's ``meta.json``
            (typically also the directory name).

    Returns:
        ``{"success": True, "template": {...}}`` on success, or
        ``{"success": False, "error": "..."}`` when the id is unknown.
    """
    for tdir in _iter_template_dirs():
        if tdir.name == template_id:
            meta = _load_template_meta(tdir)
            return {"success": True, "template": meta}
    return {"success": False, "error": f"unknown template id: {template_id}"}


# ---------------------------------------------------------------------------
# layouts — each template page exposed as a globally-addressable layout
# ---------------------------------------------------------------------------


LAYOUT_ID_SEP = ":"


def _build_layout_id(template_id: str, page_idx: int) -> str:
    return f"{template_id}{LAYOUT_ID_SEP}{page_idx}"


def _split_layout_id(layout_id: str) -> Optional[tuple[str, int]]:
    """Parse ``<template_id>:<page_idx>`` → ``(template_id, page_idx)``.

    Returns ``None`` if the format is invalid.  Template ids never contain
    a colon, so a simple ``rsplit`` is unambiguous.
    """
    if not isinstance(layout_id, str) or LAYOUT_ID_SEP not in layout_id:
        return None
    template_id, _, idx_str = layout_id.rpartition(LAYOUT_ID_SEP)
    if not template_id or not idx_str:
        return None
    try:
        return template_id, int(idx_str)
    except ValueError:
        return None


def list_slide_layouts() -> Dict[str, Any]:
    """Return every layout (page) across every template as a flat list.

    Used by the deck editor's page picker: the user can compose a deck out of
    any layout from any template, not just the one they started from.

    Returns:
        ``{"success": True, "layouts": [...]}`` where each entry contains
        ``id``, ``template_id``, ``template_name``, ``template_category``,
        ``page_idx``, ``layout`` (the layout name from meta), ``thumbnail_name``
        (the filename inside the template's thumbnails dir), and ``slots``
        (slot schema).  Callers that need to serve a thumbnail URL should
        prefix with ``/api/slide-templates/<template_id>/thumbnails/<idx>.png``.
    """
    layouts: List[Dict[str, Any]] = []
    for tdir in _iter_template_dirs():
        try:
            meta = _load_template_meta(tdir)
        except Exception as exc:
            logger.warning(
                "slides_tool: skipping unreadable template %s: %s", tdir, exc
            )
            continue
        template_id = meta.get("id", tdir.name)
        for page in meta.get("pages", []):
            idx = page.get("idx")
            if not isinstance(idx, int):
                continue
            layouts.append(
                {
                    "id": _build_layout_id(template_id, idx),
                    "template_id": template_id,
                    "template_name": meta.get("name", template_id),
                    "template_category": meta.get("category", ""),
                    "page_idx": idx,
                    "layout": page.get("layout", ""),
                    "file": page.get("file", ""),
                    "slots": page.get("slots", {}),
                }
            )
    return {"success": True, "layouts": layouts}


def resolve_layout(layout_id: str) -> Dict[str, Any]:
    """Look up the template + page meta + html path for a layout id.

    Returns ``{"success": True, "template_id", "page_idx", "page_meta",
    "template_dir", "html_path"}`` on success.  On bad id or missing page,
    returns ``{"success": False, "error"}``.
    """
    parsed = _split_layout_id(layout_id)
    if parsed is None:
        return {"success": False, "error": f"invalid layout_id: {layout_id!r}"}
    template_id, page_idx = parsed
    found = get_slide_template(template_id)
    if not found["success"]:
        return found
    meta = found["template"]
    for page in meta.get("pages", []):
        if page.get("idx") == page_idx:
            template_dir = Path(meta["_dir"])
            return {
                "success": True,
                "template_id": template_id,
                "page_idx": page_idx,
                "page_meta": page,
                "template_dir": template_dir,
                "html_path": template_dir / page["file"],
            }
    return {
        "success": False,
        "error": f"layout '{layout_id}' has no page idx {page_idx} in template {template_id}",
    }


def render_layout_html(
    layout_id: str, slot_values: Dict[str, str]
) -> Dict[str, Any]:
    """Return one layout's HTML with its slots filled in.

    Same behaviour as :func:`render_page_html` but addressed by a layout id
    instead of a (template_id, page_idx) pair.  Used by the deck editor for
    live previews of arbitrary layouts.
    """
    found = resolve_layout(layout_id)
    if not found["success"]:
        return found
    html_path: Path = found["html_path"]
    if not html_path.exists():
        return {"success": False, "error": f"layout missing file: {html_path}"}
    return {
        "success": True,
        "html": _fill_html_slots(html_path.read_text(encoding="utf-8"), slot_values),
    }


# ---------------------------------------------------------------------------
# slot filling
# ---------------------------------------------------------------------------


def render_page_html(
    template_id: str, page_idx: int, slot_values: Dict[str, str]
) -> Dict[str, Any]:
    """Return one page's HTML with its slots filled in (no disk writes).

    Used by the live-preview endpoint: the frontend POSTs the current form
    state on every keystroke and drops the returned HTML into an iframe via
    ``srcDoc``.  Pages not present in the template still return success with
    ``html: ""`` so callers can no-op gracefully.

    Args:
        template_id: Template directory name.
        page_idx: Zero-based page index within the template.
        slot_values: ``{slot_name: text}`` for the page.

    Returns:
        ``{"success": True, "html": "<html>…"}`` on success, or
        ``{"success": False, "error": "…"}`` when the template / page is
        unknown.
    """
    found = get_slide_template(template_id)
    if not found["success"]:
        return found
    meta = found["template"]
    template_dir = Path(meta["_dir"])
    for page in meta.get("pages", []):
        if page.get("idx") == page_idx:
            src = template_dir / page["file"]
            if not src.exists():
                return {"success": False, "error": f"template missing file: {src}"}
            return {
                "success": True,
                "html": _fill_html_slots(src.read_text(encoding="utf-8"), slot_values),
            }
    return {"success": False, "error": f"no page with idx {page_idx} in template {template_id}"}


def _fill_html_slots(html: str, slot_values: Dict[str, str]) -> str:
    """Return ``html`` with every ``data-slot`` element rewritten.

    Elements whose slot name does *not* appear in ``slot_values`` are left
    untouched so the template's own default copy still renders.  ``slot_values``
    entries that don't match any element are silently ignored (they're useful
    as forward-compatibility hints when templates evolve).

    The replacement only touches the element's *text content* — its attributes,
    classes and surrounding markup are preserved.
    """
    soup = BeautifulSoup(html, "html.parser")
    for el in soup.select("[data-slot]"):
        name = el.get("data-slot")
        if not isinstance(name, str):
            continue
        if name not in slot_values:
            continue
        # Clear existing children, then set fresh text.  Using ``clear()`` keeps
        # the element node so CSS targeting still works.
        el.clear()
        el.append(soup.new_string(slot_values[name]))
    return str(soup)


def _validate_slot_values(
    meta: Dict[str, Any], slot_values: Dict[int, Dict[str, str]]
) -> List[str]:
    """Return a list of human-readable errors for missing required slots."""
    errors: List[str] = []
    for page in meta.get("pages", []):
        idx = page.get("idx")
        provided = slot_values.get(idx, {})
        for slot_name, slot_spec in (page.get("slots") or {}).items():
            required = bool(slot_spec.get("required"))
            if required and not provided.get(slot_name):
                errors.append(f"page {idx} missing required slot '{slot_name}'")
    return errors


def render_deck(
    template_id: str,
    slot_values: Dict[int, Dict[str, str]],
    output_dir: str,
    workspace: Optional[TaskWorkspace] = None,
) -> Dict[str, Any]:
    """Materialise a filled deck from a template + per-page slot values.

    Args:
        template_id: Template directory name (matches ``meta.json#id``).
        slot_values: Mapping ``{page_idx: {slot_name: text}}``.  Pages or
            slots not present in this mapping fall back to the template's
            default copy.
        output_dir: Destination directory for the rendered HTML files.  When
            ``workspace`` is provided and the path is relative, it's resolved
            against the workspace root.
        workspace: Optional task workspace used for path resolution.

    Returns:
        On success ``{"success": True, "template_id", "output_dir",
        "html_paths": [...]}``.  On error ``{"success": False, "error"}``.
        Validation problems (missing required slots) come back as a non-empty
        ``errors`` list alongside ``success: False``.
    """
    found = get_slide_template(template_id)
    if not found["success"]:
        return found
    meta: Dict[str, Any] = found["template"]
    template_dir = Path(meta["_dir"])

    errors = _validate_slot_values(meta, slot_values)
    if errors:
        return {"success": False, "error": "missing required slots", "errors": errors}

    out_dir = Path(output_dir)
    if workspace is not None and not out_dir.is_absolute():
        out_dir = workspace.root / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    html_paths: List[str] = []
    for page in meta.get("pages", []):
        src = template_dir / page["file"]
        if not src.exists():
            return {"success": False, "error": f"template missing file: {src}"}
        idx = page.get("idx", len(html_paths))
        filled = _fill_html_slots(src.read_text(encoding="utf-8"), slot_values.get(idx, {}))
        dst = out_dir / src.name
        dst.write_text(filled, encoding="utf-8")
        html_paths.append(str(dst))

    return {
        "success": True,
        "template_id": template_id,
        "output_dir": str(out_dir),
        "html_paths": html_paths,
    }


# ---------------------------------------------------------------------------
# variable-length deck rendering (Phase A: deck.pages with layout refs)
# ---------------------------------------------------------------------------


def _validate_deck_pages(
    pages: Sequence[Dict[str, Any]],
) -> List[str]:
    """Validate a deck.pages list against the layouts it references.

    Returns a list of human-readable errors; empty list means valid.
    """
    errors: List[str] = []
    if not pages:
        errors.append("deck has no pages")
        return errors
    for i, entry in enumerate(pages):
        if not isinstance(entry, dict):
            errors.append(f"page {i}: not an object")
            continue
        layout_id = entry.get("layout_id")
        if not isinstance(layout_id, str) or not layout_id:
            errors.append(f"page {i}: missing layout_id")
            continue
        resolved = resolve_layout(layout_id)
        if not resolved.get("success"):
            errors.append(f"page {i}: {resolved.get('error', 'unresolved layout')}")
            continue
        slot_schema = (resolved["page_meta"].get("slots") or {})
        provided = entry.get("slot_values") or {}
        for slot_name, spec in slot_schema.items():
            if bool(spec.get("required")) and not str(provided.get(slot_name, "")).strip():
                errors.append(
                    f"page {i} ({layout_id}): missing required slot '{slot_name}'"
                )
    return errors


def render_deck_pages(
    pages: Sequence[Dict[str, Any]],
    output_dir: str,
    workspace: Optional[TaskWorkspace] = None,
) -> Dict[str, Any]:
    """Materialise a variable-length deck (Phase A shape) to disk.

    Args:
        pages: Ordered list of ``{"layout_id": "...", "slot_values": {...}}``
            entries.  Each layout id is resolved via :func:`resolve_layout`.
        output_dir: Where to write the per-page HTML files.  Names are
            ``page_<NN>__<original_filename>`` so order is preserved on disk
            and the originating layout is still identifiable.
        workspace: Optional task workspace for relative path resolution.

    Returns:
        On success ``{"success": True, "output_dir", "html_paths": [...]}``.
        On error ``{"success": False, "error", "errors": [...]}``.
    """
    errors = _validate_deck_pages(pages)
    if errors:
        return {"success": False, "error": "invalid deck pages", "errors": errors}

    out_dir = Path(output_dir)
    if workspace is not None and not out_dir.is_absolute():
        out_dir = workspace.root / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    html_paths: List[str] = []
    for i, entry in enumerate(pages):
        resolved = resolve_layout(entry["layout_id"])
        # _validate_deck_pages above guarantees success, but be defensive.
        if not resolved.get("success"):
            return {"success": False, "error": resolved.get("error", "resolve failed")}
        src: Path = resolved["html_path"]
        if not src.exists():
            return {"success": False, "error": f"layout missing file: {src}"}
        slot_values = entry.get("slot_values") or {}
        filled = _fill_html_slots(src.read_text(encoding="utf-8"), slot_values)
        # Prefix with running index so the converter consumes them in order.
        dst = out_dir / f"page_{i:02d}__{src.name}"
        dst.write_text(filled, encoding="utf-8")
        html_paths.append(str(dst))

    return {
        "success": True,
        "output_dir": str(out_dir),
        "html_paths": html_paths,
    }


async def export_deck_pages_to_pptx(
    pages: Sequence[Dict[str, Any]],
    output_pptx_path: str,
    workspace: Optional[TaskWorkspace] = None,
    keep_html: bool = False,
) -> Dict[str, Any]:
    """Render a deck.pages list to HTML, then convert to a single .pptx.

    Companion to the legacy :func:`create_deck_from_template` but driven by
    a Phase A pages array instead of a single template_id.
    """
    if workspace is not None:
        html_out_dir = "decks/deck_html"
    else:
        html_out_dir = str(Path(output_pptx_path).with_suffix("")) + "_html"

    render_result = render_deck_pages(pages, html_out_dir, workspace=workspace)
    if not render_result.get("success"):
        return render_result

    export_result = await convert_html_to_pptx(
        render_result["html_paths"], output_pptx_path, workspace=workspace
    )
    render_result["pptx"] = export_result

    if not keep_html:
        for p in render_result["html_paths"]:
            try:
                Path(p).unlink(missing_ok=True)
            except OSError as exc:  # pragma: no cover
                logger.warning("slides_tool: failed to delete intermediate html %s: %s", p, exc)
        try:
            Path(render_result["output_dir"]).rmdir()
        except OSError:
            pass

    return render_result


def template_to_initial_pages(template_id: str) -> Dict[str, Any]:
    """Bootstrap a deck.pages list from every page of one template.

    Used by ``POST /api/decks`` when the caller passes only a ``template_id``:
    the deck is created with that template's full page sequence as initial
    entries (empty slot_values), which the user can then edit, rearrange, or
    extend with layouts from other templates.

    Returns ``{"success": True, "pages": [...]}`` or
    ``{"success": False, "error": "..."}``.
    """
    found = get_slide_template(template_id)
    if not found["success"]:
        return found
    meta = found["template"]
    pages: List[Dict[str, Any]] = []
    for page in meta.get("pages", []):
        idx = page.get("idx")
        if not isinstance(idx, int):
            continue
        pages.append(
            {
                "layout_id": _build_layout_id(template_id, idx),
                "slot_values": {},
            }
        )
    return {"success": True, "pages": pages}


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


async def export_deck_to_pptx(
    html_paths: Sequence[str],
    output_path: str,
    workspace: Optional[TaskWorkspace] = None,
) -> Dict[str, Any]:
    """Render a sequence of slide HTML files into a single .pptx.

    Thin async wrapper over :func:`html_to_pptx.convert_html_to_pptx` so
    consumers of the slides product can stay on one import path.  When
    ``workspace`` is provided the resulting file is auto-registered and the
    returned dict carries an inline artifact entry.
    """
    return await convert_html_to_pptx(html_paths, output_path, workspace=workspace)


# ---------------------------------------------------------------------------
# convenience: create + export in one call
# ---------------------------------------------------------------------------


async def create_deck_from_template(
    template_id: str,
    slot_values: Dict[int, Dict[str, str]],
    output_pptx_path: str,
    workspace: Optional[TaskWorkspace] = None,
    keep_html: bool = True,
) -> Dict[str, Any]:
    """One-shot: fill a template, write the HTML files, export to PPTX.

    Intended for ``flash``-style single-call agents that want a finished deck
    in one tool invocation.  The intermediate HTML pages are kept by default
    so the user can edit them in the web UI; pass ``keep_html=False`` if you
    only care about the pptx artifact.

    Returns the merged dictionary from :func:`render_deck` plus the export
    result under ``pptx`` (``output_path``, ``size_bytes``, ``file_ref`` …).
    """
    if workspace is not None:
        html_out_dir = "decks/" + template_id + "_html"
    else:
        html_out_dir = str(Path(output_pptx_path).with_suffix("")) + "_html"

    render_result = render_deck(template_id, slot_values, html_out_dir, workspace=workspace)
    if not render_result.get("success"):
        return render_result

    export_result = await export_deck_to_pptx(
        render_result["html_paths"], output_pptx_path, workspace=workspace
    )
    render_result["pptx"] = export_result

    if not keep_html:
        for p in render_result["html_paths"]:
            try:
                Path(p).unlink(missing_ok=True)
            except OSError as exc:  # pragma: no cover — defensive
                logger.warning("slides_tool: failed to delete intermediate html %s: %s", p, exc)
        # also try removing the (now empty) html directory
        try:
            Path(render_result["output_dir"]).rmdir()
        except OSError:
            pass

    return render_result


# ---------------------------------------------------------------------------
# LLM auto-fill: turn a free-text topic into slot values for every page
# ---------------------------------------------------------------------------


def _default_chat_llm() -> Any:
    """Build a default async chat client from environment variables.

    Prefers DeepSeek when ``DEEPSEEK_API_KEY`` is set, otherwise OpenAI.  We
    use the plain :class:`OpenAILLM` for both because DeepSeek's public API is
    OpenAI-compatible — going through ``DeepSeekLLM`` would force model-name
    validation against this fork's internal model registry, which doesn't
    cover DeepSeek's public model identifiers.

    Returns ``None`` when no provider is configured so the caller can surface
    a clean error instead of crashing on construction.
    """
    from ...model.chat.basic.openai import OpenAILLM

    deepseek_key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
    if deepseek_key:
        return OpenAILLM(
            model_name=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
            api_key=deepseek_key,
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
        )
    openai_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if openai_key:
        return OpenAILLM(
            model_name=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            api_key=openai_key,
            base_url=os.getenv("OPENAI_BASE_URL") or None,
        )
    return None


def _build_auto_fill_prompt(meta: Dict[str, Any], topic: str) -> str:
    """Render the slot schema + topic into a single system+user prompt body.

    The LLM is asked to return a JSON object keyed by page index (as strings)
    whose values map slot name → text.  Including each slot's hint and
    max-length gives the model enough context to write tight, on-template
    copy without our having to do post-processing beyond JSON validation.
    """
    lines: List[str] = [
        "You write slide-deck content. Given a topic and a template schema,",
        "produce concise, professional copy for every fillable slot.",
        "",
        f"TOPIC:\n{topic}",
        "",
        f"TEMPLATE: {meta.get('name', meta.get('id'))} "
        f"({meta.get('page_count', '?')} pages, {meta.get('description', '')})",
        "",
        "PAGES:",
    ]
    for page in meta.get("pages", []):
        lines.append(f'- Page {page["idx"]} ({page.get("layout", "")}):')
        for slot_name, spec in (page.get("slots") or {}).items():
            req = " REQUIRED" if spec.get("required") else ""
            max_len = (
                f", max {spec['max_length']} chars" if spec.get("max_length") else ""
            )
            hint = f" — {spec['hint']}" if spec.get("hint") else ""
            lines.append(f"    • {slot_name}{req}{max_len}{hint}")
    lines.extend(
        [
            "",
            "RESPOND WITH ONLY A JSON OBJECT in this exact shape (no markdown,",
            "no commentary). Keys are page indices as strings. Values are objects",
            "mapping slot name → string content. Omit slots you don't have content",
            "for; do not invent slot names that aren't listed above.",
            "",
            "Example shape:",
            '{"0": {"title": "...", "subtitle": "..."}, "1": {"heading": "..."}}',
        ]
    )
    return "\n".join(lines)


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Pull the first balanced JSON object out of an LLM response."""
    text = text.strip()
    # Strip common code-fence wrappings.
    fenced = re.match(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    # Locate the first '{' and walk to its matching '}'.
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _coerce_slot_values(
    meta: Dict[str, Any], raw: Dict[str, Any]
) -> Dict[int, Dict[str, str]]:
    """Filter the LLM's JSON to known slots, enforce max_length, drop nulls."""
    by_idx: Dict[int, Dict[str, Any]] = {}
    for k, v in (raw or {}).items():
        try:
            by_idx[int(k)] = v if isinstance(v, dict) else {}
        except (TypeError, ValueError):
            continue
    cleaned: Dict[int, Dict[str, str]] = {}
    for page in meta.get("pages", []):
        idx = page.get("idx")
        page_in = by_idx.get(idx, {})
        page_out: Dict[str, str] = {}
        for slot_name, spec in (page.get("slots") or {}).items():
            value = page_in.get(slot_name)
            if not isinstance(value, str):
                continue
            value = value.strip()
            if not value:
                continue
            max_len = spec.get("max_length")
            if isinstance(max_len, int) and max_len > 0:
                value = value[:max_len]
            page_out[slot_name] = value
        if page_out:
            cleaned[idx] = page_out
    return cleaned


async def auto_fill_slots(
    template_id: str,
    topic: str,
    llm: Optional[Any] = None,
) -> Dict[str, Any]:
    """Have the configured chat LLM write copy for every slot in a template.

    Args:
        template_id: Template directory name.
        topic: Free-text description of what the deck should cover.
        llm: Optional chat client.  When ``None``, builds one from env
            (``DEEPSEEK_API_KEY`` preferred, otherwise ``OPENAI_API_KEY``).

    Returns:
        ``{"success": True, "slot_values": {idx: {slot: text}, …}, "model":
        "<model name>"}`` on success.  On a configuration or LLM failure,
        ``{"success": False, "error": "…"}``.
    """
    found = get_slide_template(template_id)
    if not found["success"]:
        return found
    meta = found["template"]

    if llm is None:
        llm = _default_chat_llm()
    if llm is None:
        return {
            "success": False,
            "error": (
                "No chat LLM configured. Set DEEPSEEK_API_KEY or OPENAI_API_KEY "
                "in the environment before calling auto-fill."
            ),
        }

    prompt = _build_auto_fill_prompt(meta, topic)
    messages = [
        {"role": "system", "content": "You output strict JSON. No prose."},
        {"role": "user", "content": prompt},
    ]
    try:
        response = await llm.chat(messages=messages, temperature=0.4)
    except Exception as exc:
        logger.exception("slides_tool: auto-fill LLM call failed")
        return {"success": False, "error": f"LLM call failed: {exc}"}

    raw_text: str = ""
    if isinstance(response, dict):
        raw_text = response.get("content") or response.get("text") or ""
    elif isinstance(response, str):
        raw_text = response
    else:
        raw_text = getattr(response, "content", "") or str(response)

    parsed = _extract_json_object(raw_text)
    if parsed is None:
        return {
            "success": False,
            "error": "LLM did not return valid JSON.",
            "raw": raw_text[:500],
        }

    return {
        "success": True,
        "slot_values": _coerce_slot_values(meta, parsed),
        "model": getattr(llm, "model_name", ""),
    }


# ---------------------------------------------------------------------------
# workspace artifact helper (used by the adapter layer)
# ---------------------------------------------------------------------------


def _attach_artifact(
    result: Dict[str, Any], workspace: TaskWorkspace, file_path: str
) -> Dict[str, Any]:
    """Attach a workspace file_ref + inline artifact to a result dict."""
    try:
        file_ref = build_workspace_file_ref(workspace=workspace, file_path=file_path)
        result["file_id"] = file_ref["file_id"]
        result["file_ref"] = file_ref
        result["artifacts"] = [build_inline_artifact(file_ref)]
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("slides_tool: failed to attach artifact for %s: %s", file_path, exc)
        result["file_ref_warning"] = str(exc)
    return result
