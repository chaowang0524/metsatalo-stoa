"""Score the CubiCasa5K model's counts against the cubicasa5k questions, per
question type and per split of the model's own training data.

    uv run python -m stoa.cubicasa_probe

Needs out/cubicasa_cache filled by cubicasa_tools.analyse() and the split
lists in CubiCasa5k/data/cubicasa5k/{train,val,test}.txt. Only the test and
val rows say how the model does on drawings it has not been trained on.
"""

import collections
import json
from pathlib import Path

from stoa.tools.cubicasa_tools import REPO, counts

DATASET = Path("junction-dataset-preview")

# English question prefix -> how to count it from the model output.
RULES = {
    "How many windows": lambda c: c.get("Window", 0),
    "How many doors": lambda c: c.get("Door", 0),
    "How many toilet bowls": lambda c: c.get("Toilet", 0),
    "How many sinks": lambda c: c.get("Sink", 0),
    "How many showers and bathtubs": lambda c: c.get("Bathtub", 0),
    "How many saunas": lambda c: min(c.get("Sauna Bench", 0), 1),
    "How many kitchens": lambda c: c.get("Kitchen", 0),
    "How many bedrooms": lambda c: c.get("Bed Room", 0),
    "How many bathrooms": lambda c: c.get("Bath", 0),
    "How many balconies": lambda c: c.get("Outdoor", 0),
}


def splits() -> dict[str, str]:
    out = {}
    for s in ("train", "val", "test"):
        for line in (REPO / f"data/cubicasa5k/{s}.txt").read_text().split():
            out[line.strip("/").replace("/", "-")] = s
    return out


def main() -> None:
    split = splits()
    qs = [json.loads(l) for l in (DATASET / "questions.jsonl").open()]
    hits = collections.defaultdict(lambda: collections.defaultdict(list))
    off = collections.defaultdict(list)  # signed error on unseen drawings
    for q in qs:
        if "/cubicasa5k/" not in q["path"]:
            continue
        rule = next((f for p, f in RULES.items() if q["question"].startswith(p)), None)
        if rule is None:
            continue
        path = DATASET / q["path"]
        if not (Path("out/cubicasa_cache") / f"{path.stem}.json").exists():
            continue
        kind = next(p for p in RULES if q["question"].startswith(p))[9:]
        s = "train" if split[path.stem] == "train" else "test+val"
        pred, gt = rule(counts(path)), int(float(q["answer"]))
        hits[kind][s].append(pred == gt)
        if s == "test+val":
            off[kind].append(pred - gt)

    print(f"{'question':<26}{'train n':>8}{'acc':>7}{'test+val n':>12}{'acc':>7}{'mean err':>10}")
    for kind, by in hits.items():
        tr, te = by["train"], by["test+val"]
        f = lambda xs: f"{sum(xs) / len(xs):.0%}" if xs else "-"
        e = off[kind]
        print(f"{kind:<26}{len(tr):>8}{f(tr):>7}{len(te):>12}{f(te):>7}{sum(e) / len(e) if e else 0:>+10.2f}")


if __name__ == "__main__":
    main()
