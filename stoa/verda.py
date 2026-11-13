"""The only way this project calls the Verda inference endpoint.

Every call appends one line to logs/verda_calls.jsonl (see
VERDA_ENDPOINT_GUIDELINES.md): purpose, model, tokens, latency. That log is our
cost evidence for Stoa and our process evidence for the Verda bounty.
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI

MODEL = os.environ.get("VERDA_MODEL", "deepseek-4-1-flash-1017")
LOG_PATH = Path(os.environ.get("VERDA_LOG", "logs/verda_calls.jsonl"))

# USD per million tokens. Unknown until Verda publishes pricing for this model;
# set VERDA_PRICE_IN / VERDA_PRICE_OUT to get cost estimates in the log.
PRICE_IN = float(os.environ.get("VERDA_PRICE_IN", "0"))
PRICE_OUT = float(os.environ.get("VERDA_PRICE_OUT", "0"))

_client: OpenAI | None = None


def client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=os.environ["VERDA_INFERENCE_BASE_URL"],
            api_key=os.environ["VERDA_INFERENCE_API_KEY"],
        )
    return _client


def chat(purpose: str, messages: list[dict], context: dict | None = None, **kwargs):
    """chat.completions.create plus a log line. `purpose` says why we called."""
    kwargs.setdefault("model", MODEL)
    t0 = time.perf_counter()
    error = None
    resp = None
    try:
        resp = client().chat.completions.create(messages=messages, **kwargs)
        return resp
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        raise
    finally:
        usage = getattr(resp, "usage", None)
        p = getattr(usage, "prompt_tokens", 0) or 0
        c = getattr(usage, "completion_tokens", 0) or 0
        has_image = any(
            isinstance(m.get("content"), list)
            and any(part.get("type") == "image_url" for part in m["content"])
            for m in messages
        )
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "purpose": purpose,
            "model": kwargs["model"],
            "prompt_tokens": p,
            "completion_tokens": c,
            "reasoning_tokens": getattr(usage, "reasoning_tokens", None),
            "has_image": has_image,
            "latency_s": round(time.perf_counter() - t0, 3),
            "cost_usd": round((p * PRICE_IN + c * PRICE_OUT) / 1e6, 6),
            "error": error,
            **(context or {}),
        }
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def text_of(resp) -> str:
    """Some responses put the text in reasoning_content instead of content."""
    m = resp.choices[0].message
    return m.content or getattr(m, "reasoning_content", None) or ""
