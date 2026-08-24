#!/bin/bash
# 下载 MiniMax-H3 模型权重（T2VA/FL2VA 必需部分，约 144GB）
#
# 与 setup_h3.sh 分开：环境搭建不应该静默触发一次 144GB 下载，这一步必须
# 由你显式执行。直接调用上游自带的 scripts/download_t2va.py（不重新实现
# 下载逻辑），它用 huggingface_hub.snapshot_download 按需拉取子目录
# （modular_model_index.json、text_encoder/、transformer/、vae/、
# audio_vae/、scheduler/、audio_scheduler/、tokenizer/、processor/），
# 不是仓库全量的 498GB。
#
# 用法: bash scripts/download_h3.sh
#       H3_DOWNLOAD_YES=1 bash scripts/download_h3.sh   # 跳过磁盘确认提示
# 可覆盖: H3_ENV_NAME, HF_HOME, HF_TOKEN（若仓库需要鉴权）
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
H3_DIR="$ROOT/vendor/Diffusers_minimax-h3"
ENV_NAME="${H3_ENV_NAME:-videogen-h3}"

# 如果 scripts/h3.env 存在会先加载它（跟 server-h3.sh 一致），例如把
# HF_HOME 固定指到没有 /data 的机器上的路径，不用每次手动传。h3.env
# 里如果写的是 export FOO=xxx（不是 : "${FOO:=xxx}" 那种条件赋值），
# 它会覆盖同名的命令行环境变量——跟 server-h3.sh 的行为一致。
[ -f "$ROOT/scripts/h3.env" ] && source "$ROOT/scripts/h3.env"

export HF_HOME="${HF_HOME:-/data/hf-cache}"

ESTIMATED_GB=144

fail() { echo "❌ $1"; exit 1; }

[ -f "$H3_DIR/scripts/download_t2va.py" ] || fail \
    "找不到 $H3_DIR/scripts/download_t2va.py，先跑 bash scripts/setup_h3.sh 拉取子模块"

conda env list | grep -qE "^${ENV_NAME}[[:space:]]" || fail \
    "conda 环境 ${ENV_NAME} 不存在，先跑 bash scripts/setup_h3.sh"

mkdir -p "$HF_HOME" 2>/dev/null || {
    sudo mkdir -p "$HF_HOME" && sudo chown "$USER" "$HF_HOME"
}

# 磁盘空间检查（对 HF_HOME 所在文件系统）
AVAIL_GB=$(df -Pk "$HF_HOME" | awk 'NR==2 {printf "%d", $4/1024/1024}')
echo "📦 预计下载大小: ~${ESTIMATED_GB}GB（T2VA/FL2VA 必需部分，仓库总量约 498GB，此处仅按需子集）"
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

if [ -n "$HF_TOKEN" ]; then
    echo "🔑 使用 HF_TOKEN 鉴权"
fi

# snapshot_download 对已完整下载的文件会自动跳过（校验本地缓存），
# 中断后重跑本脚本即可续传，不会重新下载已有部分。
ATTEMPTS="${H3_DOWNLOAD_RETRIES:-5}"
for i in $(seq 1 "$ATTEMPTS"); do
    echo "⬇️  下载尝试 $i/$ATTEMPTS ..."
    if (cd "$H3_DIR" && conda run --no-capture-output -n "$ENV_NAME" \
            python scripts/download_t2va.py); then
        echo ""
        echo "✅ 模型下载完成（HF_HOME=$HF_HOME）"
        echo "   启动: ./scripts/server-h3.sh"
        exit 0
    fi
    echo "⚠️  第 $i 次尝试失败，$([ "$i" -lt "$ATTEMPTS" ] && echo 重试 || echo 放弃)..."
    sleep 5
done

fail "下载多次失败，请检查网络/磁盘/HF_TOKEN 后重跑本脚本（已下载部分不会丢失）"
