"""Shared inference wrapper for Verda and the Gemini Developer API.

Every call appends one line to logs/verda_calls.jsonl (see
VERDA_ENDPOINT_GUIDELINES.md): purpose, model, tokens, latency. That log is our
cost evidence for Stoa and our process evidence for the Verda bounty.
"""

import json
import os
import math
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI

LOG_PATH = Path(os.environ.get("VERDA_LOG", "logs/verda_calls.jsonl"))


# The funded Gemini key lives in .env; an old, unfunded GEMINI_API_KEY exported in
# a shell profile used to win and return 402. For these, .env takes precedence.
DOTENV_WINS = {"GEMINI_API_KEY", "Gemini_API_KEY"}


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Fill os.environ from a gitignored .env file, without overriding, except DOTENV_WINS."""
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            k, sep, v = line.strip().partition("=")
            if sep and not k.startswith("#"):
                k, v = k.strip(), v.strip().strip("'\"")
                if k in DOTENV_WINS:
                    os.environ[k] = v
                else:
                    os.environ.setdefault(k, v)


_load_dotenv()

from endpoints.verda import deepseek, kimi, nemotron  # noqa: E402
from endpoints import gemini  # noqa: E402

# One module per endpoint in endpoints/verda/, each with its USD per million
# token prices as PRICE_IN / PRICE_OUT (missing means unknown, logged as 0).
# "inference" and "vision" are the old names, kept so existing callers don't break.
ENDPOINTS = {m.NAME: m for m in (deepseek, kimi, nemotron, gemini)}
ENDPOINTS["inference"] = deepseek
ENDPOINTS["vision"] = nemotron

_clients: dict[str, OpenAI] = {}
_RATE_LOCK = threading.Lock()
_RATE_UNTIL = 0.0


def _wait_for_rate_limit() -> None:
    while True:
        with _RATE_LOCK:
            delay = _RATE_UNTIL - time.monotonic()
        if delay <= 0:
            return
        time.sleep(min(delay, 30))


def _backoff(error, retry: int) -> None:
    global _RATE_UNTIL
    delay = 15 * (retry + 1)
    headers = getattr(getattr(error, "response", None), "headers", {})
    try:
        requested = float(headers.get("retry-after", 0))
        if math.isfinite(requested):
            delay = max(delay, min(requested, 120))
    except (TypeError, ValueError):
        pass
    with _RATE_LOCK:
        _RATE_UNTIL = max(_RATE_UNTIL, time.monotonic() + delay)


def client(endpoint: str = "deepseek") -> OpenAI:
    ep = ENDPOINTS[endpoint]
    if ep.NAME not in _clients:
        base = getattr(ep, "BASE_URL", None) or os.environ[ep.BASE_URL_ENV]
        key_names = (ep.API_KEY_ENV, *getattr(ep, "API_KEY_ENV_ALIASES", ()))
        key = next((os.environ[name] for name in key_names if os.environ.get(name)), None)
        if not key:
            raise ValueError(f"Set {ep.API_KEY_ENV} in .env for endpoint {ep.NAME}")
        _clients[ep.NAME] = OpenAI(base_url=base, api_key=key)
    return _clients[ep.NAME]


def chat(purpose: str, messages: list[dict], context: dict | None = None, endpoint: str = "deepseek", **kwargs):
    """chat.completions.create plus a log line. `purpose` says why we called.

    `endpoint` is a name in ENDPOINTS: deepseek, kimi, nemotron or gemini.
    """
    ep = ENDPOINTS[endpoint]
    kwargs = {**ep.DEFAULTS, **kwargs}
    kwargs.setdefault("model", ep.MODEL)
    t0 = time.perf_counter()
    error = None
    resp = None
    retries = 0
    is_verda = getattr(ep, "PROVIDER", "verda") == "verda"
    try:
        while True:
            if is_verda:
                _wait_for_rate_limit()
            try:
                resp = client(endpoint).chat.completions.create(messages=messages, **kwargs)
                return resp
            except Exception as exc:
                # The SDK already retries short failures. Add at most two longer,
                # shared cooldowns for the Verda tenant RPM limit.
                if is_verda and getattr(exc, "status_code", None) == 429 and retries < 2:
                    _backoff(exc, retries)
                    retries += 1
                    continue
                raise
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        raise
    finally:
        usage = getattr(resp, "usage", None)
        measured = _usage_values(usage, ep)
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
            "provider": getattr(ep, "PROVIDER", "verda"),
            **measured,
            "reasoning_tokens": getattr(usage, "reasoning_tokens", None),
            "has_image": has_image,
            "latency_s": round(time.perf_counter() - t0, 3),
            "cost_usd": round(measured["cost_usd"], 6),
            "error": error,
            "rate_limit_retries": retries,
            **(context or {}),
        }
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _usage_values(u, ep) -> dict:
    p = getattr(u, "prompt_tokens", 0) or 0
    c = getattr(u, "completion_tokens", 0) or 0
    extra = {}
    if getattr(ep, "PROVIDER", None) == "google":
        # Google's compatibility response can omit thinking from completion_tokens
        # while including it in total_tokens. Preserve the raw count and account
        # for the remaining output once, without double-counting inclusive totals.
        reported = c
        total = getattr(u, "total_tokens", None)
        if isinstance(total, int):
            c = max(c, total - p)
        extra = {"reported_completion_tokens": reported,
                 "unattributed_output_tokens": c - reported, "total_tokens": total}
    cost = (p * getattr(ep, "PRICE_IN", 0) + c * getattr(ep, "PRICE_OUT", 0)) / 1e6
    return {"prompt_tokens": p, "completion_tokens": c, "cost_usd": cost, **extra}


def usage_of(resp, endpoint: str) -> dict:
    """Token counts and USD cost of one response, including Google output gaps."""
    return _usage_values(resp.usage, ENDPOINTS[endpoint])


def text_of(resp) -> str:
    """Some responses put the text in reasoning_content instead of content."""
    m = resp.choices[0].message
    return m.content or getattr(m, "reasoning_content", None) or ""
