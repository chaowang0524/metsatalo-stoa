"""Web UI server.

    uv run python -m stoa.ui.facts      # once: pre-compute the facts
    uv run uvicorn stoa.ui.server:app --port 8000
"""

import base64
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from stoa import pipeline
from stoa.ui import render as render3d
from stoa.ui.facts import DATA, OUT

STATIC = Path(__file__).parent / "static"
EDITS = Path("out/edits")

app = FastAPI(title="Stoa plan review")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.mount("/facts", StaticFiles(directory=OUT), name="facts")
app.mount("/data", StaticFiles(directory=DATA), name="data")


def _doc_path(source: str, stem: str) -> Path:
    p = OUT / source / f"{stem}.json"
    if not p.resolve().is_relative_to(OUT.resolve()) or not p.exists():
        raise HTTPException(404, "unknown drawing")
    return p


def _edits_path(source: str, stem: str) -> Path:
    _doc_path(source, stem)  # validates the name
    return EDITS / source / f"{stem}.json"


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/drawings")
def drawings():
    p = OUT / "index.json"
    return json.loads(p.read_text()) if p.exists() else []


@app.get("/api/drawings/{source}/{stem}")
def drawing(source: str, stem: str):
    doc = json.loads(_doc_path(source, stem).read_text())
    e = _edits_path(source, stem)
    doc["edits"] = json.loads(e.read_text()) if e.exists() else {"added": [], "rejected": []}
    return doc


class Edits(BaseModel):
    added: list[dict] = []
    rejected: list[str] = []  # "<fact kind>:<item ref>" the engineer marked as wrong


@app.put("/api/edits/{source}/{stem}")
def save_edits(source: str, stem: str, edits: Edits):
    p = _edits_path(source, stem)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(edits.model_dump(), ensure_ascii=False))
    return {"ok": True}


class Ask(BaseModel):
    id: str
    question: str
    router: str | None = None


def _to_view(bbox, frame):
    """DXF metres -> view units, the same transform as stoa.ui.facts.Frame."""
    if not frame or bbox is None:
        return bbox
    x0, y1, s = frame
    return [(bbox[0] - x0) * s, (y1 - bbox[3]) * s, (bbox[2] - x0) * s, (y1 - bbox[1]) * s]


@app.post("/api/ask")
async def ask(q: Ask):
    source, stem = q.id.split("/", 1)
    doc = json.loads(_doc_path(source, stem).read_text())
    path = DATA / source / doc["file"]
    res = await run_in_threadpool(pipeline.answer, path, q.question, {"ui": True}, q.router)
    out = res.to_json()
    # Evidence refs (IFC GlobalIds, DXF handles) are the same refs as the
    # pre-computed items, so most shapes are a lookup. DXF evidence that isn't
    # a pre-computed item still has a bbox in DXF metres.
    shapes = {it["ref"]: it["shape"] for f in doc["facts"] for it in f["items"]}
    frame = doc["view"].get("dxf_frame")
    seen, out["items"] = set(), []
    for e in (res.answer.evidence if res.answer else []):
        shape = shapes.get(e.ref) or ({"bbox": _to_view(e.bbox, frame)} if frame and e.bbox else None)
        if shape and e.ref not in seen:
            seen.add(e.ref)
            out["items"].append({"ref": e.ref, "label": e.label, "value": e.value, "shape": shape})
    out["unplaced"] = len(res.answer.evidence) - len(out["items"]) if res.answer else 0
    return out


class Render(BaseModel):
    id: str
    image: str  # data URL of the edited plan, drawn by the browser
    style: str = "isometric"


@app.post("/api/render")
async def render(r: Render):
    png = base64.b64decode(r.image.split(",", 1)[1])
    try:
        return await run_in_threadpool(render3d.render, png, r.style, {"drawing": r.id})
    except Exception as e:
        raise HTTPException(502, f"render failed: {e}")
