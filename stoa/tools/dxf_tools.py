"""Deterministic answers from CAD floor plans (DXF), e.g. the swiss_dwellings sheets.

The DXF layers carry the semantics: ROOM_OUTLINE polygons with ROOM_TEXT labels
(name, number, "12.34 m²"), UNIT_OUTLINE apartments, DOOR, WINDOW and FIXTURE
symbols. Symbol rules below were checked against the swiss_dwellings ground truth.
"""

import functools
import re
import unicodedata
from dataclasses import dataclass, field

import ezdxf
from ezdxf import bbox
from shapely.geometry import Point, Polygon

from stoa.answer import Answer, Evidence, ToolError

AREA_RE = re.compile(r"^\s*([\d.,]+)\s*m(?:²|2)\s*$")
NUMBER_RE = re.compile(r"^\s*[A-Z]{0,3}\d+(?:\.\d+)+[a-z]?\s*$")
UNIT_RE = re.compile(r"^\s*(?:Whg\.?|Wohnung|App\.?|Appartement|Apt\.?)\s*(\S+)\s*$", re.I)

# Rooms that don't count toward net indoor floor area.
OUTDOOR = re.compile(
    r"balkon|balcon|balcony|loggia|terrass|terrace|sitzplatz|veranda|"
    r"dachgarten|garten|jardin|giardino|patio|aussen|outdoor",
    re.I,
)


@dataclass
class Room:
    handle: str
    polygon: Polygon
    number: str | None = None
    name: str | None = None
    area_label: float | None = None
    unit: str | None = None
    texts: list[str] = field(default_factory=list)

    @property
    def area(self) -> float:
        return self.area_label if self.area_label is not None else self.polygon.area

    @property
    def label(self) -> str:
        return " ".join(p for p in (self.number, self.name) if p) or self.handle

    @property
    def outdoor(self) -> bool:
        return bool(self.name and OUTDOOR.search(self.name))


@dataclass
class Plan:
    rooms: list[Room]
    units: dict[str, Polygon]  # apartment label -> outline
    unit_handles: dict[str, str]
    doors: list
    windows: list
    toilets: list
    washbasins: list
    bathtubs: list
    showers: list


def _norm(s: str | None) -> str:
    s = unicodedata.normalize("NFKC", s or "").casefold()
    s = re.sub(r"[\"'„“”«»‘’]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _bounds(e) -> tuple[float, float, float, float]:
    b = bbox.extents([e])
    return (b.extmin.x, b.extmin.y, b.extmax.x, b.extmax.y)


def _touch(a, b, tol=0.01) -> bool:
    return a[0] <= b[2] + tol and b[0] <= a[2] + tol and a[1] <= b[3] + tol and b[1] <= a[3] + tol


def _poly(e) -> Polygon | None:
    pts = [(p[0], p[1]) for p in e.get_points()]
    if len(pts) < 3:
        return None
    poly = Polygon(pts)
    return poly if poly.is_valid else poly.buffer(0)


def _text_point(e) -> Point:
    p = e.dxf.align_point if e.dxf.hasattr("align_point") and (e.dxf.halign or e.dxf.valign) else e.dxf.insert
    return Point(p[0], p[1])


def _cluster(entities, tol=0.02) -> list[list]:
    """Group entities whose bounding boxes touch (union-find)."""
    bs = [_bounds(e) for e in entities]
    parent = list(range(len(bs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(bs)):
        for j in range(i + 1, len(bs)):
            if _touch(bs[i], bs[j], tol):
                parent[find(i)] = find(j)
    groups: dict[int, list] = {}
    for i, e in enumerate(entities):
        groups.setdefault(find(i), []).append(e)
    return list(groups.values())


@functools.lru_cache(maxsize=32)
def load_plan(path: str) -> Plan:
    msp = ezdxf.readfile(path).modelspace()

    rooms = []
    for e in msp.query('LWPOLYLINE[layer=="ROOM_OUTLINE"]'):
        if (poly := _poly(e)) is not None and poly.area > 0:
            rooms.append(Room(handle=e.dxf.handle, polygon=poly))

    units: dict[str, Polygon] = {}
    unit_handles: dict[str, str] = {}
    unit_outlines = [
        (e.dxf.handle, p) for e in msp.query('LWPOLYLINE[layer=="UNIT_OUTLINE"]')
        if (p := _poly(e)) is not None
    ]

    for t in msp.query('TEXT[layer=="ROOM_TEXT"]'):
        text = t.dxf.text.strip()
        pt = _text_point(t)
        if m := UNIT_RE.match(text):
            for h, poly in unit_outlines:
                if poly.buffer(0.05).contains(pt):
                    units[m[1]] = poly
                    unit_handles[m[1]] = h
                    break
            continue
        # Smallest room polygon containing the label.
        hits = [r for r in rooms if r.polygon.buffer(0.02).contains(pt)]
        if not hits:
            continue
        room = min(hits, key=lambda r: r.polygon.area)
        room.texts.append(text)
        if m := AREA_RE.match(text):
            room.area_label = float(m[1].replace(",", "."))
        elif NUMBER_RE.match(text):
            room.number = text
        else:
            room.name = f"{room.name} {text}" if room.name else text

    for r in rooms:
        c = r.polygon.representative_point()
        for label, poly in units.items():
            if poly.buffer(0.05).contains(c):
                r.unit = label
                break

    # Doors: each door is a leaf line plus a 5-vertex swing polyline.
    doors = [e for e in msp.query('LWPOLYLINE[layer=="DOOR"]') if len(e) == 5]
    # Windows: each window is drawn as three touching rectangles.
    window_ents = list(msp.query('*[layer=="WINDOW"]'))
    windows = _chunks_by_cluster(window_ents)

    fixtures = list(msp.query('*[layer=="FIXTURE"]'))
    bowls = [e for e in fixtures if e.dxftype() == "LWPOLYLINE" and len(e) == 48]
    drains = [e for e in fixtures if e.dxftype() == "CIRCLE" and e.dxf.radius < 0.06]
    drain_bounds = [_bounds(d) for d in drains]
    toilets, washbasins = [], []
    for b in bowls:
        bb = _bounds(b)
        # A washbasin has a drain circle in its bowl; a toilet bowl doesn't.
        (washbasins if any(_touch(bb, d) for d in drain_bounds) else toilets).append(b)
    bathtubs = [e for e in fixtures if e.dxftype() == "LWPOLYLINE" and len(e) == 69]
    showers = [e for e in fixtures if e.dxftype() == "LWPOLYLINE" and len(e) == 5]

    return Plan(rooms, units, unit_handles, doors, windows, toilets, washbasins, bathtubs, showers)


def _chunks_by_cluster(window_ents) -> list[list]:
    """Windows come as 3 rectangles each; split touching clusters into triples."""
    out = []
    for g in _cluster(window_ents):
        n = max(1, round(len(g) / 3))
        g = sorted(g, key=lambda e: _bounds(e)[:2])
        size = len(g) / n
        out += [g[int(i * size): int((i + 1) * size)] for i in range(n)]
    return out


def _ev_entity(e, label: str) -> Evidence:
    return Evidence(ref=e.dxf.handle, label=label, bbox=_bounds(e))


def _ev_room(r: Room) -> Evidence:
    return Evidence(ref=r.handle, label=r.label, value=round(r.area, 2), bbox=r.polygon.bounds)


# ---- tools exposed to the LLM -------------------------------------------------

SYMBOLS = ["door", "window", "toilet", "washbasin", "bathtub", "shower", "apartment", "room"]


def dxf_count_symbols(path: str, kind: str) -> Answer:
    plan = load_plan(path)
    kind = kind.lower().rstrip("s")
    if kind == "door":
        ev = [_ev_entity(e, "door") for e in plan.doors]
    elif kind == "window":
        ev = [
            Evidence(ref=",".join(e.dxf.handle for e in g), label="window",
                     bbox=_union([_bounds(e) for e in g]))
            for g in plan.windows
        ]
    elif kind in ("toilet", "wc"):
        ev = [_ev_entity(e, "toilet") for e in plan.toilets]
    elif kind in ("washbasin", "sink", "basin", "lavabo"):
        ev = [_ev_entity(e, "washbasin") for e in plan.washbasins]
    elif kind in ("bathtub", "tub", "bath"):
        ev = [_ev_entity(e, "bathtub") for e in plan.bathtubs]
    elif kind in ("shower", "douche", "dusche", "doccia"):
        ev = [_ev_entity(e, "shower") for e in plan.showers]
    elif kind in ("apartment", "unit", "dwelling"):
        ev = [Evidence(ref=plan.unit_handles[k], label=f"apartment {k}", bbox=p.bounds)
              for k, p in plan.units.items()]
    elif kind == "room":
        ev = [_ev_room(r) for r in plan.rooms if r.number]
    else:
        raise ToolError(f"Unknown symbol kind {kind!r}. Use one of {SYMBOLS}")
    return Answer(value=len(ev), unit="count", method=f"DXF {kind} symbols", evidence=ev)


def _union(bs):
    return (min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs))


def _match_label(r: Room, label: str) -> bool:
    return _norm(r.name) == _norm(label)


def dxf_count_rooms(path: str, label: str) -> Answer:
    """Rooms whose name label equals `label` exactly (as written on the plan)."""
    plan = load_plan(path)
    hits = [r for r in plan.rooms if _match_label(r, label)]
    if not hits:
        names = sorted({r.name for r in plan.rooms if r.name})
        return Answer(value=0, unit="count", method="DXF room labels",
                      note=f"no room labelled {label!r}; labels on plan: {names}")
    return Answer(value=len(hits), unit="count", method="DXF room labels",
                  evidence=[_ev_room(r) for r in hits])


def dxf_room_area(path: str, room: str) -> Answer:
    """Area of one room, given its number (e.g. '2.10') or a unique name."""
    plan = load_plan(path)
    key = _norm(room)
    hits = [r for r in plan.rooms if _norm(r.number) == key] or [
        r for r in plan.rooms if _match_label(r, room)
    ]
    if not hits:
        raise ToolError(f"No room {room!r}. Rooms: {[r.label for r in plan.rooms if r.number][:60]}")
    if len(hits) > 1:
        raise ToolError(f"{room!r} is ambiguous: {[r.label for r in hits]}. Use the room number.")
    r = hits[0]
    src = "area label on plan" if r.area_label is not None else "outline polygon area"
    return Answer(value=round(r.area, 2), unit="m2", method=f"DXF {src}", evidence=[_ev_room(r)])


def dxf_total_area(
    path: str, label: str | None = None, apartment: str | None = None,
    include_outdoor: bool = False,
) -> Answer:
    """Sum of numbered room areas, optionally only rooms named `label` or in `apartment`."""
    plan = load_plan(path)
    # Unnamed numbered rooms are usually bathrooms; tiny ones are shafts and don't count.
    rooms = [r for r in plan.rooms if r.number and (r.name or r.polygon.area >= 2.5)]
    if label:
        rooms = [r for r in rooms if _match_label(r, label)]
    if apartment:
        a = apartment.strip().upper()
        if a not in plan.units:
            raise ToolError(f"No apartment {apartment!r}. Apartments: {sorted(plan.units)}")
        rooms = [r for r in rooms if r.unit == a]
    if not include_outdoor:
        rooms = [r for r in rooms if not r.outdoor]
    if not rooms:
        raise ToolError("No rooms match those filters.")
    total = sum(r.area for r in rooms)
    return Answer(value=round(total, 2), unit="m2",
                  method=f"sum of {len(rooms)} room areas (labels on plan, else polygon)",
                  evidence=[_ev_room(r) for r in rooms])


def dxf_room_list(path: str) -> Answer:
    """Every room with number, name, area and apartment, for open questions."""
    plan = load_plan(path)
    rows = [
        {"number": r.number, "name": r.name, "area_m2": round(r.area, 2), "apartment": r.unit}
        for r in plan.rooms
    ]
    return Answer(value=rows, method="DXF room table", evidence=[_ev_room(r) for r in plan.rooms])
