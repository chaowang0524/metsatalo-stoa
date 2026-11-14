"""Fallback: show the drawing to the vision model. Not deterministic, lower confidence."""

import base64
import json
import re
from pathlib import Path

import pymupdf

from stoa import verda
from stoa.answer import Answer, Evidence, ToolError, parse_llm_answer

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


class VisionResponseError(ToolError):
    """An unusable vision response, retaining the billed usage."""

    def __init__(self, message: str, usage: dict):
        super().__init__(message)
        self.usage = usage


def _cubicasa_count(path: str, question: str) -> bool:
    return (any(part.casefold() == "cubicasa5k" for part in Path(path).parts)
            and bool(re.match(r"\s*(how many\b|kuinka monta\b)", question, re.I)))


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
                     endpoint: str = "kimi", effort: str | None = None) -> Answer:
    """An explicit `effort` overrides the endpoint's default reasoning effort.
    Kimi defaults to low effort with thinking enabled.
    """
    b64 = base64.b64encode(_png_bytes(Path(path), page)).decode()
    inventory = _cubicasa_count(path, question)
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
