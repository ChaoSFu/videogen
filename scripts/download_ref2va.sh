#!/bin/bash
# 下载 Ref2VA 专用的 transformer_ref 组件（约 66GB）
#
# T2VA/FL2VA 不需要这个（download_h3.sh 已经够用）；只有第一次用 Ref2VA
# 时才需要。不提前下的话，第一次 Ref2VA 请求会在处理过程中现场触发这个
# 下载——H3 的 /api/progress 会显示 phase=loading_transformer，GPU 显存
# 却几乎不涨，看起来像卡住了，其实是在后台拉文件（实测遇到过，误以为是
# bug，其实是没预下载这个组件）。跟 download_h3.sh 一样单独一步、不在
# setup 阶段静默触发，因为这也是大几十 GB 级的下载。
#
# 用法: bash scripts/download_ref2va.sh
#       H3_DOWNLOAD_YES=1 bash scripts/download_ref2va.sh   # 跳过确认提示
# 可覆盖: H3_ENV_NAME, HF_HOME, HF_TOKEN
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
H3_DIR="$ROOT/vendor/Diffusers_minimax-h3"
ENV_NAME="${H3_ENV_NAME:-videogen-h3}"
export HF_HOME="${HF_HOME:-/data/hf-cache}"

ESTIMATED_GB=67

fail() { echo "❌ $1"; exit 1; }

conda env list | grep -qE "^${ENV_NAME}[[:space:]]" || fail \
    "conda 环境 ${ENV_NAME} 不存在，先跑 bash scripts/setup_h3.sh"

mkdir -p "$HF_HOME" 2>/dev/null || {
    sudo mkdir -p "$HF_HOME" && sudo chown "$USER" "$HF_HOME"
}

AVAIL_GB=$(df -Pk "$HF_HOME" | awk 'NR==2 {printf "%d", $4/1024/1024}')
echo "📦 预计下载大小: ~${ESTIMATED_GB}GB（仅 transformer_ref/ 组件，Ref2VA 专用）"
echo "💾 $HF_HOME 所在盘剩余空间: ${AVAIL_GB}GB"
if [ "$AVAIL_GB" -lt "$ESTIMATED_GB" ]; then
    echo "⚠️  剩余空间可能不够（建议 ${ESTIMATED_GB}GB + 富余）"
fi
if [ -z "$H3_DOWNLOAD_YES" ]; then
    read -r -p "继续下载？[y/N] " reply
    case "$reply" in
        [yY]*) ;;
        *) echo "已取消"; exit 0 ;;
    esac
fi

ATTEMPTS="${H3_DOWNLOAD_RETRIES:-5}"
for i in $(seq 1 "$ATTEMPTS"); do
    echo "⬇️  下载尝试 $i/$ATTEMPTS ..."
    if (cd "$H3_DIR" && conda run --no-capture-output -n "$ENV_NAME" python -c "
from huggingface_hub import snapshot_download
path = snapshot_download('MiniMaxAI/MiniMax-H3', allow_patterns=['transformer_ref/*'])
print(f'DONE -> {path}')
"); then
        echo ""
        echo "✅ transformer_ref 下载完成（HF_HOME=$HF_HOME），Ref2VA 首次请求不会再卡在下载上"
        exit 0
    fi
    echo "⚠️  第 $i 次尝试失败，$([ "$i" -lt "$ATTEMPTS" ] && echo 重试 || echo 放弃)..."
    sleep 5
done

fail "下载多次失败，请检查网络/磁盘/HF_TOKEN 后重跑本脚本（已下载部分不会丢失）"
