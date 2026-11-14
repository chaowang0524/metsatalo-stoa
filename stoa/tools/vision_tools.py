"""Fallback: show the drawing to the vision model. Not deterministic, lower confidence."""

import base64
from pathlib import Path

import pymupdf

from stoa import verda
from stoa.answer import Answer, Evidence, parse_llm_answer

MAX_SIDE = 1600

PROMPT = """You are reading an architectural drawing. Answer the question about it.
Count carefully, symbol by symbol. Reply with JSON only:
{{"answer": <number or short text>, "unit": <"count"|"m2"|"m3"|"m"|null>, "explanation": "<one sentence: what you counted and where>"}}

Question: {question}"""


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
    """`effort` ("low", "medium", "high") turns kimi's reasoning back on at that
    reasoning_effort; it is off in kimi's DEFAULTS."""
    b64 = base64.b64encode(_png_bytes(Path(path), page)).decode()
    resp = verda.chat(
        "vision fallback",
        [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT.format(question=question)},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }],
        context=context,
        endpoint=endpoint,
        # Reasoning counts against max_tokens: "low" used ~250, default up to 12k.
        max_tokens=4000 if effort else 400,
        **({"reasoning_effort": effort, "extra_body": {"thinking": {"type": "enabled"}}} if effort else {}),
        temperature=0,
    )
    data = parse_llm_answer(verda.text_of(resp))
    return Answer(
        value=data["answer"],
        unit=data.get("unit"),
        method="vision model reading the rendered drawing",
        deterministic=False,
        evidence=[Evidence(ref=f"page {page}", label=str(data.get("explanation", ""))[:300], page=page)],
        note="Model estimate from the image; verify on the drawing.",
        usage=verda.usage_of(resp, endpoint),
    )
