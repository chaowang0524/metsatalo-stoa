"""DeepSeek 4.1 Flash on the shared Verda Inference API.

Cheap model for routing and composing answers. It does read images sent as
image_url data URIs, even though usage reports only a few hundred prompt tokens
for them. The Verda gateway has no /v1/files, so DeepSeek's own file_id image
input is not available. Smoke test: uv run python -m endpoints.verda.deepseek
"""

import os

NAME = "deepseek"
BASE_URL_ENV = "VERDA_INFERENCE_BASE_URL"
API_KEY_ENV = "VERDA_INFERENCE_API_KEY"
MODEL = os.environ.get("VERDA_MODEL", "deepseek-4-1-flash-1017")
VISION = True
# USD per million tokens, for cost_usd in the call log.
PRICE_IN = 0.15
PRICE_OUT = 0.6
DEFAULTS: dict = {}

if __name__ == "__main__":
    from stoa import verda

    resp = verda.chat(
        "smoke test", [{"role": "user", "content": "What is the best viewpoint in Helsinki?"}],
        endpoint=NAME, max_tokens=2048,
    )
    print(verda.text_of(resp))
    print(resp.usage)
