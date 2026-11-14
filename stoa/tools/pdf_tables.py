"""Schedules (door, window, finish, room tables) read from the vector grid of a PDF.

Drawing sets draw schedules as ruled tables: one short line segment per cell edge.
We merge those segments into grid lines, group touching lines into tables, cut each
table into row bands and cells, and drop the text-layer words into the cells. No
OCR and no LLM, so counts and sums over a schedule are exact and every row has a
box on the sheet. pymupdf's `find_tables` takes 40 s+ per sheet on these drawings
and finds nothing, hence the custom parser.

Coordinates are page display coordinates (rotation applied), i.e. the sheet as
rendered by `page.get_pixmap()` at 72 dpi.
"""

import functools
import re
from dataclasses import dataclass, field

import numpy as np
import pymupdf

from stoa.answer import Answer, Evidence, ToolError

TOL = 1.0  # pt; segments closer than this are the same grid line
SQFT_PER_M2 = 10.763910417


@dataclass
class Row:
    cells: list[str]
    bbox: tuple[float, float, float, float]


@dataclass
class Table:
    id: str  # "T1", "T2", ... in reading order
    title: str
    columns: list[str]
    rows: list[Row]
    bbox: tuple[float, float, float, float]
    page: int = 0
    xs: list[float] = field(default_factory=list, repr=False)

    def col(self, name: str) -> int:
        key = _norm(name)
        for i, c in enumerate(self.columns):
            if _norm(c) == key:
                return i
        hits = [i for i, c in enumerate(self.columns) if key in _norm(c)]
        if len(hits) == 1:
            return hits[0]
        raise ToolError(f"No column {name!r} in {self.id} {self.title!r}. Columns: {self.columns}")


def _norm(s: str) -> str:
    s = s.replace("’", "'").replace("′", "'").replace("″", '"').replace("”", '"')
    s = re.sub(r"\s*-\s*", "-", s)  # 3' - 0" == 3'-0"
    return re.sub(r"\s+", " ", s).strip().casefold()


def _merge(segs: list[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
    """(pos, a, b) segments → merged lines: same pos (±TOL) and overlapping/adjacent spans."""
    out: list[list[float]] = []
    for pos, a, b in sorted(segs):
        for line in reversed(out[-50:]):  # recent lines at about the same pos
            if abs(line[0] - pos) <= TOL and a <= line[2] + TOL and b >= line[1] - TOL:
                line[1], line[2] = min(line[1], a), max(line[2], b)
                break
        else:
            out.append([pos, a, b])
    # one more pass: chains that only joined after both ends grew
    out.sort()
    merged: list[list[float]] = []
    for l in out:
        if merged and abs(merged[-1][0] - l[0]) <= TOL and l[1] <= merged[-1][2] + TOL:
            merged[-1][2] = max(merged[-1][2], l[2])
        else:
            merged.append(l)
    return [tuple(l) for l in merged]


def _grid_lines(page: pymupdf.Page) -> tuple[list, list]:
    m = page.rotation_matrix
    hs, vs = [], []

    def add(p, q):
        p, q = p * m, q * m
        if abs(p.y - q.y) <= 0.2 and abs(p.x - q.x) > 0.5:
            hs.append(((p.y + q.y) / 2, min(p.x, q.x), max(p.x, q.x)))
        elif abs(p.x - q.x) <= 0.2 and abs(p.y - q.y) > 0.5:
            vs.append(((p.x + q.x) / 2, min(p.y, q.y), max(p.y, q.y)))

    for d in page.get_drawings():
        for it in d["items"]:
            if it[0] == "l":
                add(it[1], it[2])
            elif it[0] == "re":
                r = it[1]
                add(r.tl, r.tr), add(r.bl, r.br), add(r.tl, r.bl), add(r.tr, r.br)
    return _merge(hs), _merge(vs)


def _components(H: list, V: list) -> list[tuple[list, list]]:
    """Groups of horizontal and vertical lines that touch each other."""
    if not H or not V:
        return []
    h = np.array(H)
    v = np.array(V)
    # hit[i, j]: horizontal i crosses or touches vertical j
    hit = ((v[None, :, 0] >= h[:, None, 1] - TOL) & (v[None, :, 0] <= h[:, None, 2] + TOL)
           & (h[:, None, 0] >= v[None, :, 1] - TOL) & (h[:, None, 0] <= v[None, :, 2] + TOL))
    parent = list(range(len(H) + len(V)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, j in zip(*np.nonzero(hit)):
        a, b = find(int(i)), find(len(H) + int(j))
        if a != b:
            parent[a] = b
    groups: dict[int, tuple[list, list]] = {}
    for i in range(len(H)):
        groups.setdefault(find(i), ([], []))[0].append(H[i])
    for j in range(len(V)):
        groups.setdefault(find(len(H) + j), ([], []))[1].append(V[j])
    comps = [g for g in groups.values() if len(g[0]) >= 2 and len(g[1]) >= 2]
    return [g for g in _stack(comps) if len(g[0]) >= 3]


def _stack(comps: list[tuple[list, list]]) -> list[tuple[list, list]]:
    """Join grids drawn one above the other with a small gap and the same width,
    e.g. a schedule's header block above its body, or keynote groups."""
    def ext(g):
        return (min(h[1] for h in g[0]), min(v[1] for v in g[1]),
                max(h[2] for h in g[0]), max(v[2] for v in g[1]))

    comps = sorted(comps, key=lambda g: ext(g)[1])
    out: list[tuple[list, list]] = []
    for g in comps:
        a = ext(g)
        for o in out:
            b = ext(o)
            if abs(a[0] - b[0]) < 3 and abs(a[2] - b[2]) < 3 and -TOL <= a[1] - b[3] < 30:
                o[0].extend(g[0]), o[1].extend(g[1])
                break
        else:
            out.append((list(g[0]), list(g[1])))
    return out


def _cluster(vals, tol=TOL * 1.5) -> list[float]:
    out: list[float] = []
    for x in sorted(vals):
        if not out or x - out[-1] > tol:
            out.append(x)
    return out


def _join(words: list) -> str:
    """Words of one cell, line by line."""
    words = sorted(words, key=lambda w: (round(w[1] / 2), w[0]))
    lines: list[list] = []
    for w in words:
        if lines and abs(lines[-1][-1][1] - w[1]) < 2.5:
            lines[-1].append(w)
        else:
            lines.append([w])
    return " ".join(" ".join(x[4] for x in sorted(l, key=lambda w: w[0])) for l in lines)


def _cells(xs: list[float], words: list) -> list[str]:
    cells = [[] for _ in xs[:-1]]
    for w in words:
        k = int(np.searchsorted(xs, w[5])) - 1
        if 0 <= k < len(cells):
            cells[k].append(w)
    return [_join(c) for c in cells]


def _text_lines(words: list) -> list[list]:
    lines: list[list] = []
    for w in sorted(words, key=lambda w: w[6]):
        if lines and abs(lines[-1][-1][6] - w[6]) < 2.5:
            lines[-1].append(w)
        else:
            lines.append([w])
    return lines


def _split(band, xs: list[float], anchor: int) -> list[tuple[float, float, list]]:
    """A band can hold several rows when the drawing omits the rule between them.
    A text line that has a value in the anchor column (the column filled in most
    rows, e.g. the door number) and in at least one other column starts a row;
    other lines (wrapped cell text) join the nearest row."""
    top, bot, ws = band[0], band[1], band[3]
    lines = _text_lines(ws)
    if len(lines) < 2:
        return [(top, bot, ws)]
    a0, a1 = xs[anchor], xs[anchor + 1]

    def cols(line):
        return {int(np.searchsorted(xs, w[5])) for w in line}

    starts = [l for l in lines if any(a0 <= w[5] <= a1 for w in l) and len(cols(l)) >= 2]
    if len(starts) < 2:
        return [(top, bot, ws)]
    ys = [sum(w[6] for w in l) / len(l) for l in starts]
    groups = [list(l) for l in starts]
    for l in lines:
        if any(l is s for s in starts):
            continue
        y = sum(w[6] for w in l) / len(l)
        groups[int(np.argmin([abs(y - s) for s in ys]))].extend(l)
    cuts = [top] + [(max(w[3] for w in g) + min(w[1] for w in h)) / 2 for g, h in zip(groups, groups[1:])] + [bot]
    return [(cuts[i], cuts[i + 1], g) for i, g in enumerate(groups)]


def _xs_at(V: list, y: float) -> list[float]:
    """Column edges at height y: the vertical rules that cross it."""
    return _cluster(v[0] for v in V if v[1] - TOL <= y <= v[2] + TOL)


def _missing_rules(xs: list[float], header: list, body: list) -> list[float]:
    """Column edges the drawing forgot. When one header cell holds two labels far
    apart (e.g. 'Type ... Width') and some x between them is clear of body text
    in every row, while text sits on both sides, that x is a column edge."""
    extra = []
    for a, b in zip(xs, xs[1:]):
        hw = sorted((w for w in header if a <= w[5] < b), key=lambda w: w[0])
        if len(hw) < 2:
            continue
        gaps = [(hw[k + 1][0] - hw[k][2], k) for k in range(len(hw) - 1)]
        gap, k = max(gaps)
        if gap < 15:
            continue
        lo, hi = hw[k][2], hw[k + 1][0]
        bw = [w for w in body if a <= w[5] < b]
        x = hi - 0.5
        while x > lo and any(w[0] - 0.5 < x < w[2] + 0.5 for w in bw):
            x -= 0.5
        if x <= lo:
            continue
        left = {round(w[6]) for w in bw if w[2] < x}
        right = {round(w[6]) for w in bw if w[0] > x}
        if left and len(right) >= 0.5 * len(left):
            extra.append(x)
    return extra


def _table(H: list, V: list, words: list, page: int) -> Table | None:
    x0, x1 = min(h[1] for h in H), max(h[2] for h in H)
    y0, y1 = min(v[1] for v in V), max(v[2] for v in V)
    inside = [w for w in words if x0 <= w[5] <= x1 and y0 <= w[6] <= y1]
    if len(inside) < 6:
        return None  # a drawing detail, not a schedule
    ys = _cluster([h[0] for h in H] + [y0, y1])  # the outer border isn't always a line item
    bands = []  # (top, bottom, column edges, words)
    for top, bot in zip(ys, ys[1:]):
        if bot - top < 3:
            continue
        xs = _xs_at(V, (top + bot) / 2)
        if len(xs) < 2:
            continue
        bands.append((top, bot, xs, [w for w in inside if top <= w[6] < bot]))
    if len(bands) < 2:
        return None
    sigs = [tuple(round(x) for x in b[2]) for b in bands if len(b[2]) > 2]
    if not sigs:
        return None
    body = max(set(sigs), key=sigs.count)
    first = next(i for i, b in enumerate(bands) if tuple(round(x) for x in b[2]) == body)
    xs = bands[first][2]
    extra = _missing_rules(xs, bands[first][3], [w for b in bands[first + 1:] for w in b[3]])
    if extra:
        xs = sorted(xs + extra)
        bands = [(b[0], b[1], sorted(b[2] + extra), b[3]) if i >= first else b for i, b in enumerate(bands)]
    centers = [(a + b) / 2 for a, b in zip(xs, xs[1:])]

    def spread(bx: list[float], texts: list[str]) -> list[str]:
        """Cell texts of a row with edges bx mapped onto the body columns."""
        out = [""] * len(centers)
        for k, text in enumerate(texts):
            if not text:
                continue
            for j, c in enumerate(centers):
                if bx[k] - TOL <= c <= bx[k + 1] + TOL:
                    out[j] = text
                    break
            else:  # cell narrower than any body column: nearest column
                mid = (bx[k] + bx[k + 1]) / 2
                out[int(np.argmin([abs(c - mid) for c in centers]))] = text
        return out

    # Header: every text line above the first data row. A merged cell (e.g. "Door"
    # over Width/Height/Type) names every column under it. A first line that is
    # one centred phrase is the table title.
    title = ""
    header_cols: list[list[str]] = [[] for _ in centers]
    lines = [(i, l) for i, b in enumerate(bands[: first + 1]) for l in _text_lines(b[3])]
    for n, (i, line) in enumerate(lines):
        y = sum(w[6] for w in line) / len(line)
        bx = bands[i][2] if i == first else _xs_at(V, y)
        cells = _cells(bx, line)
        used = [k for k, t in enumerate(cells) if t]
        mid = (min(w[0] for w in line) + max(w[2] for w in line)) / 2
        centred = abs(mid - (x0 + x1) / 2) < 0.1 * (x1 - x0)
        if n == 0 and len(lines) > 1 and (len(bx) == 2 or (len(used) <= 2 and centred)):
            title = _join(line)
            continue
        for k in used:
            for j, c in enumerate(centers):
                if bx[k] - TOL <= c <= bx[k + 1] + TOL and cells[k] not in header_cols[j]:
                    header_cols[j].append(cells[k])
    columns = [" ".join(h) or f"col{j + 1}" for j, h in enumerate(header_cols)]
    seen: dict[str, int] = {}
    for j, c in enumerate(columns):
        if c in seen:
            seen[c] += 1
            columns[j] = f"{c} ({seen[c]})"
        else:
            seen[c] = 1
    data = bands[first + 1:]
    filled = [spread(b[2], _cells(b[2], b[3])) for b in data]
    anchor = int(np.argmax([sum(bool(f[j]) for f in filled) for j in range(len(centers))])) if data else 0
    rows = []
    for b in data:
        for top, bot, ws in _split(b, xs, anchor):
            rows.append(Row(spread(b[2], _cells(b[2], ws)),
                            (round(b[2][0], 1), round(top, 1), round(b[2][-1], 1), round(bot, 1))))
    rows = [r for r in rows if any(r.cells)]
    if not rows:
        return None
    if not title:  # a caption just above the grid
        above = [w for w in words if x0 <= w[5] <= x1 and y0 - 30 <= w[6] < y0]
        title = _join(above)
    return Table("", title, columns, rows, (x0, y0, x1, y1), page, xs)


@functools.lru_cache(maxsize=16)
def tables(path: str) -> tuple[Table, ...]:
    out = []
    with pymupdf.open(path) as doc:
        for pno, page in enumerate(doc):
            m = page.rotation_matrix
            words = []
            for w in page.get_text("words"):
                r = pymupdf.Rect(w[:4]) * m
                words.append((r.x0, r.y0, r.x1, r.y1, w[4], (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2))
            H, V = _grid_lines(page)
            for gh, gv in _components(H, V):
                t = _table(gh, gv, words, pno)
                # a schedule has a header naming every column (detail grids don't)
                if t and len(t.rows) >= 2 and not any(re.fullmatch(r"col\d+", c) for c in t.columns):
                    out.append(t)
    out.sort(key=lambda t: (t.page, round(t.bbox[1] / 50), t.bbox[0]))
    for i, t in enumerate(out, 1):
        t.id = f"T{i}"
    return tuple(out)


def describe_tables(path: str, max_values: int = 12) -> str:
    """Schema of every schedule on the sheet, for the router prompt."""
    lines = []
    for t in tables(path):
        lines.append(f'{t.id} "{t.title}" ({len(t.rows)} rows). Columns:')
        for j, c in enumerate(t.columns):
            vals = list(dict.fromkeys(r.cells[j] for r in t.rows if r.cells[j]))
            shown = ", ".join(repr(v[:60]) for v in vals[:max_values])
            more = f", … ({len(vals)} distinct)" if len(vals) > max_values else ""
            lines.append(f"  - {c!r}: {shown}{more}" if vals else f"  - {c!r}: (empty)")
    return "\n".join(lines)


def _pick(path: str, table: str) -> Table:
    ts = tables(path)
    if not ts:
        raise ToolError("No ruled tables found on this sheet; use pdf_page_text.")
    key = _norm(str(table))
    for t in ts:
        if _norm(t.id) == key or _norm(t.title) == key:
            return t
    hits = [t for t in ts if key and key in _norm(t.title)]
    if len(hits) == 1:
        return hits[0]
    raise ToolError(f"Table {table!r} not found. Tables: " + "; ".join(f"{t.id} {t.title!r}" for t in ts))


_SEP = re.compile(r"\s*(?:,|;|/|&|\+|\band\b)\s*|\s+", re.I)


def _match(cell: str, how: str, values: list[str]) -> bool:
    c = _norm(cell)
    if how == "nonempty":
        return bool(c) and c not in {"-", "--", "n/a"}
    if how == "empty":
        return not c or c in {"-", "--"}
    vals = [_norm(str(v)) for v in values]
    if how == "equals":
        return c in vals
    if how == "contains":
        return any(v in c for v in vals)
    if how == "has":  # one of the listed codes, e.g. note 'A' in 'A, B'
        tokens = set(t for t in _SEP.split(c) if t)
        return any(v in tokens or v == c for v in vals)
    if how == "startswith":
        return any(c.startswith(v) for v in vals)
    if how == "regex":
        return any(re.search(v, cell, re.I) for v in values)
    raise ToolError(f"Unknown match {how!r}")


def _filter(t: Table, where: list[dict] | None) -> list[int]:
    idx = list(range(len(t.rows)))
    for f in where or []:
        j = t.col(f.get("column", ""))
        how = f.get("match", "equals")
        vals = f.get("values")
        if vals is None and "value" in f:
            vals = f["value"]
        if isinstance(vals, (str, int, float)):
            vals = [str(vals)]
        if how not in ("nonempty", "empty") and not vals:
            raise ToolError(f"Filter on {f.get('column')!r} needs values.")
        idx = [i for i in idx if _match(t.rows[i].cells[j], how, vals or [])]
    return idx


_AREA = re.compile(r"(-?\d[\d,]*(?:\.\d+)?)\s*(sf|sq\.?\s*ft\.?|ft2|ft²|m2|m²|sqm)?", re.I)


def _number(cell: str) -> tuple[float, str | None] | None:
    m = _AREA.search(cell.replace(" ", " "))
    if not m:
        return None
    unit = (m[2] or "").lower().replace(" ", "").replace(".", "")
    unit = {"sf": "ft2", "sqft": "ft2", "ft2": "ft2", "ft²": "ft2", "m2": "m2", "m²": "m2", "sqm": "m2"}.get(unit)
    return float(m[1].replace(",", "")), unit


def _evidence(t: Table, i: int, value=None) -> Evidence:
    r = t.rows[i]
    label = " | ".join(c for c in r.cells if c)
    return Evidence(ref=f"{t.id}:r{i + 1}", label=label[:200], value=value, bbox=r.bbox, page=t.page)


def pdf_table_query(path: str, table: str, op: str = "count", column: str | None = None,
                    where: list[dict] | None = None, unit: str | None = None) -> Answer:
    """Count rows, or sum/max/min a numeric column, over schedule rows that pass the filters."""
    t = _pick(path, table)
    idx = _filter(t, where)
    desc = " AND ".join(
        f"{f.get('column')} {f.get('match', 'equals')} {f.get('values', f.get('value', ''))}" for f in where or []
    ) or "all rows"
    if op == "count":
        if not idx:
            raise ToolError(f"No rows of {t.id} match {desc}. Check the values against the column list.")
        return Answer(
            value=len(idx), unit="count",
            method=f"Rows of {t.id} '{t.title}' where {desc}",
            evidence=[_evidence(t, i) for i in idx],
        )
    if op not in ("sum", "max", "min"):
        raise ToolError(f"Unknown op {op!r}; use count, sum, max or min.")
    if not column:
        raise ToolError(f"op={op} needs a column.")
    j = t.col(column)
    nums, skipped = [], 0
    for i in idx:
        n = _number(t.rows[i].cells[j])
        if n is None:
            skipped += 1
        else:
            nums.append((i, *n))
    if not nums:
        raise ToolError(f"No numeric values in column {t.columns[j]!r} for {desc}.")
    src_unit = next((u for _, _, u in nums if u), None)
    factor = 1.0
    out_unit = src_unit or ("count" if re.search(r"count|qty|quantity|no\.", t.columns[j], re.I) else None)
    if unit and src_unit and unit != src_unit:
        if {unit, src_unit} != {"ft2", "m2"}:
            raise ToolError(f"Can't convert {src_unit} to {unit}.")
        factor = 1 / SQFT_PER_M2 if unit == "m2" else SQFT_PER_M2
        out_unit = unit
    vals = [v * factor for _, v, _ in nums]
    if op == "sum":
        value, used = sum(vals), nums
    else:
        k = int(np.argmax(vals) if op == "max" else np.argmin(vals))
        value, used = vals[k], [nums[k]]
    value = round(value, 2)
    if float(value).is_integer():
        value = int(value)
    note = f"{skipped} matching rows had no number in {t.columns[j]!r} and were skipped." if skipped else ""
    if factor != 1.0:
        note = (note + f" Converted {src_unit} → {unit} (1 m² = {SQFT_PER_M2} ft²).").strip()
    return Answer(
        value=value, unit=out_unit,
        method=f"{op} of '{t.columns[j]}' in {t.id} '{t.title}' where {desc}",
        evidence=[_evidence(t, i, v) for i, v, _ in used],
        note=note,
    )


def pdf_table_rows(path: str, table: str | None = None, where: list[dict] | None = None) -> Answer:
    """Rows of a schedule (filtered, or every table if none is named) as text, each
    cell labelled with its column, for lookups and questions the LLM must phrase."""
    ts = [_pick(path, table)] if table else list(tables(path))
    if not ts:
        raise ToolError("No ruled tables found on this sheet; use pdf_page_text.")
    parts, evidence = [], []
    for t in ts:
        idx = _filter(t, where) if table else range(len(t.rows))
        parts.append(f"{t.id} {t.title} ({len(idx)} of {len(t.rows)} rows)")
        for i in idx:
            cells = "; ".join(f"{c}={v}" for c, v in zip(t.columns, t.rows[i].cells) if v)
            parts.append(f"r{i + 1}: {cells}")
            evidence.append(_evidence(t, i))
    if not evidence:
        raise ToolError("No rows match; check the filter values against the column list.")
    return Answer(value="\n".join(parts), method="Ruled tables parsed from the PDF vector grid",
                  evidence=evidence)
