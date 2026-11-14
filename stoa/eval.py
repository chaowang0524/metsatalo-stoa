"""Run the pipeline over questions.jsonl and score it with the challenge rules.

    uv run python -m stoa.eval --per-source 20 --workers 8
    uv run python -m stoa.eval --source swiss_dwellings --limit 100
    uv run python -m stoa.eval --per-source 30 --skip cubicasa5k --router kimi

Counts must match exactly; area, length and volume within 2%. Text answers are
graded against the reference by DeepSeek (0, 0.5 or 1), after answering finishes.
Use --score-existing <results.jsonl> to grade saved answers without rerunning them.
"""

import argparse
import collections
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from stoa import pipeline
from stoa.pipeline import answer
from stoa.judge import JudgeError, judge_text

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
    with (DATASET / "questions.jsonl").open(encoding="utf-8-sig") as f:
        qs = [json.loads(l) for l in f]
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
    # Skip after sampling, so the other sources get the same questions as a full run.
    if args.skip:
        qs = [q for q in qs if q["source"] not in args.skip.split(",")]
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


def grade_text(row: dict) -> dict:
    """Keep judge failures separate from incorrect answers and retain judge usage."""
    if row["type"] != "text" or row.get("text_score") is not None:
        return row
    t0 = time.perf_counter()
    row.update(text_score=None, judge=None, judge_error=None)
    value = (row.get("answer") or {}).get("value")
    if value is None or (isinstance(value, str) and not value.strip()):
        row["text_score"] = 0.0
        row["judge"] = {"score": 0.0, "explanation": "No answer was returned.", "llm_calls": 0}
    else:
        try:
            row["judge"] = judge_text(
                row["question"], str(row["gt"]), str(value),
                context={"qid": row["qid"], "run": "eval", "phase": "judge"},
            )
            row["text_score"] = row["judge"]["score"]
        except Exception as e:
            row["judge_error"] = f"{type(e).__name__}: {e}"
            if isinstance(e, JudgeError):
                row["judge"] = e.usage
    row["judge_seconds"] = round(time.perf_counter() - t0, 3)
    return row


def grade_rows(rows: list[dict], workers: int) -> None:
    todo = [r for r in rows if r["type"] == "text" and r.get("text_score") is None]
    if not todo:
        return
    print(f"Grading {len(todo)} text answers with DeepSeek ({workers} workers)...")
    with ThreadPoolExecutor(workers) as pool:
        for i, _ in enumerate(pool.map(grade_text, todo), 1):
            if i % 10 == 0 or i == len(todo):
                print(f"  text {i}/{len(todo)}")


def write_rows(path: Path, rows: list[dict]) -> None:
    """Replace only after a complete UTF-8 results file has been written."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    temporary.replace(path)


def print_summary(rows: list[dict], router: str) -> None:
    print(f"\nprofile: {router}")
    print("num_acc: exact counts / measures within 2%; text_score: mean judge score (0/0.5/1).")
    print("Usage columns include judging; judge$ is the judging portion of total cost.")
    print(f"{'source':<17}{'n':>5}{'scored':>8}{'num_acc':>9}{'text_score':>12}"
          f"{'llm/q':>7}{'tok/q':>8}{'$/1kq':>8}{'judge$':>9}{'s/q':>6}{'err':>5}{'jerr':>6}{'det%':>6}")
    groups = collections.defaultdict(list)
    for r in rows:
        groups[r["source"]].append(r)
    if rows:
        groups["ALL"] = rows
    for src, rs in groups.items():
        numeric = [r["correct"] for r in rs if r["type"] != "text" and r["correct"] is not None]
        text = [r["text_score"] for r in rs if r["type"] == "text" and r.get("text_score") is not None]
        judges = [r.get("judge") or {} for r in rs]
        llm = (sum(r.get("llm_calls", 0) for r in rs) + sum(j.get("llm_calls", 0) for j in judges)) / len(rs)
        tok = (sum(r.get("prompt_tokens", 0) + r.get("completion_tokens", 0) for r in rs)
               + sum(j.get("prompt_tokens", 0) + j.get("completion_tokens", 0) for j in judges)) / len(rs)
        judge_usd = sum(j.get("cost_usd", 0) for j in judges)
        usd = (sum(r.get("cost_usd", 0) for r in rs) + judge_usd) / len(rs) * 1e3
        sec = sum(r["seconds"] + r.get("judge_seconds", 0) for r in rs) / len(rs)
        err = sum(bool(r.get("error")) or not r.get("answer") for r in rs)
        jerr = sum(bool(r.get("judge_error")) for r in rs)
        det = sum(bool((r.get("answer") or {}).get("deterministic")) for r in rs) / len(rs)
        fmt = lambda values: f"{sum(values) / len(values):.1%}" if values else "-"
        print(f"{src:<17}{len(rs):>5}{len(numeric) + len(text):>8}{fmt(numeric):>9}{fmt(text):>12}"
              f"{llm:>7.2f}{tok:>8.0f}{usd:>8.3f}{judge_usd:>9.5f}{sec:>6.1f}{err:>5}{jerr:>6}{det:>6.0%}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--per-source", type=int)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--skip", help="comma-separated sources to leave out")
    ap.add_argument("--router", help="model profile: hybrid (default), deepseek, kimi or nemotron")
    ap.add_argument("--judge-workers", type=int, default=2, help="parallel text judges after answering (default: 2)")
    ap.add_argument("--score-existing", type=Path, help="grade a saved results file without rerunning answers")
    args = ap.parse_args()
    if args.workers < 1 or args.judge_workers < 1:
        ap.error("--workers and --judge-workers must be at least 1")

    if args.score_existing:
        with args.score_existing.open(encoding="utf-8-sig") as f:
            rows = [json.loads(line) for line in f]
        out_path = args.score_existing.with_name(
            f"{args.score_existing.stem}_scored_{datetime.now():%Y%m%d_%H%M%S}.jsonl"
        )
        grade_rows(rows, args.judge_workers)
        write_rows(out_path, rows)
        print(f"{len(rows)} saved answers -> {out_path}")
        router = next((r["router"] for r in rows if r.get("router")), "saved answers")
        print_summary(rows, router)
        return

    qs = load(args)
    if args.router:
        pipeline.ROUTER = args.router
    out_path = Path("out") / f"eval_{pipeline.ROUTER}_{datetime.now():%Y%m%d_%H%M%S}.jsonl"
    out_path.parent.mkdir(exist_ok=True)
    print(f"{len(qs)} questions -> {out_path}")

    rows = []
    with ThreadPoolExecutor(args.workers) as pool, out_path.open("w", encoding="utf-8") as f:
        for i, row in enumerate(pool.map(run_one, qs), 1):
            rows.append(row)
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            if i % 25 == 0:
                print(f"  {i}/{len(qs)}")

    grade_rows(rows, args.judge_workers)
    write_rows(out_path, rows)
    print_summary(rows, pipeline.ROUTER)


if __name__ == "__main__":
    main()
