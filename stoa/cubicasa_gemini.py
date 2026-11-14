"""Extract CubiCasa-compatible icon/room boxes with Gemini, one PNG per call.

    uv run python -m stoa.cubicasa_gemini --limit 3
    uv run python -m stoa.cubicasa_gemini

Detection files contain exactly file/icons/rooms in original PNG pixels.
Raw responses, input hashes and billed usage live under _audit/. Completed,
validated files with matching input and settings are reused on subsequent runs.
"""

import argparse
import base64
import hashlib
import json
import math
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image
from openai import APIConnectionError

from stoa import verda
from stoa.tools.cubicasa_tools import ICON_CLASSES, ROOM_CLASSES

DATA = Path("junction-dataset-preview/data/cubicasa5k")
CACHE = Path("out/cubicasa_gemini_cache")
MODEL = "gemini-4-hydrogen"
CLASSES = {
    "icons": ICON_CLASSES[1:],
    "rooms": [c for c in ROOM_CLASSES if c not in ("Background", "Wall", "Railing")],
}
PROMPT = """Inspect this residential architectural floor plan in detail and extract
EVERY visible instance of the requested icon and room classes. Work systematically
across the entire image, including every separately drawn floor and visible
neighboring fragments. Use only evidence in the image. Ignore text legends,
dimension marks, compass arrows, furniture other than the requested classes,
and repeated symbols in explanatory diagrams.

Icons use the exact classes Window, Door, Closet, Electrical Appliance, Toilet,
Sink, Sauna Bench, Fire Place, Bathtub, Chimney.
- Window: one wall opening, even when divided into several panes.
- Door: one doorway with its visible door symbol. Double leaves are one doorway.
  Bound the opening and door leaf, excluding the swing arc where possible.
  An open passage with no door symbol is not a door.
- Closet: each distinct built-in wardrobe/cabinet unit. Exclude loose furniture.
- Electrical Appliance: a visible stove/oven, refrigerator, washing machine,
  dishwasher or sauna heater. Exclude unlabeled generic cabinets.
- Sink: one sink fixture, including a double-bowl sink as one fixture.
- Toilet: each toilet bowl. Do not confuse basins, chairs or heaters with toilets.
- Sauna Bench: each distinct continuous bench block in a sauna.
- Fire Place and Chimney: only clearly drawn fireplace/chimney symbols.
- Bathtub: actual tubs. A shower tray/enclosure is not a bathtub.

Room classes are Outdoor, Kitchen, Living Room, Bed Room, Bath, Entry, Storage,
Garage, Undefined. Use labels, boundaries and layout to classify each space.
Outdoor includes each separate balcony/terrace. Bath includes bathroom/WC/wet
rooms. Entry includes hallways and entrance halls. Undefined covers enclosed
spaces with another or uncertain function, including saunas and stairs.
Each separate room needs its own box. A shared open-plan kitchen/living area may
have one box per clearly distinguishable functional region. Keep room boxes
around their interior extent, excluding unrelated rooms and exterior whitespace.

Return icons and rooms arrays with one object per instance. Each object has
class and box_2d. box_2d is [ymin, xmin, ymax, xmax], normalized from 0 to 1000
relative to the ENTIRE supplied image, with origin at top left. Use tight,
positive-area axis-aligned boxes. Include each instance once. Before returning,
inspect all outer and inner walls for windows/doors, every wet room for sanitary
fixtures, and all room labels for missing rooms. Empty arrays mean none visible.
"""


def response_schema() -> dict:
    properties = {}
    for group, classes in CLASSES.items():
        properties[group] = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "class": {"type": "string", "enum": classes},
                    "box_2d": {
                        "type": "array", "items": {"type": "number", "minimum": 0, "maximum": 1000},
                        "minItems": 4, "maxItems": 4,
                    },
                },
                "required": ["class", "box_2d"], "additionalProperties": False,
            },
        }
    return {"type": "object", "properties": properties,
            "required": list(CLASSES), "additionalProperties": False}


def validate_cache(data: dict, filename: str, size: tuple[int, int]) -> None:
    """Reject incompatible classes, duplicates and invalid/out-of-image boxes."""
    if not isinstance(data, dict) or set(data) != {"file", "icons", "rooms"} or data["file"] != filename:
        raise ValueError("Expected file/icons/rooms for the input PNG")
    width, height = size
    for group, classes in CLASSES.items():
        if not isinstance(data[group], list):
            raise ValueError(f"{group} must be an array")
        seen = set()
        for item in data[group]:
            if not isinstance(item, dict) or set(item) != {"class", "bbox"} or item["class"] not in classes:
                raise ValueError(f"Invalid {group} item")
            box = item["bbox"]
            if (not isinstance(box, list) or len(box) != 4
                    or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)):
                raise ValueError("Expected four finite bbox numbers")
            x0, y0, x1, y1 = box
            if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
                raise ValueError(f"Invalid pixel bbox {box} for {size}")
            key = (item["class"], *box)
            if key in seen:
                raise ValueError(f"Duplicate detection {key}")
            seen.add(key)


def to_cache(text: str, filename: str, size: tuple[int, int]) -> dict:
    data = json.loads(text)
    if not isinstance(data, dict) or set(data) != set(CLASSES):
        raise ValueError("Expected icons and rooms arrays")
    width, height = size
    result = {"file": filename, "icons": [], "rooms": []}
    for group in CLASSES:
        if not isinstance(data[group], list):
            raise ValueError(f"{group} must be an array")
        for item in data[group]:
            if not isinstance(item, dict) or set(item) != {"class", "box_2d"}:
                raise ValueError("Expected class and box_2d")
            box = item["box_2d"]
            if (not isinstance(box, list) or len(box) != 4
                    or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1000
                           for v in box)):
                raise ValueError("Expected four finite normalized box coordinates in [0, 1000]")
            y0, x0, y1, x1 = box
            result[group].append({"class": item["class"], "bbox": [
                round(x0 * width / 1000, 1), round(y0 * height / 1000, 1),
                round(x1 * width / 1000, 1), round(y1 * height / 1000, 1),
            ]})
    validate_cache(result, filename, size)
    return result


def _atomic_json(path: Path, value: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         suffix=".tmp", delete=False, newline="\n") as stream:
            temporary = Path(stream.name)
            stream.write(content)
        os.replace(temporary, path)
        return _sha(content.encode())
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def analyse(path: str | Path, cache_dir: Path = CACHE, *, effort: str = "high",
            max_tokens: int = 24576, attempts: int = 3, overwrite: bool = False) -> dict:
    """Extract one image, keeping every billed response even if validation fails."""
    if not 1 <= max_tokens <= 65536 or attempts < 1:
        raise ValueError("max_tokens must be in [1, 65536] and attempts must be positive")
    path, cache_dir = Path(path), Path(cache_dir)
    image = path.read_bytes()
    with Image.open(path) as im:
        size = im.size
    settings = {"model": MODEL, "reasoning_effort": effort, "max_tokens": max_tokens,
                "input_sha256": _sha(image), "prompt_sha256": _sha(PROMPT.encode()),
                "schema_sha256": _sha(json.dumps(response_schema(), sort_keys=True).encode())}
    cached = cache_dir / f"{path.stem}.json"
    audit_path = cache_dir / "_audit" / f"{path.stem}.json"
    previous = {}
    try:
        previous = json.loads(audit_path.read_text(encoding="utf-8"))
        if not isinstance(previous, dict) or not isinstance(previous.get("attempts"), list):
            previous = {}
        content = cached.read_bytes()
        detection = json.loads(content)
        validate_cache(detection, path.name, size)
        if (not overwrite and previous.get("settings") == settings
                and previous.get("output_sha256") == _sha(content)):
            return {"file": path.name, "status": "cached", "cost_usd": 0.0}
    except (OSError, ValueError, TypeError, KeyError):
        pass

    messages = [{"role": "user", "content": [
        {"type": "text", "text": PROMPT},
        {"type": "image_url", "image_url": {
            "url": "data:image/png;base64," + base64.b64encode(image).decode(), "detail": "high"}},
    ]}]
    audit = {"file": path.name, "image_size": list(size), "settings": settings,
             "attempts": previous.get("attempts", []), "status": "running"}
    cost = 0.0
    budget = max_tokens
    request_effort = effort
    history = audit["attempts"]
    if history and history[-1].get("settings") == settings and history[-1].get("finish_reason") == "length":
        previous_budget = history[-1].get("request_max_tokens", max_tokens)
        budget = min(65536, previous_budget * 2)
        if previous_budget >= 65536 and effort in ("high", "xhigh"):
            request_effort = "medium"
    for attempt in range(attempts):
        record = {"ts": datetime.now(timezone.utc).isoformat(), "settings": settings,
                  "request_max_tokens": budget, "request_reasoning_effort": request_effort}
        try:
            response = verda.chat(
                "cubicasa full detection", messages, endpoint="gemini",
                context={"file": str(path), "run": "cubicasa_gemini", "attempt": attempt + 1},
                model=MODEL, reasoning_effort=request_effort, max_tokens=budget, temperature=0,
                response_format={"type": "json_schema", "json_schema": {
                    "name": "floorplan_detections", "strict": True, "schema": response_schema()}},
            )
            record["usage"] = verda.usage_of(response, "gemini")
            cost += record["usage"]["cost_usd"]
            record["finish_reason"] = response.choices[0].finish_reason
            record["response"] = verda.text_of(response)
            if record["finish_reason"] == "length":
                if budget >= 65536 and request_effort in ("high", "xhigh"):
                    request_effort = "medium"
                budget = min(65536, budget * 2)
            if record["finish_reason"] != "stop":
                raise ValueError(f"Incomplete response: {record['finish_reason']}")
            detection = to_cache(record["response"], path.name, size)
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
            audit["attempts"].append(record)
            audit["status"] = "failed"
            _atomic_json(audit_path, audit)
            status = getattr(exc, "status_code", None)
            # Retry transient transport/provider failures and invalid model output.
            retryable = isinstance(exc, (ValueError, TimeoutError, ConnectionError, APIConnectionError)) or status in (408, 429, 500, 502, 503, 504)
            if attempt + 1 == attempts or not retryable:
                return {"file": path.name, "status": "failed", "cost_usd": cost, "error": record["error"]}
            time.sleep(min(5 * 2 ** attempt, 30))
            continue
        audit["attempts"].append(record)
        audit["status"] = "complete"
        audit["output_sha256"] = _atomic_json(cached, detection)
        _atomic_json(audit_path, audit)
        return {"file": path.name, "status": "complete", "cost_usd": cost,
                "icons": len(detection["icons"]), "rooms": len(detection["rooms"])}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DATA)
    parser.add_argument("--output-dir", type=Path, default=CACHE)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--files", nargs="+", help="specific PNG filenames within input-dir")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--effort", choices=["low", "medium", "high", "xhigh"], default="high")
    parser.add_argument("--max-tokens", type=int, default=24576)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if min(args.workers, args.max_tokens, args.attempts) < 1 or (args.limit is not None and args.limit < 1):
        parser.error("workers, max-tokens, attempts and limit must be positive")
    if args.max_tokens > 65536:
        parser.error("max-tokens must be at most 65536")
    files = sorted(args.input_dir.glob("*.png"))
    if args.files:
        wanted = set(args.files)
        files = [p for p in files if p.name in wanted]
        if wanted - {p.name for p in files}:
            parser.error(f"Unknown PNG filenames: {sorted(wanted - {p.name for p in files})}")
    files = files[:args.limit]
    if not files:
        parser.error(f"No PNG images in {args.input_dir}")
    verda.client("gemini")  # Check credentials once before submitting the batch.
    results = []
    print(f"{MODEL}: {len(files)} PNGs, {args.workers} workers, {args.effort} effort -> {args.output_dir}", flush=True)
    with ThreadPoolExecutor(args.workers) as pool:
        pending = {pool.submit(analyse, path, args.output_dir, effort=args.effort,
                               max_tokens=args.max_tokens, attempts=args.attempts,
                               overwrite=args.overwrite): path for path in files}
        for future in as_completed(pending):
            try:
                result = future.result()
            except Exception as exc:
                result = {"file": pending[future].name, "status": "failed", "cost_usd": 0.0,
                          "error": f"{type(exc).__name__}: {exc}"}
            results.append(result)
            print(f"[{len(results)}/{len(files)}] {json.dumps(result)}", flush=True)
    summary = {"model": MODEL, "files": len(files),
               "complete": sum(r["status"] == "complete" for r in results),
               "cached": sum(r["status"] == "cached" for r in results),
               "failed": sum(r["status"] == "failed" for r in results),
               "cost_usd": sum(r["cost_usd"] for r in results),
               "results": sorted(results, key=lambda r: r["file"])}
    run = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    _atomic_json(args.output_dir / "_audit" / f"run-{run}.json", summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}), flush=True)
    if summary["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
