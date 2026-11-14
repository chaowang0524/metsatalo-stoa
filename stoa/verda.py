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

LOG_PATH = Path(os.environ.get("VERDA_LOG", "logs/verda_calls.jsonl"))


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Fill os.environ from a gitignored .env file, without overriding."""
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            k, sep, v = line.strip().partition("=")
            if sep and not k.startswith("#"):
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


_load_dotenv()

from endpoints.verda import deepseek, kimi, nemotron  # noqa: E402

# One module per endpoint in endpoints/verda/, each with its USD per million
# token prices as PRICE_IN / PRICE_OUT (missing means unknown, logged as 0).
# "inference" and "vision" are the old names, kept so existing callers don't break.
ENDPOINTS = {m.NAME: m for m in (deepseek, kimi, nemotron)}
ENDPOINTS["inference"] = deepseek
ENDPOINTS["vision"] = nemotron

_clients: dict[str, OpenAI] = {}


def client(endpoint: str = "deepseek") -> OpenAI:
    ep = ENDPOINTS[endpoint]
    if ep.NAME not in _clients:
        base = getattr(ep, "BASE_URL", None) or os.environ[ep.BASE_URL_ENV]
        _clients[ep.NAME] = OpenAI(base_url=base, api_key=os.environ[ep.API_KEY_ENV])
    return _clients[ep.NAME]


def chat(purpose: str, messages: list[dict], context: dict | None = None, endpoint: str = "deepseek", **kwargs):
    """chat.completions.create plus a log line. `purpose` says why we called.

    `endpoint` is a name in ENDPOINTS: deepseek, kimi or nemotron (vision).
    """
    ep = ENDPOINTS[endpoint]
    kwargs = {**ep.DEFAULTS, **kwargs}
    kwargs.setdefault("model", ep.MODEL)
    t0 = time.perf_counter()
    error = None
    resp = None
    try:
        resp = client(endpoint).chat.completions.create(messages=messages, **kwargs)
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
            "endpoint": ep.NAME,
            "prompt_tokens": p,
            "completion_tokens": c,
            "reasoning_tokens": getattr(usage, "reasoning_tokens", None),
            "has_image": has_image,
            "latency_s": round(time.perf_counter() - t0, 3),
            "cost_usd": round((p * getattr(ep, "PRICE_IN", 0) + c * getattr(ep, "PRICE_OUT", 0)) / 1e6, 6),
            "error": error,
            **(context or {}),
        }
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def usage_of(resp, endpoint: str) -> dict:
    """Token counts and USD cost of one response, at that endpoint's prices."""
    ep = ENDPOINTS[endpoint]
    u = resp.usage
    p, c = (u.prompt_tokens or 0, u.completion_tokens or 0) if u else (0, 0)
    cost = (p * getattr(ep, "PRICE_IN", 0) + c * getattr(ep, "PRICE_OUT", 0)) / 1e6
    return {"prompt_tokens": p, "completion_tokens": c, "cost_usd": cost}


def text_of(resp) -> str:
    """Some responses put the text in reasoning_content instead of content."""
    m = resp.choices[0].message
    return m.content or getattr(m, "reasoning_content", None) or ""
