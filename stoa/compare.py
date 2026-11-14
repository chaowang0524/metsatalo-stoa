"""Compare eval runs of different routing models on the same questions.

    uv run python -m stoa.compare out/eval_deepseek_*.jsonl out/eval_kimi_*.jsonl ...

Numeric questions use the challenge scoring already in each row. Free-form text
answers are graded against the reference by an LLM judge (deepseek, the cheapest
endpoint), which only sees the question, the reference and one candidate, never
which model wrote it. Grades are cached in out/bench/judge_cache.json.
"""

import collections
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from stoa import verda
from stoa.judge import judge_text

CACHE = Path("out/bench/judge_cache.json")


def judge(q: str, gt: str, a: str) -> float:
    try:
        return judge_text(q, gt, a, context={"run": "compare"})["score"]
    except (TypeError, ValueError):
        return 0.0


def main() -> None:
    runs = {}
    for p in sys.argv[1:]:
        with open(p, encoding="utf-8-sig") as f:
            rows = [json.loads(l) for l in f]
        runs[rows[0].get("router") or Path(p).stem] = {r["qid"]: r for r in rows}
    common = set.intersection(*(set(r) for r in runs.values()))
    cache = json.loads(CACHE.read_text(encoding="utf-8-sig")) if CACHE.exists() else {}

    todo = []
    for name, rows in runs.items():
        for qid in common:
            r = rows[qid]
            if r["correct"] is None and r.get("text_score") is None:
                a = str((r.get("answer") or {}).get("value") or "")
                key = hashlib.sha1(f"{r['question']}\x00{a}".encode()).hexdigest()
                r["_key"] = key
                if key not in cache:
                    todo.append((key, r["question"], str(r["gt"]), a))
    with ThreadPoolExecutor(8) as pool:
        for key, s in zip([t[0] for t in todo], pool.map(lambda t: judge(*t[1:]) if t[3] else 0.0, todo)):
            cache[key] = s
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache), encoding="utf-8")

    sources = sorted({runs[next(iter(runs))][q]["source"] for q in common})
    print(f"{len(common)} common questions; text answers judged by deepseek against the reference\n")
    hdr = f"{'model':<10}{'source':<17}{'n':>4}{'num acc':>9}{'text':>7}{'tok/q':>8}{'$/1kq':>8}{'s/q':>7}{'fail':>6}"
    print(hdr)
    for name, rows in runs.items():
        ep = verda.ENDPOINTS[name] if name in verda.ENDPOINTS else None
        for src in sources + ["ALL"]:
            rs = [rows[q] for q in common if src == "ALL" or rows[q]["source"] == src]
            num = [r["correct"] for r in rs if r["correct"] is not None]
            txt = [r["text_score"] if r.get("text_score") is not None else cache[r["_key"]]
                   for r in rs if r["correct"] is None]
            tok = sum(r.get("prompt_tokens", 0) + r.get("completion_tokens", 0) for r in rs) / len(rs)
            # Older rows have no cost_usd: price them all at the router's rates.
            usd = sum(r["cost_usd"] if "cost_usd" in r else
                      (r.get("prompt_tokens", 0) * getattr(ep, "PRICE_IN", 0)
                       + r.get("completion_tokens", 0) * getattr(ep, "PRICE_OUT", 0)) / 1e6
                      for r in rs) / len(rs) * 1e3
            sec = sum(r["seconds"] for r in rs) / len(rs)
            fail = sum(bool(r.get("error")) or not r.get("answer") for r in rs)
            f = lambda xs: f"{sum(xs) / len(xs):.0%}" if xs else "-"
            print(f"{name:<10}{src:<17}{len(rs):>4}{f(num):>9}{f(txt):>7}{tok:>8.0f}{usd:>8.3f}{sec:>7.1f}{fail:>6}")
        print()

    # Questions where the models disagree on numeric correctness.
    diff = collections.Counter()
    for q in common:
        verdicts = tuple(runs[n][q]["correct"] for n in runs)
        if None not in verdicts and len(set(verdicts)) > 1:
            diff[verdicts] += 1
    print("numeric disagreements (" + ", ".join(runs) + "):")
    for v, n in diff.most_common():
        print(f"  {v}: {n}")


if __name__ == "__main__":
    main()
