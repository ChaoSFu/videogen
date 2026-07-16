#!/bin/bash
# 启动本地 ComfyUI（仅监听 127.0.0.1:8188，供同机的 Pixelle-Video 调用）
#
# 单卡 A100 80G 与 Ollama 共卡：--reserve-vram 给 Ollama 等其他进程
# 预留显存（GB），ComfyUI 缓存模型时不会占满整卡。
# 默认 24GB 对应 qwen3:32b（~20GB）+ 上下文开销。
set -e
COMFY_DIR="${COMFY_DIR:-$HOME/ComfyUI}"
ENV_NAME="${COMFY_ENV_NAME:-comfyui}"
RESERVE_VRAM_GB="${RESERVE_VRAM_GB:-24}"
cd "$COMFY_DIR"
exec conda run --no-capture-output -n "$ENV_NAME" \
    python main.py --listen 127.0.0.1 --port 8188 \
    --reserve-vram "$RESERVE_VRAM_GB" "$@"
