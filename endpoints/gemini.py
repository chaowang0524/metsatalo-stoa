"""Gemini Developer API via Google's OpenAI-compatible endpoint.

Docs: https://aistudio.google.com/docs/openai
Prices: https://aistudio.google.com/docs/pricing#gemini-4-hydrogen
Standard paid-tier USD / million tokens, checked 2026-11-14; estimates do not
apply free-tier allowances or implicit-cache discounts.
"""

import os

NAME = "gemini"
PROVIDER = "google"
BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
API_KEY_ENV = "GEMINI_API_KEY"
API_KEY_ENV_ALIASES = ("Gemini_API_KEY",)
MODEL = os.environ.get("GEMINI_MODEL", "gemini-4-hydrogen")
VISION = True
PRICE_IN = 1.50
PRICE_OUT = 7.50
DEFAULTS: dict = {"reasoning_effort": "low", "max_tokens": 4096}
