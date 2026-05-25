#!/usr/bin/env python3
"""Render slide template HTML pages to PNG thumbnails.

Usage:
  python scripts/generate_template_thumbnails.py [template_id ...]

If template_ids are given, only those are rendered; otherwise every dir under
slide_templates/ with a meta.json is processed. Output: 2560x1440 PNGs in
<template_dir>/thumbnails/page_<idx>_<layout>.png.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = REPO_ROOT / "src" / "xagent" / "core" / "tools" / "core" / "slide_templates"

CANVAS_W = 1280
CANVAS_H = 720
SCALE = 2  # → 2560x1440 thumbnails


def render_template(template_dir: Path, page: "Page") -> None:
    meta = json.loads((template_dir / "meta.json").read_text())
    thumbs = template_dir / "thumbnails"
    thumbs.mkdir(exist_ok=True)
    for entry in meta["pages"]:
        html_path = template_dir / entry["file"]
        if not html_path.exists():
            print(f"  skip: {html_path.name} missing"); continue
        out_name = html_path.stem + ".png"
        out_path = thumbs / out_name
        page.goto(html_path.as_uri(), wait_until="networkidle")
        page.wait_for_timeout(400)  # let webfonts settle
        page.screenshot(path=str(out_path), clip={"x": 0, "y": 0, "width": CANVAS_W, "height": CANVAS_H})
        print(f"  ✓ {template_dir.name}/{out_name}")


def main(argv: list[str]) -> int:
    wanted = set(argv[1:])
    dirs = sorted(p for p in TEMPLATES_DIR.iterdir() if p.is_dir() and (p / "meta.json").exists())
    if wanted:
        dirs = [d for d in dirs if d.name in wanted]
        missing = wanted - {d.name for d in dirs}
        for m in missing:
            print(f"warn: no template dir for '{m}'", file=sys.stderr)
    if not dirs:
        print("nothing to render"); return 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(
            viewport={"width": CANVAS_W, "height": CANVAS_H},
            device_scale_factor=SCALE,
        )
        page = context.new_page()
        for tdir in dirs:
            print(f"→ {tdir.name}")
            render_template(tdir, page)
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
