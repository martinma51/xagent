"""AI Image generation API (Phase D4).

The xAgent Widgets v0.4 spec calls out AI Images as one of the two widgets
to ship first. This module powers the ``/images`` landing page's Generate
button by proxying user prompts to `pollinations.ai`_ — a keyless,
publicly-available image generation service backed by Flux/SDXL.

We deliberately keep this thin: no API key handshake, no model selection
matrix, and no server-side image storage. The endpoint just returns
fully-formed pollinations URLs that the browser fetches directly as
``<img src>`` — a pattern that:

- works with zero configuration (no ``OPENAI_API_KEY`` plumbing),
- streams images progressively as the model finishes them,
- doesn't put image bytes through our backend's memory footprint.

NETWORK CAVEAT
--------------
``image.pollinations.ai`` is hosted outside the GFW so users on Chinese
ISPs may see SSL handshake failures. The frontend handles the broken
image case with a "service unreachable" tile. To unblock those users:

1. Run the app from a host that can reach pollinations, or
2. Wire a Chinese-cloud image provider (DashScope/Tongyi Wanxiang or
   ZhipuAI/CogView) — both expose an OpenAI-compatible image endpoint
   so swapping just the URL builder below is enough. Track via
   ``XAGENT_IMAGE_PROVIDER`` env once we have a second provider.

If a future phase needs auditability or offline reliability we can swap
the body for a server-side fetch + save under
``~/.xagent/generated_images/{user_id}/`` without changing the API
contract — the response shape (a list of URLs) survives the swap.

.. _pollinations.ai: https://pollinations.ai
"""

from __future__ import annotations

import logging
import random
import urllib.parse
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..auth_dependencies import get_current_user
from ..models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/images", tags=["images"])

# Pollinations exposes a GET-only "fetch and you get a PNG" endpoint —
# the URL itself is the cache key.  We append ``nologo=true`` so the
# returned image has no watermark and ``model=flux`` because Flux gives
# noticeably better composition than the default model on style-heavy
# prompts.
POLLINATIONS_BASE = "https://image.pollinations.ai/prompt"
DEFAULT_MODEL = "flux"


class ImageGenerateRequest(BaseModel):
    prompt: str = Field(
        ..., min_length=2, max_length=1500,
        description="What the image should depict.",
    )
    style_description: Optional[str] = Field(
        default=None, max_length=300,
        description=(
            "Visual style preamble — gets prepended to the prompt. "
            "Comes from the style card the user picked on /images."
        ),
    )
    n: int = Field(
        default=4, ge=1, le=8,
        description="How many variants to generate (each uses a fresh seed).",
    )
    width: int = Field(default=1024, ge=256, le=2048)
    height: int = Field(default=1024, ge=256, le=2048)


class GeneratedImage(BaseModel):
    url: str = Field(..., description="Direct URL the client should fetch / display.")
    seed: int
    prompt: str = Field(..., description="Final prompt sent to the model (style + user input).")


class ImageGenerateResponse(BaseModel):
    success: bool = True
    images: List[GeneratedImage]
    model: str = Field(default=f"pollinations-{DEFAULT_MODEL}")


def _build_url(prompt: str, *, seed: int, width: int, height: int) -> str:
    encoded = urllib.parse.quote(prompt, safe="")
    return (
        f"{POLLINATIONS_BASE}/{encoded}"
        f"?width={width}&height={height}"
        f"&seed={seed}&nologo=true&model={DEFAULT_MODEL}"
    )


@router.post("/generate", response_model=ImageGenerateResponse)
async def generate_image(
    body: ImageGenerateRequest,
    current_user: User = Depends(get_current_user),
) -> ImageGenerateResponse:
    """Generate ``n`` images for one prompt by handing it to pollinations.ai.

    The returned URLs are static — the browser hits pollinations directly
    when it loads the ``<img>``. Pollinations renders synchronously per
    request and replies with a PNG body, so the very first paint shows a
    loading state and the image fills in once Flux finishes (~5-15 s per
    image, parallel by browser).

    No image bytes pass through our server — the URL is the API surface.
    """
    full_prompt = body.prompt.strip()
    if body.style_description:
        # Style preamble first; the user prompt is the noun phrase.
        full_prompt = f"{body.style_description.strip()}. {full_prompt}"

    base_seed = random.randint(1, 1_000_000)
    images: List[GeneratedImage] = []
    for i in range(body.n):
        seed = base_seed + i
        images.append(
            GeneratedImage(
                url=_build_url(full_prompt, seed=seed, width=body.width, height=body.height),
                seed=seed,
                prompt=full_prompt,
            )
        )

    logger.info(
        "images: generated %s URLs for user %s (style=%s, prompt_len=%s)",
        len(images),
        int(current_user.id),
        bool(body.style_description),
        len(body.prompt),
    )
    return ImageGenerateResponse(success=True, images=images)
