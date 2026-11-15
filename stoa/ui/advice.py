"""Advisory questions ("do we need more doors / windows / toilets?") answered by a
rule check on the plan's facts.

There is no number in the drawing to look up for these, so a vision model answers
them with a bare yes/no that nothing on the plan supports. The plan's facts
already hold every component and room with its shape (exact CAD geometry for
DXF/IFC plans, Gemini detections for raster ones), so each check is geometric.
The rules are data (RULES): which room types need which component, and whether
it sits on the room's boundary (doors, windows) or inside it (fixtures).

    question → DeepSeek picks a rule, types unknown room labels   (one small call)
    rule     → each room of those types has the component?        (shapely, free)

The engineer's edits are applied first: rejected objects are left out, added
ones count. The answer names the rule it used and what it does not check.
"""

import json
import re

from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

from stoa import verda
from stoa.ui.corrections import _poly

TOL = 0.1  # wall thickness as a share of the median door width (~0.9 m); "reach" in RULES overrides it across the wall

# Room types from labels in the dataset's languages (en, fi, de, fr, it, sv, da, et).
# A label can have several ("ELUTUBA+KÖÖK", "Séjour/repas"). Labels none of these
# match are typed by the router LLM.
ROOM_TYPES = {
    "bed": r"bed|makuu|schlaf|chambre|camera da letto|sovrum|magamis|soverum|slaapkamer|\bmh\b",
    "room": r"pi[eè]ce|stanza|zimmer|^room|huone|værelse|^camera\b|\bh\b",
    "living": r"living|wohn|s[ée]jour|soggiorno|olohuone|elutuba|vardagsrum|\bstue\b|salon|lounge|\boh\b|dining|repas|pranzo|essen",
    "kitchen": r"kitchen|küche|cuisine|cucina|keitti|\bkök\b|köök|køkken|keuken|\bk\b|\bkt\b|\brt\b",
    "bath": r"bath|bagno|bain|\bbad\b|kylpy|badrum|dusche|douche|shower|pesuh|\bkph\b|\bph\b",
    "wc": r"\bwc\b|toilet|tualett|gabinetto|vessa|\bw\.c\.",
    "sauna": r"sauna|\bs\b",
    "storage": r"storage|ripostiglio|r[ée]duit|abstell|varasto|förråd|depot|abiruum|closet|dressing|vaate|keller|cellar|\bcave\b|\bvar\b|\bvh\b",
    "utility": r"utility|laundry|buanderie|lavanderia|wasch|kodinhoito|\bkhh\b|tekn|techni|server",
    "hall": r"\bhall|entry|entr[ée]e|corrid|korr?id|couloir|\bgang\b|eteinen|esik|flur|vestibul|ingresso|diele|disimpegno|\bet\b",
    "office": r"office|kontor|m[øo]derum|büro|bureau|ufficio|toimisto|meeting",
    "outdoor": r"balcon|balkon|terra|loggia|patio|porch|outdoor|garden|parveke|veranta|undefined",
    "stair": r"ascenseur|trepikoda|escalier|scala|treppe|stair|porras|trappe|ascensor|elevator|\blift\b|aufzug|hissi|skakt|schacht|shaft|gaine",
    "other": r"garage|autotalli|carport|\bak\b",
}
TYPES = list(ROOM_TYPES)

# The checks. rooms: room types that need the component; relation: where it
# must be; note: room types where a missing one is only mentioned (often open
# plan); words: fallback match when the router LLM is unavailable.
RULES = [
    {"id": "doors", "kinds": ["door"], "noun": ("door", "doors"), "relation": "boundary", "exits": True, "reach": 0.25,
     "rooms": {"bed", "room", "office", "bath", "wc", "sauna", "storage", "utility"}, "note": {"living", "kitchen", "hall"},
     "text": "bedrooms, offices, bathrooms, WCs, saunas, storage and utility rooms need their own door, and at least one "
             "door leads out of the plan or to a staircase",
     "unchecked": "building-code exits, door widths, fire doors",
     "words": r"\bdoors?\b|\bov(i|ea|ia|et|ien|elle|ille)\b|\btür(en)?\b|\bportes?\b|\bdörr(ar)?\b|\bporta\b"},
    # Windows sit mid-wall in thick exterior walls: reach ~0.6 m (0.7 door widths) across the wall.
    {"id": "windows", "kinds": ["window"], "noun": ("window", "windows"), "relation": "boundary", "reach": 0.7,
     "outdoor_doors": True,
     "rooms": {"bed", "room", "office", "living"}, "note": {"kitchen"},
     "text": "bedrooms, offices and living rooms need daylight from a window or a door to a balcony or terrace "
             "(kitchens are often internal, so only noted)",
     "unchecked": "window-to-floor area ratios, egress window sizes",
     "words": r"window|ikkun|fenster|fen[êe]tre|finestr|fönster|vindue|\baken"},
    {"id": "toilets", "kinds": ["toilet"], "noun": ("toilet", "toilets"), "relation": "inside", "plan_min": 1,
     "rooms": {"wc"}, "note": set(),
     "text": "every WC has a toilet, and the plan has at least one",
     "unchecked": "toilets per occupant, accessible toilets",
     "words": r"toilet|\bwc\b|vess|klo"},
    {"id": "washbasins", "kinds": ["sink", "washbasin"], "noun": ("washbasin", "washbasins"), "relation": "inside",
     "rooms": {"bath", "wc"}, "note": set(),
     "text": "every bathroom and WC has a washbasin",
     "unchecked": "kitchen sinks (not told apart from washbasins on image plans)",
     "words": r"basin|\bsinks?\b|lavabo|pesuallas|waschbecken|lavandino"},
    {"id": "bath_fixtures", "kinds": ["shower", "bathtub"], "noun": ("shower or bathtub", "showers or bathtubs"),
     "relation": "inside", "rooms": {"bath"}, "note": set(),
     "text": "every bathroom has a shower or a bathtub",
     "unchecked": "water supply and drainage",
     "words": r"shower|bathtub|\btub|suihku|amme|dusche|badewanne|douche|baignoire|docc[ei]|vasca"},
    {"id": "closets", "kinds": ["closet"], "noun": ("closet", "closets"), "relation": "inside",
     "rooms": {"bed"}, "note": set(),
     "text": "every bedroom has a closet",
     "unchecked": "closet sizes",
     "words": r"closet|wardrobe|vaatekaap|kaapp|schrank|armoire|armadio|garderob"},
]
BY_ID = {r["id"]: r for r in RULES}
ADVICE_WORD = re.compile(r"\bneed|\benough\b|\bmore\b|\bmissing\b|\black|\bshould\b|\badd(ing|itional)?\b"
                         r"|\btarpeek|\briittäv|\bsufficient|\brequire|\bextra\b|\banother\b|\btarvi|\briittä"
                         r"|\blisää|\bpuuttu|\bbrauch|\bgenug\b|\bfehl|\bmehr\b|\bbesoin\b|\bassez\b|\bmanque"
                         r"|\bbehöv|\bräcker\b|\bsaknas\b|\bmanca|\bmancan|\bbisogn|\bservono\b|\bfalta|\bnecesit", re.I)
COUNT_ONLY = re.compile(r"^\s*(how many|montako|kuinka monta|wie viele|combien)\b(?!.*\b(more|lisää|mehr|plus)\b)", re.I)

ROUTER = """You decide whether a question about a floor plan asks for one of these checks.
{checks}
- none: anything else, including plain counts ("how many doors are there?"), areas, sizes and locations.
Pick a check only when the question asks whether something is enough, missing or needed, or whether more should be added.
Also give room types for each listed room label, from: {types}. A label can have several types (a living room with kitchen).
Reply with JSON only: {{"check": "<id or none>", "room_types": {{"<label>": ["<type>", ...]}}}}"""


def room_types(label: str) -> set[str]:
    name = re.sub(r"^[\d.\s]+", "", label)  # Swiss labels start with the room number, "1.02 Stanza"
    return {t for t, rx in ROOM_TYPES.items() if re.search(rx, name, re.I)}


def pick_rule_by_words(question: str) -> str | None:
    """Without the router: an advice word and exactly one rule's object word."""
    if not ADVICE_WORD.search(question) or COUNT_ONLY.search(question):
        return None
    hits = [r["id"] for r in RULES if re.search(r["words"], question, re.I)]
    return hits[0] if len(hits) == 1 else None


def pick_rule(question: str, unknown: list[str], context: dict | None = None) -> tuple[str | None, dict, dict]:
    """(rule id or None, {label: types} for the unknown labels, usage). One DeepSeek call."""
    checks = "\n".join(f"- {r['id']}: is there a lack of {r['noun'][1]}? Rule: {r['text']}." for r in RULES)
    user = question + (f"\n\nRoom labels: {json.dumps(unknown, ensure_ascii=False)}" if unknown else "")
    resp = verda.chat("pick plan check", [
        {"role": "system", "content": ROUTER.format(checks=checks, types=", ".join(TYPES))},
        {"role": "user", "content": user},
    ], context=context, endpoint="deepseek", max_tokens=400, temperature=0)
    usage = verda.usage_of(resp, "deepseek")
    m = re.search(r"\{.*\}", verda.text_of(resp), re.S)
    data = json.loads(m[0]) if m else {}
    rule = data.get("check") if data.get("check") in BY_ID else None
    types = {k: {t for t in v if t in ROOM_TYPES} for k, v in (data.get("room_types") or {}).items()
             if isinstance(v, list)}
    return rule, types, usage


def _geom(shape: dict):
    if shape.get("poly"):
        g = Polygon(shape["poly"])
        return g if g.is_valid else g.buffer(0)
    return box(*shape["bbox"]) if shape.get("bbox") else None


def _size(g) -> tuple[float, float]:
    x0, y0, x1, y1 = g.bounds
    return min(x1 - x0, y1 - y0), max(x1 - x0, y1 - y0)


def _rooms(doc: dict, rejected: set) -> tuple[list[dict], bool]:
    fact = next((f for f in doc["facts"] if f.get("kind") == "room" and f.get("group") == "Spaces"), None)
    if not fact:
        return [], False
    out = []
    for it in fact["items"]:
        if f"room:{it['ref']}" in rejected:
            continue
        if (g := _geom(it["shape"])) is not None and not g.is_empty:
            out.append({"ref": it["ref"], "label": it["label"], "shape": it["shape"], "geom": g})
    return out, bool(fact.get("deterministic"))


def _objects(doc: dict, kinds: list[str], edits: dict) -> tuple[list[dict], int, int, bool, bool]:
    """(objects, rejected, added, exact, any of these kinds in the plan's data)."""
    facts = [f for f in doc["facts"] if f.get("group") == "Components" and f.get("kind") in kinds]
    rejected = set(edits.get("rejected", []))
    objs, removed = [], 0
    for f in facts:
        for it in f["items"]:
            if f"{f['kind']}:{it['ref']}" in rejected:
                removed += 1
            elif (g := _geom(it["shape"])) is not None:
                objs.append({"ref": it["ref"], "kind": f["kind"], "shape": it["shape"], "geom": g, "added": False})
    added = [a for a in edits.get("added", []) if a.get("kind") in kinds]
    for a in added:
        p = _poly(a)
        objs.append({"ref": a["id"], "kind": a["kind"], "shape": {"poly": p}, "geom": Polygon(p), "added": True})
    exact = bool(facts) and all(f.get("deterministic") for f in facts)
    return objs, removed, len(added), exact, bool(facts or added)


def _door_width(doc: dict, rooms: list[dict]) -> float:
    """The median door width in view units (about 0.9 m), else a guess from the plan size."""
    doors = next((f for f in doc["facts"] if f.get("kind") == "door" and f.get("group") == "Components"), None)
    longs = sorted(_size(g)[1] for it in (doors["items"] if doors else []) if (g := _geom(it["shape"])))
    if longs:
        return longs[len(longs) // 2]
    x0, y0, x1, y1 = unary_union([r["geom"] for r in rooms]).bounds
    return (x1 - x0 + y1 - y0) * 0.04


def _grow(g, across: float, along: float):
    """The object's box grown across its wall by `across` and along it by `along`."""
    x0, y0, x1, y1 = g.bounds
    if x1 - x0 >= y1 - y0:
        return box(x0 - along, y0 - across, x1 + along, y1 + across)
    return box(x0 - across, y0 - along, x1 + across, y1 + along)


def check(doc: dict, rule: dict, edits: dict, types: dict | None = None) -> dict | None:
    """Rooms of the rule's types with and without the component, and the objects matched."""
    rooms, rooms_exact = _rooms(doc, set(edits.get("rejected", [])))
    if not rooms:
        return None
    objs, removed, added, objs_exact, present = _objects(doc, rule["kinds"], edits)
    door = _door_width(doc, rooms)
    tol = max(door * TOL, 1.0)  # about a wall thickness
    reach = max(door * rule.get("reach", TOL), tol)
    # A detected room thinner than a few walls is a bad box, not a room:
    # it would always come out as missing everything.
    slivers = [r for r in rooms if _size(r["geom"])[0] < 3 * tol]
    rooms = [r for r in rooms if r not in slivers]
    if not rooms:
        return None
    for r in rooms:
        r["types"] = room_types(r["label"]) or set((types or {}).get(r["label"], ()))
    # Gaps between rooms (walls) closed up, so only the plan's outer edge is "outside".
    footprint = unary_union([r["geom"] for r in rooms]).buffer(tol * 1.5).buffer(-tol * 1.5)

    def in_wall(objs, across):
        # In a wall: grown across it, an object reaches the rooms on both sides.
        for o in objs:
            grown = _grow(o["geom"], across, tol)
            o["rooms"] = [r for r in rooms if grown.intersects(r["geom"])]
            o["outside"] = grown.difference(footprint).area > 0.05 * grown.area

    if rule["relation"] == "boundary":
        in_wall(objs, reach)
    else:
        for o in objs:
            c = o["geom"].centroid
            o["rooms"] = [r for r in rooms if r["geom"].buffer(tol).contains(Point(c.x, c.y))][:1]
    for r in rooms:
        r["objs"] = [o for o in objs if r in o["rooms"]]
    # Glazed doors to a balcony or terrace light a room too. Unlabelled spaces
    # next to a room are mostly balconies on the Swiss plans.
    extra = []
    if rule.get("outdoor_doors"):
        doors = _objects(doc, ["door"], edits)[0]
        in_wall(doors, tol)
        for d in doors:
            if d["outside"] or any(r["types"] & {"outdoor"} or not r["types"] for r in d["rooms"]):
                d["via"] = True
                extra.append(d)
                for r in d["rooms"]:
                    r["objs"].append(d)
    needs = [r for r in rooms if r["types"] & rule["rooms"] and not r["types"] & {"outdoor", "stair"}]
    return {
        "rooms": rooms, "needs": needs, "objs": objs, "removed": removed, "added": added, "present": present,
        "exact": rooms_exact and objs_exact,
        "missing": [r for r in needs if not r["objs"]],
        "note": [r for r in rooms if not r["objs"] and r not in needs and r["types"] & rule["note"]],
        # A way out: through the plan's outer edge, or into a staircase (flats on an upper floor).
        "exits": [o for o in objs if o.get("outside") or any("stair" in r["types"] for r in o["rooms"])],
        "extra": extra,
        "unmatched": [o for o in objs if not o["rooms"]],
        "untyped": [r for r in rooms if not r["types"]],
        "slivers": slivers,
    }


def _names(rooms: list[dict]) -> str:
    return ", ".join(r["label"] for r in rooms)


def _n(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _verdict(rule: dict, c: dict) -> str:
    one, many = rule["noun"]
    missing, objs, needs = c["missing"], c["objs"], c["needs"]
    # Sure only of exact walls and openings. Detected rooms are often off by a
    # wall, and CAD files don't always carry fixture symbols, so a room without
    # the object may be missing data rather than a missing object.
    sure = c["exact"] and rule["relation"] == "boundary" and any(not o["added"] for o in objs)
    where = "in the drawing's CAD/BIM data" if c["exact"] else "on the plan"
    too_few = len(objs) < rule.get("plan_min", 0)
    if missing and sure:
        value = (f"Yes, probably {len(missing)} more: {_names(missing)} {'has' if len(missing) == 1 else 'have'} "
                 f"no {one} on the plan.")
    elif missing:
        value = (f"Possibly: no {one} was found {where} for {_names(missing)}. Check "
                 f"{'it' if len(missing) == 1 else 'them'} on the drawing; this is often missing "
                 f"{'detections' if not c['exact'] else 'symbols'} rather than a missing {one}.")
    elif too_few:
        value = (f"{'Yes' if c['exact'] else 'Possibly'}: {_n(len(objs), one, many)} found {where}; "
                 f"at least {rule['plan_min']} expected.")
    elif rule.get("exits") and not c["exits"]:
        value = f"Possibly one: every room that needs a {one} has one, but none leading out of the plan or to a staircase was found."
    elif not needs and rule.get("plan_min"):
        value = (f"Probably not: the plan has {_n(len(objs), one, many)} (at least {rule['plan_min']} expected) "
                 f"and no separate room that needs its own.")
    elif not needs:
        value = f"Nothing to check: no room on the plan needs a {one} under this rule."
    else:
        extra = f" and {_n(len(c['extra']), 'door to outside', 'doors to outside')}" if c["extra"] else ""
        value = (f"Probably not. {_n(len(objs), one, many)}{extra} cover {len(needs) - len(missing)} of "
                 f"{_n(len(needs), 'room', 'rooms')} that need one")
        if rule.get("exits"):
            value += (f", and {len(c['exits'])} lead{'s' if len(c['exits']) == 1 else ''}" if c["exact"]
                      else ", and at least one appears to lead") + " out of the plan or to a staircase"
        value += "."
    if missing and too_few:
        value += f" Only {_n(len(objs), one, many)} found {where} in all."
    if missing and rule.get("exits") and not c["exits"]:
        value += f" No {one} leading out of the plan was found either."
    return value


def answer(doc: dict, question: str, edits: dict | None, pick=pick_rule, context: dict | None = None) -> dict | None:
    """The /api/ask response for an advice question one of RULES covers, or None to use the pipeline."""
    edits = edits or {"added": [], "rejected": []}
    rooms, _ = _rooms(doc, set())
    if not rooms:
        return None
    unknown = sorted({r["label"] for r in rooms if not room_types(r["label"])})
    usage, types = {}, {}
    try:
        rule_id, types, usage = pick(question, unknown, context)
    except Exception:  # router down: fall back to keywords, unknown labels stay untyped
        rule_id = pick_rule_by_words(question)
    rule = BY_ID.get(rule_id)
    if rule is None:
        return None
    one, many = rule["noun"]
    c = check(doc, rule, edits, types)
    if c is None or not c["present"]:
        return None  # e.g. no showers in this plan's data: the pipeline may still read the drawing
    notes = []
    if c["note"]:
        notes.append(f"No {one} found for {_names(c['note'])}: often fine for these rooms, check on the plan.")
    if c["unmatched"] and not c["exact"]:
        notes.append(f"{_n(len(c['unmatched']), f'detected {one}', f'detected {many}')} could not be matched to a "
                     f"detected room.")
    if c["slivers"]:
        notes.append(f"Left out {_n(len(c['slivers']), 'room box', 'room boxes')} too thin to be a room "
                     f"({_names(c['slivers'])}).")
    if c["untyped"]:
        notes.append(f"Room type unknown, not checked: {_names(c['untyped'])}.")
    notes.append(f"Rule: {rule['text']}. Not checked: {rule['unchecked']}.")
    if c["added"] or c["removed"]:
        parts = ", ".join(filter(None, [c["added"] and f"+{c['added']} added", c["removed"] and f"−{c['removed']} rejected"]))
        notes.append(f"Includes the engineer's edits ({parts}).")
    if not c["exact"]:
        notes.append(f"{many.capitalize()} and rooms come from the image detection; verify on the plan.")

    def obj_label(o):
        names = [r["label"] for r in o["rooms"]] + (["out of the plan"] if o.get("outside") and rule.get("exits") else [])
        what = f"{o['kind']} added by the engineer" if o["added"] else o["kind"]
        what += " to a balcony / outside" if o.get("via") else ""
        sep = " ↔ " if rule["relation"] == "boundary" else ", "
        return what + (f": {sep.join(names)}" if names else ": no room")

    items = [{"ref": r["ref"], "label": f"{r['label']}: no {one}", "shape": r["shape"]} for r in c["missing"] + c["note"]]
    items += [{"ref": o["ref"], "label": obj_label(o), "shape": o["shape"], **({"added": True} if o["added"] else {})}
              for o in c["objs"] + c["extra"]]
    evidence = [{"ref": it["ref"], "label": it["label"], "value": None} for it in items]
    corrections = ({"applied": True, "kind": rule["kinds"][0], "before": None, "removed": c["removed"], "added": c["added"]}
                   if c["added"] or c["removed"] else None)
    return {
        "path": doc.get("path") or doc.get("file"), "question": question,
        "answer": {"value": _verdict(rule, c), "unit": None, "deterministic": c["exact"],
                   "method": f"Plan check '{rule['id']}': each {one} matched to the room it "
                             + ("opens into" if rule["relation"] == "boundary" else "stands in")
                             + " on the plan's " + ("CAD/BIM geometry" if c["exact"] else "detected objects and rooms")
                             + " (the LLM only picks the check)",
                   "evidence_count": len(evidence), "evidence": evidence[:50], "note": " ".join(notes)},
        "calls": [{"tool": "plan_check", "args": {"rule": rule["id"]}}], "llm_calls": 1 if usage else 0,
        "prompt_tokens": usage.get("prompt_tokens", 0), "completion_tokens": usage.get("completion_tokens", 0),
        "cost_usd": round(usage.get("cost_usd", 0.0), 6), "error": None, "router": "rules",
        "items": items, "unplaced": 0, **({"corrections": corrections} if corrections else {}),
    }
