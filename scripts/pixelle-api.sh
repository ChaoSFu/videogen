#!/bin/bash
# 启动 Pixelle-Video API 服务（FastAPI，默认 http://localhost:8000）
set -e
cd "$(dirname "$0")/../vendor/Pixelle-Video"
exec uv run python api/app.py "$@"
