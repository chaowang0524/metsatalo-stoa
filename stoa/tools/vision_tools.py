"""Fallback: show the drawing to the vision model. Not deterministic, lower confidence."""

import base64
import io
import json
import re
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFont

from stoa import verda
from stoa.answer import Answer, Evidence, ToolError, parse_llm_answer
from stoa.inspect_file import in_cubicasa

MAX_SIDE = 1600
INVENTORY_METHOD = "vision object inventory; count computed from list length"

PROMPT = """You are reading an architectural drawing. Answer the question about it.
Count carefully, symbol by symbol. Reply with JSON only:
{{"answer": <number or short text>, "unit": <"count"|"m2"|"m3"|"m"|null>, "explanation": "<one sentence: what you counted and where>"}}

Question: {question}"""

CUBICASA_COUNT_PROMPT = """Read this residential floor plan and count the objects requested.
First establish the scope from apartment boundaries, entrances, labels and layout.
Count the main fully depicted dwelling, excluding incidental fragments of neighboring
apartments at the image edges. Include all separately drawn floors of the same
dwelling. If the question explicitly specifies a different scope, follow it.
Do not discard an object merely because it touches the image edge: use its dwelling
membership. If several complete dwellings are the subject, include all of them.

Inspect the relevant rooms and walls systematically. List each distinct requested
object once, with its room/floor and position. Follow the question's counting unit:
a double sink is one fixture; a double-leaf door is one doorway; an open passage
without a door is not a door. Count window openings, not individual glass panes.
Do not infer a room's function solely from furniture; use labels and the layout.
Do not count objects of another category or invent objects that are not visible.
Before finishing, check the full plan for omissions and the list for duplicates
and objects outside the scope. Keep entries short; do not output bounding boxes.

Reply with JSON only, using this schema:
{{"scope": "brief description of included dwelling/floors and excluded fragments",
 "objects": ["room/floor and position of ONE object", "room/floor and position of another"],
 "unit": "count"}}
Use an empty objects array if none are present. The program computes the count.

Question: {question}"""


# Windows on CubiCasa plans: the model alone undercounts (it skips windows in long
# walls). With the CubiCasa5K detector's boxes drawn on the plan as numbered
# proposals, it confirms or rejects each and adds the ones without a box. On the
# 44 test+val window questions (drawings the detector wasn't trained on): exact
# 24/44 in two runs vs 21 and 20 for the inventory prompt, within one 29/44 vs
# 25 and 24. Doors stay on the inventory prompt, which did better for them.
# The cache now holds Gemini full-plan detections (stoa.cubicasa_gemini) in the
# same schema; the CNN is out of the main flow. Uploaded plans are added on upload.
DETECTOR_CACHE = Path("out/cubicasa_cache")
WINDOW_SIDE = 2048  # small scans are upscaled so thin window marks survive
WINDOW_WHAT = ("Count the windows. A window is drawn as a thin opening in a wall, usually with one "
               "or more parallel lines across the wall thickness (glazing). Each separately drawn "
               "window counts once, even several side by side in one room; a window in a bay "
               "counts per drawn window. Balcony or terrace doors are doors, not windows.")
WINDOW_SCOPE = ("Count the main dwelling only; skip fragments of neighbouring apartments cut off at "
                "the image edges. Include all separately drawn floors of the same dwelling.")

WINDOW_VERIFY_PROMPT = """This is a residential floor plan. {what}

A detector proposed candidates, drawn as numbered red boxes. It misses some and
sometimes boxes the wrong thing or a neighbouring apartment's window. {scope}
1. For each numbered box, decide whether it is a window of the dwelling. Two boxes
   on the same window count once: accept only one.
2. Then trace every wall of each floor of the dwelling and add each window that has
   no box, with the point at its centre.

Reply with JSON only:
{{"scope": "which dwelling/floors you counted",
  "boxes": [{{"id": 1, "accept": true}}, ...],
  "added": [{{"where": "floor, room, wall", "point": [y, x]}}, ...]}}
Points are [y, x] normalised to 0-1000 over the whole image.

Question: {question}"""

WINDOW_POINTS_PROMPT = """This is a residential floor plan. {what}

Work floor by floor. For each floor of the dwelling, trace its exterior walls all the
way around, then check interior walls, and record every window you pass, one entry
each, with the point at its centre. {scope}

Reply with JSON only:
{{"scope": "which dwelling/floors you counted",
  "added": [{{"where": "floor, room, wall", "point": [y, x]}}, ...]}}
Points are [y, x] normalised to 0-1000 over the whole image.

Question: {question}"""


class VisionResponseError(ToolError):
    """An unusable vision response, retaining the billed usage."""

    def __init__(self, message: str, usage: dict):
        super().__init__(message)
        self.usage = usage


def _cubicasa_count(path: str, question: str, floor_plan: bool | None = None) -> bool:
    return ((in_cubicasa(path) if floor_plan is None else floor_plan)
            and bool(re.match(r"\s*(how many\b|kuinka monta\b)", question, re.I)))


def _window_question(question: str) -> bool:
    return bool(re.search(r"\bwindows?\b|ikkun", question, re.I))


def detector_windows(path: str | Path) -> list[list[float]] | None:
    """Window boxes from the cached full-plan detection, in image pixels, in
    the order stoa.ui.facts numbers them (window-0, window-1, ...)."""
    cached = DETECTOR_CACHE / f"{Path(path).stem}.json"
    if not cached.exists():
        return None
    icons = json.loads(cached.read_text(encoding="utf-8-sig"))["icons"]
    return [x["bbox"] for x in icons if x["class"] == "Window"]


def _window_image(path: Path, boxes: list | None) -> tuple[bytes, tuple[int, int]]:
    im = Image.open(path).convert("RGB")
    size = im.size
    s = WINDOW_SIDE / max(size)
    im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    if boxes:
        d = ImageDraw.Draw(im)
        font = ImageFont.load_default(size=max(18, im.width // 70))
        for i, b in enumerate(boxes, 1):
            bb = [b[0] * s - 4, b[1] * s - 4, b[2] * s + 4, b[3] * s + 4]
            d.rectangle(bb, outline=(230, 0, 0), width=3)
            d.text((bb[2] + 3, bb[1] - 4), str(i), fill=(230, 0, 0), font=font)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue(), size


def _window_verdict(text: str, n_boxes: int) -> tuple[str, list[int], list[tuple[str, float, float]], list[int]]:
    """Accepted box ids, added (where, y, x) points and ids of boxes that don't exist.
    A malformed reply raises; an accepted id with no box is dropped and reported."""
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)[:-3].strip()
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.get("scope"), str) or not data["scope"].strip():
        raise ValueError("Missing counting scope")
    boxes, added = data.get("boxes", []), data.get("added")
    if not isinstance(boxes, list) or not isinstance(added, list):
        raise ValueError("Expected boxes and added lists")
    accepted, bogus = [], []
    for b in boxes:
        if not isinstance(b, dict) or not isinstance(b.get("id"), int) or not isinstance(b.get("accept"), bool):
            raise ValueError("Each box needs an integer id and a boolean accept")
        if not 1 <= b["id"] <= n_boxes:
            bogus.append(b["id"])
            continue
        if b["accept"] and b["id"] not in accepted:
            accepted.append(b["id"])
    points = []
    for a in added:
        pt = a.get("point") if isinstance(a, dict) else None
        if (not isinstance(a.get("where"), str) or not isinstance(pt, list) or len(pt) != 2
                or not all(isinstance(v, (int, float)) and 0 <= v <= 1000 for v in pt)):
            raise ValueError("Each added window needs a description and a [y, x] point in 0-1000")
        points.append((a["where"].strip(), float(pt[0]), float(pt[1])))
    return data["scope"].strip(), accepted, points, bogus


def _count_windows(path: str, question: str, context: dict | None, endpoint: str, effort: str | None) -> Answer:
    boxes = detector_windows(path)
    png, (w, h) = _window_image(Path(path), boxes)
    template = WINDOW_VERIFY_PROMPT if boxes is not None else WINDOW_POINTS_PROMPT
    prompt = template.format(what=WINDOW_WHAT, scope=WINDOW_SCOPE, question=question)
    thinking = {"reasoning_effort": effort} if effort else {}
    resp = verda.chat(
        "cubicasa window count", [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}},
        ]}],
        context=context, endpoint=endpoint,
        max_tokens=verda.ENDPOINTS[endpoint].DEFAULTS.get("max_tokens", 4000), temperature=0, **thinking,
    )
    usage = verda.usage_of(resp, endpoint)
    if getattr(resp.choices[0], "finish_reason", None) == "length":
        raise VisionResponseError("CubiCasa window count was truncated", usage)
    try:
        scope, accepted, points, bogus = _window_verdict(verda.text_of(resp), len(boxes or []))
    except (ValueError, TypeError, AttributeError) as exc:
        raise VisionResponseError(f"Invalid CubiCasa window count: {exc}", usage) from exc
    # Refs match stoa.ui.facts (window-<i>) so confirmed boxes highlight as they are.
    evidence = [Evidence(ref=f"window-{i - 1}", label="window: detector box, confirmed", value=1,
                         bbox=tuple(boxes[i - 1])) for i in accepted]
    r = 0.012 * max(w, h)
    evidence += [Evidence(ref=f"window+{j}", label=f"window: {where} (placed by the vision model)", value=1,
                          bbox=(x / 1000 * w - r, y / 1000 * h - r, x / 1000 * w + r, y / 1000 * h + r))
                 for j, (where, y, x) in enumerate(points, 1)]
    if boxes is not None:
        how = (f"{len(accepted)} of {len(boxes)} detector boxes confirmed, {len(points)} windows added "
               "by the vision model")
        method = "full-plan detection boxes checked by a vision model; count = confirmed + added"
    else:
        how = f"{len(points)} windows located by the vision model"
        method = "vision model window list with positions; count computed from list length"
    if bogus:
        how += f"; ignored verdicts on boxes that don't exist: {bogus}"
    return Answer(value=len(evidence), unit="count", deterministic=False, method=method, evidence=evidence,
                  note=f"Counting scope: {scope}. {how}; verify on the drawing.", usage=usage)


def _count_inventory(text: str) -> tuple[str, list[str]]:
    """Never scrape a number out of malformed or incomplete object lists."""
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)[:-3].strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Expected a count inventory")
    # The caller has already established this is a count question. A label such
    # as "floor" instead of "count" does not change the number of listed objects.
    scope, objects = data.get("scope"), data.get("objects")
    if not isinstance(scope, str) or not scope.strip():
        raise ValueError("Missing counting scope")
    if not isinstance(objects, list) or any(not isinstance(x, str) or not x.strip() for x in objects):
        raise ValueError("Expected one nonempty location string per object")
    # Reject exact duplicate descriptions instead of silently deduplicating two
    # potentially different objects in the same room.
    if len({x.strip().casefold() for x in objects}) != len(objects):
        raise ValueError("Duplicate object descriptions")
    return scope, objects


def _png_bytes(path: Path, page: int = 0) -> bytes:
    if path.suffix.lower() == ".pdf":
        with pymupdf.open(path) as doc:
            pg = doc[page]
            zoom = MAX_SIDE / max(pg.rect.width, pg.rect.height)
            return pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png")
    pix = pymupdf.Pixmap(str(path))
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)
    scale = MAX_SIDE / max(pix.width, pix.height)
    if scale < 1:
        doc = pymupdf.open()
        pg = doc.new_page(width=pix.width * scale, height=pix.height * scale)
        pg.insert_image(pg.rect, pixmap=pix)
        return pg.get_pixmap().tobytes("png")
    return pix.tobytes("png")


def ask_vision_model(path: str, question: str, page: int = 0, context: dict | None = None,
                     endpoint: str = "kimi", effort: str | None = None, floor_plan: bool | None = None) -> Answer:
    """An explicit `effort` overrides the endpoint's default reasoning effort.
    Kimi defaults to low effort with thinking enabled. `floor_plan` marks a
    CubiCasa-style raster plan; None reads it from the path.
    """
    inventory = _cubicasa_count(path, question, floor_plan)
    if inventory and _window_question(question):
        return _count_windows(path, question, context, endpoint, effort)
    b64 = base64.b64encode(_png_bytes(Path(path), page)).decode()
    prompt = CUBICASA_COUNT_PROMPT if inventory else PROMPT
    content = [
        {"type": "text", "text": prompt.format(question=question)},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
    ]
    thinking = {"reasoning_effort": effort} if effort else {}
    if effort and endpoint == "kimi":
        thinking["extra_body"] = {"thinking": {"type": "enabled"}}
    resp = verda.chat(
        "cubicasa count inventory" if inventory else "vision fallback",
        [{
            "role": "user",
            "content": content,
        }],
        context=context,
        endpoint=endpoint,
        # Inherit the endpoint's budget, including reasoning and final output.
        max_tokens=verda.ENDPOINTS[endpoint].DEFAULTS.get("max_tokens", 4000 if effort else 400),
        **thinking,
        temperature=0,
    )
    usage = verda.usage_of(resp, endpoint)
    if inventory:
        if getattr(resp.choices[0], "finish_reason", None) == "length":
            raise VisionResponseError("CubiCasa count inventory was truncated", usage)
        try:
            scope, objects = _count_inventory(verda.text_of(resp))
        except (ValueError, TypeError) as exc:
            raise VisionResponseError(f"Invalid CubiCasa count inventory: {exc}", usage) from exc
        return Answer(
            value=len(objects), unit="count", deterministic=False,
            method=INVENTORY_METHOD,
            evidence=[Evidence(ref=f"page {page}: object {i}", label=location, value=1, page=page)
                      for i, location in enumerate(objects, 1)],
            note=f"Counting scope: {scope}. Objects identified by the vision model; verify on the drawing.",
            usage=usage,
        )
    data = parse_llm_answer(verda.text_of(resp))
    return Answer(
        value=data["answer"],
        unit=data.get("unit"),
        method="vision model reading the rendered drawing",
        deterministic=False,
        evidence=[Evidence(ref=f"page {page}", label=str(data.get("explanation", ""))[:300], page=page)],
        note="Model estimate from the image; verify on the drawing.",
        usage=usage,
    )
