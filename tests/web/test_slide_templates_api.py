"""Integration tests for the slide-templates HTTP API.

Uses a minimal FastAPI app with the router mounted and the auth dependency
stubbed out, so we don't have to spin up the full xagent webserver (with its
DB / langfuse / websocket scaffolding) just to test these endpoints.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xagent.web.api.slide_templates import router
from xagent.web.auth_dependencies import get_current_user


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    # bypass real auth: any request is treated as a logged-in test user
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, username="tester")
    return app


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(_build_app())


def test_list_returns_midnight_tech(client: TestClient) -> None:
    r = client.get("/api/slide-templates/")
    assert r.status_code == 200
    body = r.json()
    ids = {t["id"] for t in body}
    assert "midnight_tech" in ids
    mt = next(t for t in body if t["id"] == "midnight_tech")
    assert mt["page_count"] == 5
    assert len(mt["thumbnail_urls"]) == 5
    assert mt["thumbnail_urls"][0].endswith("/0.png")


def test_list_category_filter(client: TestClient) -> None:
    r = client.get("/api/slide-templates/", params={"category": "Tech"})
    assert r.status_code == 200
    assert all(t["category"].lower() == "tech" for t in r.json())

    r = client.get("/api/slide-templates/", params={"category": "Nonexistent"})
    assert r.status_code == 200 and r.json() == []


def test_detail_includes_slot_schema(client: TestClient) -> None:
    r = client.get("/api/slide-templates/midnight_tech")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "midnight_tech"
    assert len(body["pages"]) == 5
    cover = body["pages"][0]
    assert cover["layout"] == "cover"
    assert cover["slots"]["title"]["required"] is True
    assert cover["slots"]["subtitle"]["required"] is False


def test_detail_unknown_id_returns_404(client: TestClient) -> None:
    r = client.get("/api/slide-templates/no_such_template")
    assert r.status_code == 404


def test_thumbnail_by_index_and_filename(client: TestClient) -> None:
    # positional
    r = client.get("/api/slide-templates/midnight_tech/thumbnails/0.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert len(r.content) > 10_000  # 2x DPI screenshots are ~1 MB

    # exact-filename lookup also works
    r = client.get(
        "/api/slide-templates/midnight_tech/thumbnails/page_0_cover.png"
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"


def test_thumbnail_missing_returns_404(client: TestClient) -> None:
    r = client.get("/api/slide-templates/midnight_tech/thumbnails/99.png")
    assert r.status_code == 404


def test_render_validates_required_slots(client: TestClient) -> None:
    r = client.post(
        "/api/slide-templates/midnight_tech/render",
        json={"slot_values": {}},
    )
    assert r.status_code == 400
    body = r.json()
    assert "errors" in body["detail"]
    assert any("title" in e for e in body["detail"]["errors"])


@pytest.mark.slow
def test_render_produces_pptx(client: TestClient, tmp_path: Path) -> None:
    pytest.importorskip("playwright.async_api")
    payload = {
        "filename": "smoke_deck",
        "slot_values": {
            "0": {"title": "API Smoke", "subtitle": "rendered by the API"},
            "1": {
                "heading": "Plan",
                "item_1": "One",
                "item_2": "Two",
                "item_3": "Three",
            },
            "2": {"title": "Section"},
            "3": {
                "heading": "Three features",
                "feat_1_title": "A",
                "feat_1_body": "a",
                "feat_2_title": "B",
                "feat_2_body": "b",
                "feat_3_title": "C",
                "feat_3_body": "c",
            },
            "4": {"headline": "Done"},
        },
    }
    r = client.post("/api/slide-templates/midnight_tech/render", json=payload)
    assert r.status_code == 200
    assert (
        r.headers["content-type"]
        == "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )
    assert "smoke_deck.pptx" in r.headers["content-disposition"]
    # response body is the actual pptx — must be a non-trivial zip
    assert r.content.startswith(b"PK")
    assert len(r.content) > 5_000
