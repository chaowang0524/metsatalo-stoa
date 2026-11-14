"""Gemini Developer API: one key for both uses of Google's models.

- Vision answers (`hybrid`, `hybrid_gemini`) go through Google's OpenAI-compatible
  endpoint via stoa.verda.chat, using BASE_URL / MODEL / prices below.
- The UI's 3D render (stoa/ui/render.py) uses the google-genai SDK via client(),
  with IMAGE_MODEL. Smoke test: uv run python -m endpoints.gemini

The key is GEMINI_API_KEY (Gemini_API_KEY also accepted) in .env, read by
stoa.verda's .env loader; nothing is taken from ~/.zshrc.

Docs: https://aistudio.google.com/docs/openai
Prices: https://aistudio.google.com/docs/pricing#gemini-4-hydrogen
Standard paid-tier USD / million tokens, checked 2026-11-14; estimates do not
apply free-tier allowances or implicit-cache discounts.
"""

import os
from functools import cache
from pathlib import Path

NAME = "gemini"
PROVIDER = "google"
BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
API_KEY_ENV = "GEMINI_API_KEY"
API_KEY_ENV_ALIASES = ("Gemini_API_KEY",)
MODEL = os.environ.get("GEMINI_MODEL", "gemini-4-hydrogen")
VISION = True
PRICE_IN = 1.50
PRICE_OUT = 7.50
DEFAULTS: dict = {"reasoning_effort": "low", "max_tokens": 4096}
# Other image models this key lists: gemini-3.1-flash-image, gemini-3-pro-image,
# nano-banana-pro-preview, gemini-2.5-flash-image.
IMAGE_MODEL = os.environ.get("STOA_IMAGE_MODEL", "gemini-nano-banana-2.1")


def api_key() -> str:
    from stoa import verda  # noqa: F401  (loads .env)

    for name in (API_KEY_ENV, *API_KEY_ENV_ALIASES):
        if key := os.environ.get(name):
            return key
    raise RuntimeError(f"Set {API_KEY_ENV} in .env")


@cache
def client():
    """One shared genai client: a genai.Client closes its connection when garbage-collected."""
    from google import genai

    return genai.Client(api_key=api_key())


if __name__ == "__main__":
    from google.genai import types

    resp = client().models.generate_content(
        model=IMAGE_MODEL, contents="A simple 3D isometric model of a one-room cabin.",
        config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
    )
    for part in resp.candidates[0].content.parts:
        if part.inline_data:
            Path("out").mkdir(exist_ok=True)
            out = Path("out/gemini_smoke." + part.inline_data.mime_type.split("/")[-1])
            out.write_bytes(part.inline_data.data)
            print(IMAGE_MODEL, "->", out)
    print(resp.usage_metadata)
