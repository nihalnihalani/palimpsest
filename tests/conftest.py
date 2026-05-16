"""Test session bootstrap.

`config.py` requires `GEMINI_API_KEY` at module-import time. CI / local runs
without a `.env` would otherwise fail to even collect tests, even though no
test makes a real Gemini call. We inject a dummy value here so collection
succeeds; per-test code is responsible for mocking out the actual network
calls.
"""
from __future__ import annotations

import os

os.environ.setdefault("GEMINI_API_KEY", "test-dummy-key")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")
