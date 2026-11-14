"""Kimi K3.1 Pro on the shared Verda Inference API.

Larger text model on its own API key (VERDA_KIMI), for questions deepseek gets wrong.
Text only. Smoke test: uv run python -m endpoints.verda.kimi
"""

import os

NAME = "kimi"
BASE_URL_ENV = "VERDA_INFERENCE_BASE_URL"
API_KEY_ENV = "VERDA_KIMI"
MODEL = os.environ.get("KIMI_MODEL", "kimi-k3-1-pro")
VISION = False
# USD per million tokens, for cost_usd in the call log. Reasoning tokens bill as output.
PRICE_IN = 2.0
PRICE_OUT = 10.0
# Reasoning off: it is on by default and spent up to 12k tokens per drawing.
DEFAULTS: dict = {"extra_body": {"thinking": {"type": "disabled"}}}

if __name__ == "__main__":
    from stoa import verda

    resp = verda.chat(
        "smoke test", [{"role": "user", "content": "What is the best viewpoint in Helsinki?"}],
        endpoint=NAME, max_tokens=2048,
    )
    print(verda.text_of(resp))
    print(resp.usage)
