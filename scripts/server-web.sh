#!/bin/bash
# 服务器端启动 Web UI（仅监听本机 127.0.0.1:17861，经 SSH 隧道访问）
set -e
ENV_NAME="${ENV_NAME:-videogen}"
# Playwright 浏览器装在 /data（与 setup_conda.sh 一致）
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/data/pw-browsers}"
cd "$(dirname "$0")/../vendor/Pixelle-Video"
exec conda run --no-capture-output -n "$ENV_NAME" \
    streamlit run web/app.py --server.address 127.0.0.1 --server.port 17861 "$@"
