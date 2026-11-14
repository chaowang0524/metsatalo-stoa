"""Drawings uploaded through the web UI: classified by content, facts pre-computed.

A drawing's dataset path tells the pipeline what it is (cubicasa5k/ means a
raster floor plan); an upload has no such path, so the kind is decided here
from the files themselves and stored on the facts document.

Files land in out/uploads/ as <batch>_<name>, so the files of one upload pair
up the way the dataset's do (plan.pdf + plan.dxf, model.ifc + model_<level>.svg)
and never collide with another upload. Each drawing gets out/facts/uploads/<stem>.json,
written at once with status "processing" and replaced when its facts are done:

- .dxf (with or without its .pdf): exact facts from the CAD geometry, no LLM.
- .svg plans tagged with IFC GlobalIds (plus the .ifc): facts from plan and model.
- raster images and scanned PDFs: one Gemini full-plan detection call
  (stoa.cubicasa_gemini), cached with the dataset's plans; Ask treats them as
  floor plans, like CubiCasa5K.
- other PDFs: page 1 as the background, no pre-computed facts; Ask uses the PDF tools.
- a bare .ifc: nothing to draw; Ask queries the model.
"""

import io
import json
import re
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pymupdf
from PIL import Image

from stoa import cubicasa_gemini
from stoa.inspect_file import RASTER, inspect_file
from stoa.tools.vision_tools import DETECTOR_CACHE
from stoa.ui.facts import OUT, cubicasa_facts, ifc_facts, swiss_facts

UPLOADS = Path("out/uploads")
DOCS = OUT / "uploads"
INDEX = DOCS / "index.json"
ACCEPT = RASTER | {".pdf", ".dxf", ".svg", ".ifc"}
MAX_FILES = 50
MAX_BYTES = 200 * 1024 * 1024  # IFC models run to ~100 MB
MAX_SIDE = 2048  # rendered PDF pages, as sent to the vision model
CREDIT = "Uploaded drawing"
# Order of preference when one stem comes in several formats.
KINDS = [(".dxf", "dxf"), (".svg", "svg"), (".pdf", "pdf"), *((e, "raster") for e in sorted(RASTER)), (".ifc", "ifc")]
SOURCE = {"dxf": "swiss_dwellings", "svg": "ifc_models", "raster": "cubicasa5k", "pdf": "pdf", "ifc": "ifc"}

_pool = ThreadPoolExecutor(4)  # Gemini detection takes ~1 min per plan
_lock = threading.Lock()


def _safe(name: str) -> str:
    stem, ext = Path(Path(name).name).stem, Path(name).suffix.lower()
    stem = re.sub(r"[^\w-]+", "_", stem).strip("_.")[:80] or "drawing"
    return stem + ext


def index() -> list[dict]:
    return json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else []


def _write(doc: dict) -> None:
    stem = doc["id"].split("/", 1)[1]
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / f"{stem}.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    entry = {"id": doc["id"], "source": "uploads", "file": doc["file"], "status": doc["status"],
             "summary": {f["id"]: f["value"] for f in doc["facts"] if f["group"] == "Components"}}
    with _lock:
        rows = [r for r in index() if r["id"] != doc["id"]] + [entry]
        INDEX.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")


def save(files: list[tuple[str, bytes]]) -> dict:
    """Store one upload and queue its drawings. Returns their index entries."""
    batch = datetime.now().strftime("%m%d-%H%M%S-") + secrets.token_hex(2)
    UPLOADS.mkdir(parents=True, exist_ok=True)
    groups: dict[str, dict[str, tuple[str, Path]]] = {}  # saved stem -> ext -> (original name, path)
    skipped = []
    for name, data in files:
        ext = Path(name).suffix.lower()
        if ext not in ACCEPT:
            skipped.append(name)
            continue
        p = UPLOADS / f"{batch}_{_safe(name)}"
        if ext in RASTER and ext != ".png":  # one image format for the detector and the browser
            p = p.with_suffix(".png")
            Image.open(io.BytesIO(data)).convert("RGB").save(p)
        else:
            p.write_bytes(data)
        groups.setdefault(p.stem, {})[ext] = (Path(name).name, p)
    svgs = [s for s, g in groups.items() if ".svg" in g]
    out = []
    for stem, g in groups.items():
        ext, kind = next((e, k) for e, k in KINDS if e in g)
        if kind == "ifc" and any(s.startswith(stem + "_") for s in svgs):
            continue  # the model behind uploaded plan SVGs, not a drawing of its own
        name, path = g[ext]
        if kind == "dxf" and ".pdf" in g:
            name, path = g[".pdf"]  # the sheet people know; the pipeline finds the .dxf next to it
        doc = {"id": f"uploads/{stem}", "source": SOURCE[kind], "file": name, "path": str(path),
               "kind": kind, "uploaded": True, "status": "processing", "credit": CREDIT,
               "view": _placeholder_view(path, kind), "facts": []}
        _write(doc)
        _pool.submit(_process, doc)
        out.append(doc["id"])
    return {"drawings": out, "skipped": skipped}


def _placeholder_view(path: Path, kind: str) -> dict:
    if kind == "raster":
        w, h = Image.open(path).size
        return {"width": w, "height": h, "background": f"/uploads/{path.name}"}
    return {"width": 1000, "height": 700, "background": ""}


def _render_page(pdf: Path) -> Path:
    png = pdf.with_suffix(".png")
    with pymupdf.open(pdf) as d:
        page = d[0]
        zoom = min(MAX_SIDE / max(page.rect.width, page.rect.height), 4)
        page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).save(png)
    return png


def _process(doc: dict) -> None:
    try:
        doc.update(_facts(doc), status="done")
    except Exception as e:  # keep the drawing: Ask may still work on it
        doc.update(status="failed", error=f"{type(e).__name__}: {e}"[:300])
    doc["credit"] = CREDIT
    _write(doc)


def _facts(doc: dict) -> dict:
    """The fields of the finished document: view, facts and what the pipeline needs."""
    path, kind, stem = Path(doc["path"]), doc["kind"], doc["id"].split("/", 1)[1]
    if kind == "dxf":
        f = swiss_facts(path.with_suffix(".dxf"), svg_path=DOCS / f"{stem}.svg")
        return {"view": f["view"], "facts": f["facts"]}
    if kind == "svg":
        f = ifc_facts(path)
        f["view"]["background"] = f"/uploads/{path.name}"
        return {"view": f["view"], "facts": f["facts"], "ifc": f.get("ifc"), "storey": f.get("storey")}
    if kind == "pdf":
        png = _render_page(path)
        if inspect_file(path).kind != "raster":
            w, h = Image.open(png).size
            return {"view": {"width": w, "height": h, "background": f"/uploads/{png.name}"}, "facts": []}
        doc.update(path=str(png), kind="raster", source=SOURCE["raster"])  # scanned sheet: a picture of a plan
        path, kind = png, "raster"
    if kind == "raster":
        r = cubicasa_gemini.analyse(path, DETECTOR_CACHE)
        if r["status"] == "failed":
            raise RuntimeError(f"Gemini detection failed: {r.get('error', '')}")
        f = cubicasa_facts(path)
        return {"view": f["view"] | {"background": f"/uploads/{path.name}"}, "facts": f["facts"],
                "floor_plan": True, "cost_usd": round(r["cost_usd"], 4)}
    return {}  # bare IFC: nothing to draw


def resume() -> None:
    """Requeue drawings a server restart interrupted."""
    for row in index():
        if row["status"] == "processing":
            p = DOCS / f"{row['id'].split('/', 1)[1]}.json"
            if p.exists():
                _pool.submit(_process, json.loads(p.read_text(encoding="utf-8")))
