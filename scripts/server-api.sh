#!/bin/bash
# 服务器端启动 API 服务（仅监听本机 127.0.0.1:18001，经 SSH 隧道访问）
set -e
ENV_NAME="${ENV_NAME:-videogen}"
cd "$(dirname "$0")/../vendor/Pixelle-Video"
exec conda run --no-capture-output -n "$ENV_NAME" \
    python api/app.py --host 127.0.0.1 --port 18001 "$@"
