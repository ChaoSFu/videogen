#!/bin/bash
# 启动本地 ComfyUI（仅监听 127.0.0.1:8188，供同机的 Pixelle-Video 调用）
set -e
COMFY_DIR="${COMFY_DIR:-$HOME/ComfyUI}"
ENV_NAME="${COMFY_ENV_NAME:-comfyui}"
cd "$COMFY_DIR"
exec conda run --no-capture-output -n "$ENV_NAME" \
    python main.py --listen 127.0.0.1 --port 8188 "$@"
