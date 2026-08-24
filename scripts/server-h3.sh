#!/bin/bash
# 启动 MiniMax-H3 runtime（独立进程，仅监听 127.0.0.1，不直接暴露公网）
#
# 默认配置是上游 README 里针对 "48GB (RTX PRO 5000) – Recommended" 的
# profile，套用到 2×A6000 48GB：
#   H3_LOWVRAM=1 H3_TE_PRUNE=1 H3_TE_DEVICE=cuda:1 H3_VIDEO_VAE_FP16=1
#   H3_KEEP_TRANSFORMER=1 H3_ATTN_BACKEND=default
# A6000 是 Ampere 架构，H3_ATTN_BACKEND 不使用上游默认的 sage（需要
# sm_120+ 编译的 SageAttention），保持 default（SDPA）。
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

# --- 显存/推理配置（2×A6000 48GB 默认值，均可覆盖）---
: "${H3_LOWVRAM:=1}"
: "${H3_TE_PRUNE:=1}"
: "${H3_TE_DEVICE:=cuda:1}"
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
