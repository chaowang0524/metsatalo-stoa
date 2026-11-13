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
