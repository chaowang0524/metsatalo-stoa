"""Run the pipeline over questions.jsonl and score it with the challenge rules.

    uv run python -m stoa.eval --per-source 20 --workers 8
    uv run python -m stoa.eval --source swiss_dwellings --limit 100

Counts must match exactly; area, length and volume within 2%. Text answers are
free-form and reported but not scored.
"""

import argparse
import collections
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from stoa.pipeline import answer

DATASET = Path("junction-dataset-preview")


def score(q: dict, value) -> bool | None:
    if q["type"] == "text":
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    gt = float(q["answer"])
    if q["type"] == "count":
        return round(v) == gt and abs(v - round(v)) < 1e-6
    return abs(v - gt) <= 0.02 * abs(gt) if gt else abs(v) < 1e-6


def load(args) -> list[dict]:
    qs = [json.loads(l) for l in (DATASET / "questions.jsonl").open()]
    for i, q in enumerate(qs):
        q["id"] = i
        q["source"] = q["path"].split("/")[1]
    if args.source:
        qs = [q for q in qs if q["source"] == args.source]
    rng = random.Random(args.seed)
    if args.per_source:
        by = collections.defaultdict(list)
        for q in qs:
            by[q["source"]].append(q)
        qs = [q for g in by.values() for q in rng.sample(g, min(args.per_source, len(g)))]
    elif args.limit:
        qs = rng.sample(qs, min(args.limit, len(qs)))
    return qs


def run_one(q: dict) -> dict:
    t0 = time.perf_counter()
    try:
        res = answer(DATASET / q["path"], q["question"], context={"qid": q["id"], "run": "eval"})
        out = res.to_json()
        value = res.answer.value if res.answer else None
    except Exception as e:  # keep the run going; record the failure
        out, value = {"error": f"{type(e).__name__}: {e}"}, None
    out.update(qid=q["id"], question=q["question"], source=q["source"], type=q["type"], gt=q["answer"],
               correct=score(q, value), seconds=round(time.perf_counter() - t0, 2))
    if out.get("answer"):
        out["answer"]["evidence"] = out["answer"]["evidence"][:5]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--per-source", type=int)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    qs = load(args)
    out_path = Path("out") / f"eval_{datetime.now():%Y%m%d_%H%M%S}.jsonl"
    out_path.parent.mkdir(exist_ok=True)
    print(f"{len(qs)} questions -> {out_path}")

    rows = []
    with ThreadPoolExecutor(args.workers) as pool, out_path.open("w") as f:
        for i, row in enumerate(pool.map(run_one, qs), 1):
            rows.append(row)
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            if i % 25 == 0:
                print(f"  {i}/{len(qs)}")

    print(f"\n{'source':<17}{'n':>5}{'scored':>8}{'acc':>8}{'llm/q':>7}{'tok/q':>8}{'det%':>6}")
    groups = collections.defaultdict(list)
    for r in rows:
        groups[r["source"]].append(r)
    groups["ALL"] = rows
    for src, rs in groups.items():
        scored = [r for r in rs if r["correct"] is not None]
        acc = sum(r["correct"] for r in scored) / len(scored) if scored else float("nan")
        llm = sum(r.get("llm_calls", 0) for r in rs) / len(rs)
        tok = sum(r.get("prompt_tokens", 0) + r.get("completion_tokens", 0) for r in rs) / len(rs)
        det = sum(bool(r.get("answer") and r["answer"]["deterministic"]) for r in rs) / len(rs)
        print(f"{src:<17}{len(rs):>5}{len(scored):>8}{acc:>8.1%}{llm:>7.2f}{tok:>8.0f}{det:>6.0%}")


if __name__ == "__main__":
    main()
