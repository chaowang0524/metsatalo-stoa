"""Apply the engineer's plan edits to an Ask answer.

The pipeline answers from the source file, so it never sees what the engineer
rejected or added on the plan. For a count of one component kind this corrects
the number (rejected objects out, added ones in) without another LLM call; for
anything else it only says the plan has edits the answer doesn't reflect.
"""

import math
from collections import Counter


def _poly(a: dict) -> list[list[float]]:
    """Corners of an added symbol, the same as app.js corners()."""
    if a.get("cx") is None:
        x0, y0, x1, y1 = a["bbox"]
        return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    r = math.radians(a.get("rot") or 0)
    c, s = math.cos(r), math.sin(r)
    out = []
    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        x, y = sx * a["w"] / 2, sy * a["h"] / 2
        out.append([a["cx"] + x * c - y * s, a["cy"] + x * s + y * c])
    return out


def _kind(doc: dict, refs: list[str], question: str) -> tuple[str | None, bool]:
    """The component kind the answer counts, and whether its evidence is the
    plan's own objects (only then can rejected ones be taken out). The kind is
    the one the evidence comes from, else the one the question names."""
    comps = [f for f in doc["facts"] if f.get("group") == "Components" and f.get("kind")]
    owner = {it["ref"]: f["kind"] for f in comps for it in f["items"]}
    kinds = Counter(owner[r] for r in refs if r in owner)
    if len(kinds) == 1:
        return next(iter(kinds)), True
    if kinds:
        return None, False  # evidence spans several kinds: not a single-kind count
    q = question.lower()
    named = {f["kind"] for f in comps if f["kind"] in q or f.get("label", "").lower() in q}
    # No evidence at all (e.g. an answer of 0) still counts as the plan's objects.
    return (named.pop(), not refs) if len(named) == 1 else (None, False)


def apply(doc: dict, edits: dict, question: str, refs: list[str], out: dict) -> None:
    """Correct `out` (the /api/ask response) in place. `refs` are all evidence refs."""
    added, rejected = edits.get("added", []), set(edits.get("rejected", []))
    ans = out.get("answer")
    if not ans or not (added or rejected):
        return
    value = ans.get("value")
    is_count = ans.get("unit") == "count" and isinstance(value, int) and not isinstance(value, bool)
    kind, linked = _kind(doc, refs, question) if is_count else (None, False)
    mine = [k for k in rejected if k.startswith(f"{kind}:")] + [a for a in added if a.get("kind") == kind]
    if kind is None or (not linked and mine):
        # Not a count of one kind, or counted from something other than the plan's
        # objects (PDF text, a vision pass): the edits can't be mapped onto it.
        hit = sum(1 for r in refs if any(k.endswith(f":{r}") for k in rejected))
        n = len(mine) if kind else len(added) + len(rejected)
        out["corrections"] = {"applied": False, "edits": n, "rejected_in_evidence": hit, "kind": kind}
        return
    gone = [r for r in dict.fromkeys(refs) if f"{kind}:{r}" in rejected]
    new = [a for a in added if a.get("kind") == kind]
    if not gone and not new:
        return
    for it in out["items"]:
        if it["ref"] in gone:
            it["rejected"] = True
    out["items"] += [{"ref": a["id"], "label": f"{kind} (added by the engineer)", "added": True,
                      "shape": {"poly": _poly(a)}} for a in new]
    ans["value"] = max(0, value - len(gone) + len(new))
    out["corrections"] = {"applied": True, "kind": kind, "before": value,
                          "removed": len(gone), "added": len(new)}
