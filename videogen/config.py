"""Environment-variable configuration for the unified videogen API.

Deliberately not a config framework (no pydantic-settings, no .env
loader) — plain env vars read once at import time, matching every other
script in this repo. GPU/runtime tuning for the H3 runtime itself lives in
scripts/server-h3.sh, not here; this module only configures how the
unified API process talks to that runtime.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    return float(value) if value else default


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value else default


VIDEOGEN_HOST = os.environ.get("VIDEOGEN_HOST", "127.0.0.1")
VIDEOGEN_PORT = _env_int("VIDEOGEN_PORT", 18010)

H3_BASE_URL = os.environ.get("H3_BASE_URL", "http://127.0.0.1:18611")
# Video generation can run for minutes; health checks must not wait that long.
H3_REQUEST_TIMEOUT = _env_float("H3_REQUEST_TIMEOUT", 1800.0)
H3_HEALTH_TIMEOUT = _env_float("H3_HEALTH_TIMEOUT", 10.0)

# 生成历史（JSONL 文件，非数据库）；run/ 已在 .gitignore 中忽略
VIDEOGEN_HISTORY_FILE = Path(
    os.environ.get("VIDEOGEN_HISTORY_FILE", str(REPO_ROOT / "run" / "history.jsonl"))
)
