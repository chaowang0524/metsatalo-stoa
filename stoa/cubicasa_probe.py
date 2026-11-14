"""Score the CubiCasa5K model's counts against the cubicasa5k questions, per
question type and per split of the model's own training data.

    uv run python -m stoa.cubicasa_probe
    uv run python -m stoa.cubicasa_probe --hybrid   # also run the LLM pipeline on test+val

Needs out/cubicasa_cache filled by cubicasa_tools.analyse() and the split
lists in CubiCasa5k/data/cubicasa5k/{train,val,test}.txt. Only the test and
val rows say how the model does on drawings it has not been trained on.
With --hybrid, the same test+val questions go through stoa.pipeline (answers
cached in out/bench/cubicasa_hybrid.jsonl) for a per-type head-to-head.
"""

import argparse
import collections
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from stoa.tools.cubicasa_tools import REPO, counts

DATASET = Path("junction-dataset-preview")
HYBRID_CACHE = Path("out/bench/cubicasa_hybrid.jsonl")

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
        for line in (REPO / f"data/cubicasa5k/{s}.txt").read_text(encoding="utf-8-sig").split():
            out[line.strip("/").replace("/", "-")] = s
    return out


def run_hybrid(qs: list[dict], profile: str) -> dict[tuple, dict]:
    """Pipeline answers for (path, question), cached across runs."""
    from stoa.pipeline import answer

    done = {}
    if HYBRID_CACHE.exists():
        for line in HYBRID_CACHE.open(encoding="utf-8"):
            r = json.loads(line)
            if r["profile"] == profile:
                done[(r["path"], r["question"])] = r
    todo = [q for q in qs if (q["path"], q["question"]) not in done]

    def one(q: dict) -> dict:
        res = answer(DATASET / q["path"], q["question"], context={"run": "cubicasa_probe"}, router=profile)
        return {"profile": profile, "path": q["path"], "question": q["question"],
                "value": res.answer.value if res.answer else None, "cost_usd": res.cost_usd}

    print(f"{profile}: {len(done)} cached, {len(todo)} to run")
    HYBRID_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(8) as pool, HYBRID_CACHE.open("a", encoding="utf-8") as f:
        for r in pool.map(one, todo):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            done[(r["path"], r["question"])] = r
    return done


def as_int(v) -> int | None:
    try:
        return round(float(v))
    except (TypeError, ValueError):
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hybrid", action="store_true", help="also score the LLM pipeline on test+val")
    ap.add_argument("--profile", default="hybrid")
    args = ap.parse_args()

    split = splits()
    rows = []
    with (DATASET / "questions.jsonl").open(encoding="utf-8-sig") as f:
        qs = [json.loads(l) for l in f]
    for q in qs:
        if "/cubicasa5k/" not in q["path"]:
            continue
        prefix = next((p for p in RULES if q["question"].startswith(p)), None)
        path = DATASET / q["path"]
        if prefix is None or not (Path("out/cubicasa_cache") / f"{path.stem}.json").exists():
            continue
        rows.append({**q, "kind": prefix[9:], "gt": int(float(q["answer"])),
                     "cnn": RULES[prefix](counts(path)),
                     "split": "train" if split[path.stem] == "train" else "test+val"})

    llm = run_hybrid([r for r in rows if r["split"] == "test+val"], args.profile) if args.hybrid else {}

    f = lambda xs: f"{sum(xs) / len(xs):.0%}" if xs else "-"
    head = f"{'question':<22}{'train n':>8}{'CNN':>6}{'test+val n':>12}{'CNN':>6}{'CNN err':>9}"
    if llm:
        head += f"{args.profile:>9}{'LLM err':>9}{'better':>8}"
    print(head)
    cost = 0.0
    for kind in dict.fromkeys(r["kind"] for r in rows):
        tr = [r["cnn"] == r["gt"] for r in rows if r["kind"] == kind and r["split"] == "train"]
        te = [r for r in rows if r["kind"] == kind and r["split"] == "test+val"]
        cnn = [r["cnn"] == r["gt"] for r in te]
        err = sum(r["cnn"] - r["gt"] for r in te) / len(te) if te else 0
        line = f"{kind:<22}{len(tr):>8}{f(tr):>6}{len(te):>12}{f(cnn):>6}{err:>+9.2f}"
        if llm:
            vals = [as_int(llm[(r["path"], r["question"])]["value"]) for r in te]
            hit = [v == r["gt"] for v, r in zip(vals, te)]
            lerr = [v - r["gt"] for v, r in zip(vals, te) if v is not None]
            cost += sum(llm[(r["path"], r["question"])]["cost_usd"] for r in te)
            a, b = sum(cnn), sum(hit)
            line += f"{f(hit):>9}{sum(lerr) / len(lerr) if lerr else 0:>+9.2f}{'CNN' if a > b else args.profile if b > a else 'tie':>8}"
        print(line)
    if llm:
        n = sum(r["split"] == "test+val" for r in rows)
        print(f"\n{args.profile} cost on {n} test+val questions: ${cost:.2f} (${cost / n * 1000:.2f} per 1k); CNN: $0")


if __name__ == "__main__":
    main()
