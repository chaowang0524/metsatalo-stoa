"""Text-layer lookups in vector PDFs (schedules, legends, title blocks)."""

import functools
import re

import pymupdf

from stoa.answer import Answer, Evidence, ToolError


@functools.lru_cache(maxsize=16)
def _lines(path: str) -> list[tuple[int, str, tuple]]:
    """Every text line as (page, text, bbox), in reading order."""
    out = []
    with pymupdf.open(path) as doc:
        for pno, page in enumerate(doc):
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    text = " ".join(s["text"] for s in line["spans"]).strip()
                    if text:
                        out.append((pno, text, tuple(round(v, 1) for v in line["bbox"])))
    return out


def pdf_search_text(path: str, pattern: str) -> Answer:
    """Lines matching a case-insensitive regex or plain substring, with locations."""
    try:
        rx = re.compile(pattern, re.I)
    except re.error:
        rx = re.compile(re.escape(pattern), re.I)
    hits = [(p, t, b) for p, t, b in _lines(path) if rx.search(t)]
    return Answer(
        value=len(hits),
        unit="count",
        method=f"PDF text lines matching /{pattern}/",
        evidence=[Evidence(ref=f"p{p}:{b}", label=t, bbox=b, page=p) for p, t, b in hits],
    )


def pdf_page_text(path: str, page: int = 0, max_chars: int = 12000) -> Answer:
    """Text of one page, grouped into rows by y position so tables stay readable."""
    lines = [(t, b) for p, t, b in _lines(path) if p == page]
    if not lines:
        raise ToolError(f"No text on page {page}.")
    rows: list[tuple[float, list]] = []
    for t, b in sorted(lines, key=lambda x: (round(x[1][1] / 3), x[1][0])):
        if rows and abs(rows[-1][0] - b[1]) < 3:
            rows[-1][1].append(t)
        else:
            rows.append((b[1], [t]))
    text = "\n".join(" | ".join(r[1]) for r in rows)
    return Answer(
        value=text[:max_chars],
        method=f"PDF text layer, page {page}",
        note="" if len(text) <= max_chars else f"truncated from {len(text)} chars",
    )


AREA = re.compile(r"^\d+(?:[.,]\d+)?\s*(?:m²|m2|sf|sq\.?\s?ft)\.?$", re.I)
ROOM_NO = re.compile(r"^\d+(?:\.\d+)+[a-z]?$", re.I)
UNIT = re.compile(r"^(?:whg|wohnung|app|appartement|apt|apartment|unit)\.?\s*[\w-]{1,4}$", re.I)


def _area(text: str) -> float:
    v = float(re.match(r"\d+(?:[.,]\d+)?", text).group().replace(",", "."))
    return round(v * 0.09290304, 2) if re.search(r"ft|sf", text, re.I) else v


def pdf_rooms(path: str, page: int = 0) -> tuple[list[dict], list[dict]]:
    """Room stamps on a plan sheet: a name with a room number and/or an area
    directly below it (Swiss sheets: 'Zimmer' / '2.01' / '15.61 m²').
    Returns (rooms, apartments); bboxes in PDF points cover the whole stamp."""
    lines = [(t, b) for p, t, b in _lines(path) if p == page]

    def below(b):
        h = b[3] - b[1]
        hits = [(t, c) for t, c in lines if -0.2 * h <= c[1] - b[3] < 0.6 * h and c[0] < b[2] and c[2] > b[0]]
        return min(hits, key=lambda x: x[1][1]) if hits else (None, None)

    rooms = []
    for text, b in lines:
        if not re.search(r"[^\W\d_]", text) or AREA.match(text) or UNIT.match(text):
            continue
        stamp, number, area = [b], None, None
        t, c = below(b)
        if t and ROOM_NO.match(t):
            number, stamp = t, stamp + [c]
            t, c = below(c)
        if t and AREA.match(t):
            area, stamp = _area(t), stamp + [c]
        if number or area is not None:
            rooms.append({"label": text, "number": number, "area": area,
                          "bbox": [min(x[0] for x in stamp), min(x[1] for x in stamp),
                                   max(x[2] for x in stamp), max(x[3] for x in stamp)]})
    units = [{"label": t, "bbox": list(b)} for t, b in lines if UNIT.match(t)]
    return rooms, units
