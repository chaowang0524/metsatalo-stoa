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
- other PDFs: the same Gemini detection on page 1, rendered, for the components;
  rooms and apartments from the text layer's room stamps when it has them (exact).
  Ask keeps the PDF, so its text-layer and schedule tools still apply.
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
from stoa.tools.pdf_tools import pdf_rooms
from stoa.ui.facts import OUT, _fact, cubicasa_facts, ifc_facts, swiss_facts

UPLOADS = Path("out/uploads")
DOCS = OUT / "uploads"
INDEX = DOCS / "index.json"
ACCEPT = RASTER | {".pdf", ".dxf", ".svg", ".ifc"}
MAX_FILES = 50
MAX_BYTES = 200 * 1024 * 1024  # IFC models run to ~100 MB
MAX_SIDE = 2048  # rendered PDF pages, as sent to the vision model
# A dense CAD sheet ran past the default 24576 output tokens and was billed twice
# (VERDA_LOG 2026-11-14), so rendered pages start with the budget the retry needed.
PDF_MAX_TOKENS = 49152
CREDIT = "Uploaded drawing"
# Order of preference when one stem comes in several formats.
KINDS = [(".dxf", "dxf"), (".svg", "svg"), (".pdf", "pdf"), *((e, "raster") for e in sorted(RASTER)), (".ifc", "ifc")]
SOURCE = {"dxf": "swiss_dwellings", "svg": "ifc_models", "raster": "cubicasa5k", "pdf": "pdf", "ifc": "ifc"}

_pool = ThreadPoolExecutor(20)  # Gemini detection takes ~1 min per plan; a batch upload runs side by side
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
    doc_path = DOCS / f"{stem}.json"
    pending = doc_path.with_suffix(".json.tmp")
    pending.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    pending.replace(doc_path)
    entry = {"id": doc["id"], "source": "uploads", "file": doc["file"], "status": doc["status"],
             "background": doc["view"]["background"],
             "summary": {f["id"]: f["value"] for f in doc["facts"] if f["group"] == "Components"}}
    with _lock:
        rows = [r for r in index() if r["id"] != doc["id"]] + [entry]
        pending_index = INDEX.with_suffix(".json.tmp")
        pending_index.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        pending_index.replace(INDEX)


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
    plans = [s for s, g in groups.items() if ".svg" in g or ".pdf" in g]
    out = []
    for stem, g in groups.items():
        ext, kind = next((e, k) for e, k in KINDS if e in g)
        if kind == "ifc" and any(s.startswith(stem + "_") for s in plans):
            continue  # the model behind uploaded plan sheets (<model>_<level>), not a drawing of its own
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


def _render_page(pdf: Path) -> tuple[Path, float]:
    """Page 1 as PNG, and the scale from PDF points to its pixels."""
    png = pdf.with_suffix(".png")
    with pymupdf.open(pdf) as d:
        page = d[0]
        zoom = min(MAX_SIDE / max(page.rect.width, page.rect.height), 4)
        page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).save(png)
    return png, zoom


def _text_rooms(pdf: Path, zoom: float) -> list[dict]:
    """Room and apartment facts from the stamps in the text layer: exact, no LLM.
    On the Swiss sheets they match the DXF room labels on 63 of 65 plans."""
    rooms, units = pdf_rooms(str(pdf))
    if not rooms:
        return []
    def shape(b):
        return {"bbox": [round(v * zoom, 1) for v in b]}
    items = [{"ref": f"stamp-{i}", "label": r["label"] + (f" {r['number']}" if r["number"] else ""),
              "value": r["area"], "shape": shape(r["bbox"])} for i, r in enumerate(rooms)]
    method = "PDF text layer: room name stamps with a room number or area below"
    facts = []
    if units:
        facts.append(_fact("apartment", "Spaces", "Apartments",
                           [{"ref": f"unit-{i}", "label": u["label"], "shape": shape(u["bbox"])} for i, u in enumerate(units)],
                           method="PDF text layer: apartment labels"))
    facts.append(_fact("room", "Spaces", "Rooms", items, method=method))
    names: dict[str, list] = {}
    for r, it in zip(rooms, items):
        names.setdefault(r["label"], []).append(it)
    for name, its in sorted(names.items(), key=lambda x: -len(x[1])):
        facts.append(_fact(f"room:{name}", "Rooms by name", name, its, method=method, kind="room"))
    return facts


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
        return {"view": f["view"], "facts": f["facts"], "dxf": Path(doc["file"]).with_suffix(".dxf").name}
    if kind == "svg":
        f = ifc_facts(path)
        f["view"]["background"] = f"/uploads/{path.name}"
        return {"view": f["view"], "facts": f["facts"], "ifc": f.get("ifc"), "storey": f.get("storey")}
    if kind == "pdf":
        png, zoom = _render_page(path)
        with Image.open(png) as image:
            w, h = image.size
        # Publish the page while Gemini is still working so the UI can preview it.
        doc["view"] = {"width": w, "height": h, "background": f"/uploads/{png.name}"}
        _write(doc)
        info = inspect_file(path)
        if info.ifc or info.dxf:
            # A sheet rendered from an uploaded model: Ask answers from the IFC exactly,
            # so a Gemini guess at the same objects would only cost money.
            return {"view": doc["view"], "facts": []}
        if info.kind != "raster":
            # Vector sheet: Gemini finds the components on the rendered page; rooms come
            # from the text layer when it has room stamps. Ask keeps the PDF itself, so
            # the text-layer and schedule tools still apply.
            out = _detect(png, PDF_MAX_TOKENS)
            if rooms := _text_rooms(path, zoom):
                out["facts"] = [f for f in out["facts"] if f["group"] == "Components"] + rooms
            return out
        doc.update(path=str(png), kind="raster", source=SOURCE["raster"])  # scanned sheet: a picture of a plan
        path, kind = png, "raster"
    if kind == "raster":
        return _detect(path) | {"floor_plan": True}
    return {}  # bare IFC: nothing to draw


def _detect(png: Path, max_tokens: int = 24576) -> dict:
    """One Gemini full-plan detection call: boxes in the PNG's pixels, which are the view's."""
    r = cubicasa_gemini.analyse(png, DETECTOR_CACHE, max_tokens=max_tokens)
    if r["status"] == "failed":
        raise RuntimeError(f"Gemini detection failed: {r.get('error', '')}")
    f = cubicasa_facts(png)
    return {"view": f["view"] | {"background": f"/uploads/{png.name}"}, "facts": f["facts"],
            "cost_usd": round(r["cost_usd"], 4)}


def resume() -> None:
    """Requeue drawings a server restart interrupted."""
    for row in index():
        if row["status"] == "processing":
            p = DOCS / f"{row['id'].split('/', 1)[1]}.json"
            if p.exists():
                _pool.submit(_process, json.loads(p.read_text(encoding="utf-8")))
