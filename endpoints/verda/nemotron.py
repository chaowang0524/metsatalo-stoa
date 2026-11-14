"""Nemotron 3.5 Super VL on its own Verda container endpoint.

The vision model: the only one of ours that actually sees images. Thinking is
off by default; with it on, 3000 tokens went to reasoning and no answer came
back (VERDA_LOG.md). 1M context, 32k output, supports tools.
Smoke test: uv run python -m endpoints.verda.nemotron [image]
"""

import os

NAME = "nemotron"
BASE_URL = os.environ.get("NEMOTRON_BASE_URL", "https://containers.datacrunch.io/nemotron-3-5-super-vl-b300/v1")
API_KEY_ENV = "NEMOTRON_API_KEY"
MODEL = os.environ.get("NEMOTRON_MODEL", "nemotron-3.5-super-vl")
VISION = True
DEFAULTS: dict = {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}

if __name__ == "__main__":
    import base64
    import mimetypes
    import sys

    from stoa import verda

    content: list[dict] = [{"type": "text", "text": "What is the best viewpoint in Helsinki?"}]
    if len(sys.argv) > 1:
        mime = mimetypes.guess_type(sys.argv[1])[0] or "image/png"
        b64 = base64.b64encode(open(sys.argv[1], "rb").read()).decode()
        content = [
            {"type": "text", "text": "Describe this drawing in two sentences."},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ]
    resp = verda.chat("smoke test", [{"role": "user", "content": content}], endpoint=NAME, max_tokens=1024)
    print(verda.text_of(resp))
    print(resp.usage)
