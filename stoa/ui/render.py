"""2D plan -> 3D-looking picture with a Gemini image model.

Not a Verda endpoint (the Verda gateway serves no image-generation model), so
it has its own call log, logs/image_calls.jsonl, with the same fields.
"""

import base64
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from google.genai import types

from endpoints.Google import gemini
from stoa import verda  # noqa: F401  (loads .env)

MODEL = gemini.MODEL
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
    error, usage, out = None, {}, None
    try:
        resp = gemini.client().models.generate_content(
            model=MODEL,
            contents=[PROMPT.format(style=STYLES.get(style, STYLES["isometric"])),
                      types.Part.from_bytes(data=png, mime_type="image/png")],
            config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
        )
        usage = resp.usage_metadata.model_dump(by_alias=True, exclude_none=True, mode="json") if resp.usage_metadata else {}
        for part in resp.candidates[0].content.parts if resp.candidates else []:
            if part.inline_data:
                out = f"data:{part.inline_data.mime_type};base64,{base64.b64encode(part.inline_data.data).decode()}"
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
