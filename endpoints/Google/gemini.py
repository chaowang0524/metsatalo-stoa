"""Gemini image generation ("Nano Banana") on the Google Generative Language API.

Not a Verda endpoint: Verda serves no image-generation model. Used by the UI's
3D render (stoa/ui/render.py), which logs each call to logs/image_calls.jsonl.
The key is the GEMINI env var (exported in ~/.zshrc, so run source ~/.zshrc
first in a non-interactive shell). Smoke test: uv run python -m endpoints.Google.gemini
"""

import os
from functools import cache
from pathlib import Path

NAME = "gemini"
API_KEY_ENV = "GEMINI"
# Other image models this key lists: gemini-3.1-flash-image, gemini-3-pro-image,
# nano-banana-pro-preview, gemini-2.5-flash-image.
MODEL = os.environ.get("STOA_IMAGE_MODEL", "gemini-nano-banana-2.1")


def api_key() -> str:
    # Not GEMINI_API_KEY: the one exported in ~/.zshrc is an older key with no credit left.
    if key := os.environ.get(API_KEY_ENV):
        return key
    raise RuntimeError(f"set {API_KEY_ENV} (source ~/.zshrc)")


@cache
def client():
    """One shared client: a genai.Client closes its connection when garbage-collected."""
    from google import genai

    return genai.Client(api_key=api_key())


if __name__ == "__main__":
    from google.genai import types

    resp = client().models.generate_content(
        model=MODEL, contents="A simple 3D isometric model of a one-room cabin.",
        config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
    )
    for part in resp.candidates[0].content.parts:
        if part.inline_data:
            Path("out").mkdir(exist_ok=True)
            out = Path("out/gemini_smoke." + part.inline_data.mime_type.split("/")[-1])
            out.write_bytes(part.inline_data.data)
            print(MODEL, "->", out)
    print(resp.usage_metadata)
