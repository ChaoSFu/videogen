#!/bin/bash
# 启动 videogen 统一 API（独立进程，仅监听 127.0.0.1，经 SSH 隧道访问）
#
# 只做 HTTP 转发/编排，不加载任何模型 —— 用主 videogen conda 环境即可
# （fastapi/uvicorn/httpx，纯 Python，与 H3 的 CUDA 依赖完全隔离）。
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_NAME="${ENV_NAME:-videogen}"

: "${VIDEOGEN_HOST:=127.0.0.1}"
: "${VIDEOGEN_PORT:=18010}"
: "${H3_BASE_URL:=http://127.0.0.1:18611}"
export VIDEOGEN_HOST VIDEOGEN_PORT H3_BASE_URL

echo "videogen unified API"
echo "Endpoint: http://${VIDEOGEN_HOST}:${VIDEOGEN_PORT}  (docs: /docs)"
echo "Backends:"
echo "  minimax-h3 -> ${H3_BASE_URL}"
echo ""

cd "$ROOT"
exec conda run --no-capture-output -n "$ENV_NAME" \
    python -m uvicorn videogen.api:app --host "$VIDEOGEN_HOST" --port "$VIDEOGEN_PORT"
