"""Symbol library: real plan symbols collected from every pre-computed drawing.

The engineer stamping a missing object on a plan is no longer limited to the
symbols already on that plan: the library holds a few distinct variants of each
component kind (CAD symbols from the Swiss DXFs, element outlines from the IFC
plans), each with its real-world size so it is stamped at the right scale.

    {"kinds": {kind: [{"id", "kind", "label", "sym", "bbox", "size_m": [w, h], "source", "n"}]}}

`sym` is SVG markup in its drawing's view units and `bbox` its extent there, the
same pair the UI already uses to stamp a copy of an on-plan instance.

    uv run python -m stoa.ui.library     # rebuild out/facts/library.json
"""

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

from stoa.ui.facts import OUT

LIBRARY = OUT / "library.json"
SOURCES = ("swiss_dwellings", "ifc_models")
PER_KIND = 8  # variants kept per kind, most common first
DOOR_M = 0.9  # door leaf width used to scale plans without units


def _bbox(shape: dict) -> list[float]:
    if "bbox" in shape:
        return shape["bbox"]
    xs, ys = [p[0] for p in shape["poly"]], [p[1] for p in shape["poly"]]
    return [min(xs), min(ys), max(xs), max(ys)]


def _units_per_m(doc: dict) -> float | None:
    v = doc["view"]
    if v.get("units_per_m"):
        return v["units_per_m"]
    boxes = [_bbox(it["shape"]) for f in doc["facts"] if f["kind"] == "door" for it in f["items"]]
    sides = sorted(max(b[2] - b[0], b[3] - b[1]) for b in boxes)
    return sides[len(sides) // 2] / DOOR_M if sides else None


def _signature(kind: str, sym: str, bbox: list[float], size_m: list[float]) -> str:
    """Same kind, same size (10 cm) and same shape relative to the bbox → one variant.
    Turned copies count as one too: the engineer rotates a stamp with one key."""
    w, h = (bbox[2] - bbox[0]) or 1, (bbox[3] - bbox[1]) or 1
    pts = re.findall(r"(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)", sym)
    shape = sorted({(round((float(x) - bbox[0]) / w * 8), round((float(y) - bbox[1]) / h * 8)) for x, y in pts})
    if h > w:
        shape = sorted((y, x) for x, y in shape)
    a, b = sorted((round(size_m[0] * 10), round(size_m[1] * 10)))
    key = f"{kind}|{a}x{b}|{shape if pts else ''}"
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def build() -> dict:
    found: dict[str, dict] = {}
    for src in SOURCES:
        for p in sorted((OUT / src).glob("*.json")):
            doc = json.loads(p.read_text(encoding="utf-8"))
            upm = _units_per_m(doc)
            if not upm:
                continue
            for f in doc["facts"]:
                if f["group"] != "Components":
                    continue
                for it in f["items"]:
                    if not it.get("sym"):
                        continue
                    b = _bbox(it["shape"])
                    size = [round((b[2] - b[0]) / upm, 2), round((b[3] - b[1]) / upm, 2)]
                    if min(size) <= 0.02 or max(size) > 12:  # degenerate or a mis-scaled plan
                        continue
                    sid = _signature(f["kind"], it["sym"], b, size)
                    if sid in found:
                        found[sid]["n"] += 1
                        continue
                    found[sid] = {"id": sid, "kind": f["kind"], "label": f"{size[0]:.2f} × {size[1]:.2f} m",
                                  "sym": it["sym"], "bbox": b, "size_m": size, "source": doc["id"], "n": 1}
    kinds = defaultdict(list)
    for v in sorted(found.values(), key=lambda v: -v["n"]):
        if len(kinds[v["kind"]]) < PER_KIND:
            kinds[v["kind"]].append(v)
    return {"kinds": dict(sorted(kinds.items()))}


def load() -> dict:
    if not LIBRARY.exists():
        LIBRARY.write_text(json.dumps(build(), ensure_ascii=False), encoding="utf-8")
    return json.loads(LIBRARY.read_text(encoding="utf-8"))


if __name__ == "__main__":
    lib = build()
    LIBRARY.write_text(json.dumps(lib, ensure_ascii=False), encoding="utf-8")
    print({k: len(v) for k, v in lib["kinds"].items()}, f"→ {LIBRARY} ({LIBRARY.stat().st_size // 1024} KB)")
