"""Web UI server.

    uv run python -m stoa.ui.facts      # once: pre-compute the facts
    uv run uvicorn stoa.ui.server:app --port 8000
"""

import base64
import json
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from stoa import pipeline
from stoa.ui import corrections, library
from stoa.ui import render as render3d
from stoa.ui import uploads
from stoa.ui.facts import DATA, OUT

STATIC = Path(__file__).parent / "static"
EDITS = Path("out/edits")
QUOTES = Path("out/quotes")
OUT.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Mestari plan review")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.mount("/facts", StaticFiles(directory=OUT), name="facts")
app.mount("/data", StaticFiles(directory=DATA), name="data")
uploads.UPLOADS.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=uploads.UPLOADS), name="uploads")
uploads.resume()


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
    return uploads.index() + (json.loads(p.read_text(encoding="utf-8")) if p.exists() else [])


@app.get("/api/library")
def symbol_library():
    """Real symbols of each component kind from all drawings, to stamp on any plan."""
    return library.load()


@app.post("/api/uploads")
async def upload(files: list[UploadFile] = File(...)):
    """Floor plans from the engineer (or an evaluator): facts are computed in the background."""
    if len(files) > uploads.MAX_FILES:
        raise HTTPException(413, f"at most {uploads.MAX_FILES} files per upload")
    got = []
    for f in files:
        data = await f.read()
        if len(data) > uploads.MAX_BYTES:
            raise HTTPException(413, f"{f.filename} is larger than {uploads.MAX_BYTES // 2**20} MB")
        got.append((f.filename or "drawing", data))
    try:
        return await run_in_threadpool(uploads.save, got)
    except Exception as e:  # e.g. an image PIL can't read
        raise HTTPException(400, f"upload failed: {type(e).__name__}: {e}")


@app.get("/api/drawings/{source}/{stem}")
def drawing(source: str, stem: str):
    doc = json.loads(_doc_path(source, stem).read_text(encoding="utf-8"))
    e = _edits_path(source, stem)
    doc["edits"] = json.loads(e.read_text(encoding="utf-8")) if e.exists() else {"added": [], "rejected": []}
    c = e.with_suffix(".cost.json")
    doc["cost"] = json.loads(c.read_text(encoding="utf-8")) if c.exists() else {"lines": {}, "extra": []}
    doc["quotes"] = _quotes_of(f"{source}/{stem}")
    return doc


class Edits(BaseModel):
    added: list[dict] = []
    rejected: list[str] = []  # "<fact kind>:<item ref>" the engineer marked as wrong


@app.put("/api/edits/{source}/{stem}")
def save_edits(source: str, stem: str, edits: Edits):
    p = _edits_path(source, stem)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(edits.model_dump(), ensure_ascii=False), encoding="utf-8")
    return {"ok": True}


class CostEdits(BaseModel):
    lines: dict[str, dict] = {}  # line key (component kind or "floor") -> {qty, material, labour, note} typed by the engineer
    extra: list[dict] = []  # lines the engineer added by hand


@app.put("/api/cost/{source}/{stem}")
def save_cost(source: str, stem: str, cost: CostEdits):
    p = _edits_path(source, stem).with_suffix(".cost.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cost.model_dump(), ensure_ascii=False), encoding="utf-8")
    return {"ok": True}


# ---------- quotes: a frozen copy of the estimate, handed to an approver or the client ----------
def _quote_path(qid: str) -> Path:
    p = QUOTES / f"{qid}.json"
    if not qid.isalnum() or not p.exists():
        raise HTTPException(404, "unknown quote")
    return p


def _summary(q: dict) -> dict:
    return {k: q.get(k) for k in ("id", "version", "to", "by", "created", "total", "status", "decision", "sig")}


def _quotes_of(drawing: str) -> list[dict]:
    if not QUOTES.exists():
        return []
    qs = [json.loads(p.read_text(encoding="utf-8")) for p in QUOTES.glob("*.json")]
    return sorted((_summary(q) for q in qs if q.get("drawing") == drawing), key=lambda q: -q["version"])


class Quote(BaseModel):
    to: str = "approval"  # "approval" (site manager / cost controller) or "client"
    by: str = ""
    note: str = ""
    sig: str = ""  # the estimate's fingerprint, to tell later whether it changed
    total: float = 0
    data: dict  # lines, totals, sources and warnings exactly as the engineer saw them
    plan: str = ""  # data URL of the edited plan


@app.post("/api/quotes/{source}/{stem}")
def submit_quote(source: str, stem: str, q: Quote):
    _doc_path(source, stem)
    drawing = f"{source}/{stem}"
    QUOTES.mkdir(parents=True, exist_ok=True)
    rec = q.model_dump() | {
        "id": secrets.token_hex(6), "drawing": drawing, "version": len(_quotes_of(drawing)) + 1,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "status": "submitted", "decision": None,
    }
    (QUOTES / f"{rec['id']}.json").write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    return _summary(rec)


@app.get("/api/quote/{qid}")
def get_quote(qid: str):
    return json.loads(_quote_path(qid).read_text(encoding="utf-8"))


class Decision(BaseModel):
    status: str  # "approved" or "changes"
    by: str = ""
    comment: str = ""


@app.post("/api/quote/{qid}/decision")
def decide_quote(qid: str, d: Decision):
    if d.status not in ("approved", "changes"):
        raise HTTPException(400, "status must be approved or changes")
    p = _quote_path(qid)
    rec = json.loads(p.read_text(encoding="utf-8"))
    rec["status"] = d.status
    rec["decision"] = d.model_dump() | {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    p.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    return _summary(rec)


@app.get("/quote/{qid}")
def quote_page(qid: str):
    _quote_path(qid)
    return FileResponse(STATIC / "quote.html")


class Ask(BaseModel):
    id: str
    question: str
    router: str | None = None
    edits: Edits | None = None  # the plan as the engineer sees it now; else the saved edits


def _to_view(bbox, frame):
    """DXF metres -> view units, the same transform as stoa.ui.facts.Frame."""
    if not frame or bbox is None:
        return bbox
    x0, y1, s = frame
    return [(bbox[0] - x0) * s, (y1 - bbox[3]) * s, (bbox[2] - x0) * s, (y1 - bbox[1]) * s]


@app.post("/api/ask")
async def ask(q: Ask):
    source, stem = q.id.split("/", 1)
    doc = json.loads(_doc_path(source, stem).read_text(encoding="utf-8"))
    # Uploads carry their own path and kind; dataset drawings are found by source.
    path = Path(doc["path"]) if doc.get("path") else DATA / source / doc["file"]
    res = await run_in_threadpool(pipeline.answer, path, q.question, {"ui": True}, q.router, doc.get("floor_plan"))
    out = res.to_json()
    # Evidence refs (IFC GlobalIds, DXF handles) are the same refs as the
    # pre-computed items, so most shapes are a lookup. DXF evidence that isn't
    # a pre-computed item still has a bbox in DXF metres.
    shapes = {it["ref"]: it["shape"] for f in doc["facts"] for it in f["items"]}
    frame = doc["view"].get("dxf_frame")
    seen, out["items"] = set(), []
    for e in (res.answer.evidence if res.answer else []):
        # Without a DXF frame, evidence boxes are already in view units (CubiCasa
        # PNG pixels, e.g. windows the vision model added).
        shape = shapes.get(e.ref) or ({"bbox": _to_view(e.bbox, frame)} if e.bbox and (frame or doc["source"] == "cubicasa5k") else None)
        if shape and e.ref not in seen:
            seen.add(e.ref)
            out["items"].append({"ref": e.ref, "label": e.label, "value": e.value, "shape": shape})
    out["unplaced"] = len(res.answer.evidence) - len(out["items"]) if res.answer else 0
    # The answer comes from the source file; fold in the engineer's edits to the plan.
    saved = _edits_path(source, stem)
    edits = q.edits.model_dump() if q.edits else json.loads(saved.read_text(encoding="utf-8")) if saved.exists() else None
    if edits and res.answer:
        corrections.apply(doc, edits, q.question, [ev.ref for ev in res.answer.evidence], out)
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
