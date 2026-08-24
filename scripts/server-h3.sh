#!/bin/bash
# 启动 MiniMax-H3 runtime（独立进程，仅监听 127.0.0.1，不直接暴露公网）
#
# 默认配置：H3_LOWVRAM=1 H3_TE_PRUNE=1 H3_TE_DEVICE=cuda:0 H3_VIDEO_VAE_FP16=1
# H3_KEEP_TRANSFORMER=1 H3_ATTN_BACKEND=default —— 单卡（cuda:0）同时放
# transformer + 文本编码器，2026-08-24 在双卡 48GB（RTX 6000 Ada）机器上
# 实测验证：峰值显存 41.58GB，48GB 卡内完全够用。
#
# 上游 README 的 "48GB Recommended" profile 用的是 H3_TE_DEVICE=cuda:1
# （文本编码器放第二张卡，两张卡各自独立），这在"双卡都基本空闲"的前提下
# 更快；但共享/多用户机器上第二张卡经常被别人占用，与其每次都要排查
# cuda:1 上还有多少剩余显存，不如默认就用已验证稳定可行的单卡方案。
# 如果你的机器双卡确实都空闲、想要 cuda:1 那种独立布局，用环境变量覆盖：
#   H3_TE_DEVICE=cuda:1 ./scripts/server-h3.sh
#
# 所有值都可以在调用时用环境变量覆盖，例如：
#   H3_LOWVRAM=0 ./scripts/server-h3.sh
#   H3_PORT=18612 ./scripts/server-h3.sh
#
# 如果 scripts/h3.env 存在会先加载它（可持久化自定义配置，不提交到 git）。
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
H3_DIR="$ROOT/vendor/Diffusers_minimax-h3"
ENV_NAME="${H3_ENV_NAME:-videogen-h3}"

[ -f "$ROOT/scripts/h3.env" ] && source "$ROOT/scripts/h3.env"

# --- 显存/推理配置（单卡 48GB 默认值，均可覆盖，见上方说明）---
: "${H3_LOWVRAM:=1}"
: "${H3_TE_PRUNE:=1}"
: "${H3_TE_DEVICE:=cuda:0}"
: "${H3_VIDEO_VAE_FP16:=1}"
: "${H3_KEEP_TRANSFORMER:=1}"
: "${H3_ATTN_BACKEND:=default}"
export H3_LOWVRAM H3_TE_PRUNE H3_TE_DEVICE H3_VIDEO_VAE_FP16 H3_KEEP_TRANSFORMER H3_ATTN_BACKEND

# --- CUDA 运行时 ---
: "${CUDA_VISIBLE_DEVICES:=0,1}"
: "${PYTORCH_CUDA_ALLOC_CONF:=expandable_segments:True}"
export CUDA_VISIBLE_DEVICES PYTORCH_CUDA_ALLOC_CONF

# --- 缓存/输出目录（统一放 /data）---
: "${HF_HOME:=/data/hf-cache}"
export HF_HOME

# --- 监听地址：只本机，经 SSH 隧道访问 ---
: "${H3_HOST:=127.0.0.1}"
: "${H3_PORT:=18611}"

echo "MiniMax-H3 backend"
if command -v nvidia-smi &>/dev/null; then
    nvidia-smi --query-gpu=index,name,memory.used,memory.total --format=csv,noheader \
        | awk -F', ' '{printf "GPU%s: %s (%s / %s)\n", $1, $2, $3, $4}'
fi
echo "Endpoint: http://${H3_HOST}:${H3_PORT}"
echo "Configuration:"
echo "  H3_LOWVRAM=$H3_LOWVRAM H3_TE_PRUNE=$H3_TE_PRUNE H3_TE_DEVICE=$H3_TE_DEVICE"
echo "  H3_VIDEO_VAE_FP16=$H3_VIDEO_VAE_FP16 H3_KEEP_TRANSFORMER=$H3_KEEP_TRANSFORMER"
echo "  H3_ATTN_BACKEND=$H3_ATTN_BACKEND CUDA_VISIBLE_DEVICES=$CUDA_VISIBLE_DEVICES"
echo "  HF_HOME=$HF_HOME"
echo ""

cd "$H3_DIR"
exec conda run --no-capture-output -n "$ENV_NAME" \
    python -m uvicorn app:app --host "$H3_HOST" --port "$H3_PORT"
