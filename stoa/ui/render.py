"""2D plan -> 3D-looking picture with a Gemini image model.

Not a Verda endpoint (the Verda gateway serves no image-generation model), so
it has its own call log, logs/image_calls.jsonl, with the same fields.
"""

import base64
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from stoa import verda  # noqa: F401  (loads .env)

MODEL = os.environ.get("STOA_IMAGE_MODEL", "gemini-3.1-flash-image")
URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
LOG_PATH = Path("logs/image_calls.jsonl")

STYLES = {
    "isometric": "a clean 3D isometric cutaway view from above at about 45 degrees, roof removed, "
                 "walls extruded to ceiling height, soft daylight, white walls, light wood floors",
    "photoreal": "a photorealistic 3D bird's-eye render, roof removed, furnished rooms, natural light",
    "massing": "a minimal grey 3D massing model, walls extruded, no furniture, architectural model look",
}

PROMPT = """Turn this 2D architectural floor plan into {style}.
Keep the layout exactly as drawn: same walls, room shapes and proportions, and every door
and window in the same place. Coloured numbered boxes are annotations from the engineer;
render a window where a box is labelled window and a door where it is labelled door,
and do not show the boxes or numbers themselves. Do not add or remove rooms."""


def render(png: bytes, style: str = "isometric", context: dict | None = None) -> dict:
    """Returns {"image": data URL, "model", "latency_s", "usage"}."""
    t0 = time.perf_counter()
    body = {
        "contents": [{"parts": [
            {"text": PROMPT.format(style=STYLES.get(style, STYLES["isometric"]))},
            {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(png).decode()}},
        ]}],
        "generationConfig": {"responseModalities": ["IMAGE"]},
    }
    error, usage, out = None, {}, None
    try:
        r = requests.post(URL.format(model=MODEL), json=body, timeout=180,
                          headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]})
        data = r.json()
        if r.status_code != 200:
            raise RuntimeError(data.get("error", {}).get("message", r.text[:300]))
        usage = data.get("usageMetadata", {})
        for part in data["candidates"][0]["content"]["parts"]:
            if inline := part.get("inlineData") or part.get("inline_data"):
                mime = inline.get("mimeType") or inline.get("mime_type", "image/png")
                out = f"data:{mime};base64,{inline['data']}"
                break
        if out is None:
            raise RuntimeError("model returned no image")
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        raise
    finally:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a") as f:
            f.write(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "purpose": f"3d render ({style})", "model": MODEL,
                "prompt_tokens": usage.get("promptTokenCount", 0),
                "completion_tokens": usage.get("candidatesTokenCount", 0),
                "latency_s": round(time.perf_counter() - t0, 3), "error": error, **(context or {}),
            }, ensure_ascii=False) + "\n")
    return {"image": out, "model": MODEL, "latency_s": round(time.perf_counter() - t0, 1), "usage": usage}
