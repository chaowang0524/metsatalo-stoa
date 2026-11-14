"""Shared reference-based grading for free-form answers."""

from stoa import verda
from stoa.answer import parse_llm_answer

JUDGE = """Grade a candidate answer to a question about a construction drawing or BIM model against the reference answer.
Score 1 if the candidate states the same key facts as the reference (wording, order, extra detail and language may differ; numbers must agree), 0.5 if partly right, 0 if wrong, missing or contradicting the reference.
Reply with JSON only: {{"answer": <1|0.5|0>, "explanation": "<one short sentence>"}}

Question: {q}

Reference answer: {gt}

Candidate answer: {a}"""


class JudgeError(ValueError):
    """Invalid grading response, with usage retained for cost accounting."""

    def __init__(self, message: str, usage: dict):
        super().__init__(message)
        self.usage = {"endpoint": "deepseek", "llm_calls": 1, **usage}


def judge_text(question: str, reference: str, candidate: str, context: dict | None = None) -> dict:
    resp = verda.chat(
        "judge text answer",
        [{"role": "user", "content": JUDGE.format(q=question, gt=reference, a=candidate)}],
        endpoint="deepseek", context=context, max_tokens=300, temperature=0,
    )
    usage = verda.usage_of(resp, "deepseek")
    data = parse_llm_answer(verda.text_of(resp))
    try:
        value = float(data["answer"])
    except (TypeError, ValueError) as e:
        raise JudgeError("Judge did not return a score of 0, 0.5 or 1", usage) from e
    if value not in (0.0, 0.5, 1.0):
        raise JudgeError(f"Invalid judge score: {value}", usage)
    return {
        "score": value, "explanation": str(data.get("explanation", "")),
        "endpoint": "deepseek", "llm_calls": 1, **usage,
    }
