#!/bin/bash
# 部署 MiniMax-H3 独立 runtime 环境（vendor/Diffusers_minimax-h3）
#
# 与主 videogen 环境完全隔离：H3 的 diffusers 固定到特定 commit、torch
# 固定 cu128、Python 需要 3.12，全部装进独立的 conda 环境
# ${H3_ENV_NAME:-videogen-h3}，不污染主 videogen / Pixelle-Video 环境。
#
# 只装环境和依赖，不下载模型权重（模型下载见 scripts/download_h3.sh，
# 由你显式执行 —— 144GB 级下载不应该在 setup 过程中静默触发）。
#
# 用法: bash scripts/setup_h3.sh
# 可覆盖的环境变量:
#   H3_ENV_NAME=videogen-h3     conda 环境名
#   HF_HOME=/data/hf-cache      HuggingFace 缓存目录
#   H3_OUTPUT_DIR=/data/videogen-output/minimax-h3   生成产物落地目录
#   PIP_INDEX_URL=...           普通依赖走的 pip 源（默认清华镜像；torch
#                                固定走官方 cu128 索引，不受此变量影响）
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
H3_DIR="$ROOT/vendor/Diffusers_minimax-h3"
ENV_NAME="${H3_ENV_NAME:-videogen-h3}"

# 如果 scripts/h3.env 存在会先加载它（跟 server-h3.sh 等一致），例如把
# HF_HOME/H3_OUTPUT_DIR/TMPDIR 固定指到没有 /data 的机器上的路径。
[ -f "$ROOT/scripts/h3.env" ] && source "$ROOT/scripts/h3.env"

HF_HOME="${HF_HOME:-/data/hf-cache}"
H3_OUTPUT_DIR="${H3_OUTPUT_DIR:-/data/videogen-output/minimax-h3}"

export PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-/data/pip-cache}"
export TMPDIR="${TMPDIR:-/data/tmp}"

fail() { echo "❌ $*"; exit 1; }

# 建目录：直接建 -> 建不了就 sudo 建并 chown 给当前用户（会按需提示输入密码）
# -> 两者都不行就明确报错退出（而不是在后面某个 mkdir 处莫名其妙地炸）。
ensure_dir() {
    local dir="$1"
    [ -d "$dir" ] && return 0
    mkdir -p "$dir" 2>/dev/null && return 0
    if command -v sudo &>/dev/null && sudo mkdir -p "$dir" 2>/dev/null; then
        sudo chown "$USER" "$dir" && return 0
    fi
    fail "无法创建 $dir（无写权限，sudo 也不可用）。请用环境变量把相关路径指到你有权限的目录，例如: " \
        "HF_HOME=\$HOME/hf-cache H3_OUTPUT_DIR=\$HOME/videogen-output/minimax-h3 TMPDIR=\$HOME/tmp bash scripts/setup_h3.sh"
}

ensure_dir "$TMPDIR"
ensure_dir "$PIP_CACHE_DIR"

# 1-2. GPU 检查（不足两张只警告，不阻断——环境搭建本身不需要 GPU）
echo "🖥  GPU 检查..."
if ! command -v nvidia-smi &>/dev/null; then
    fail "未找到 nvidia-smi，请先确认 NVIDIA 驱动已安装"
fi
nvidia-smi --query-gpu=index,name,memory.total,memory.used --format=csv || fail "nvidia-smi 执行失败"
GPU_COUNT=$(nvidia-smi --query-gpu=index --format=csv,noheader | wc -l | tr -d ' ')
echo "检测到 GPU 数量: $GPU_COUNT"
if [ "$GPU_COUNT" -lt 2 ]; then
    echo "⚠️  少于 2 张 GPU：README 推荐的双卡配置（H3_TE_DEVICE=cuda:1 等）需要至少两张卡。"
    echo "   单卡也能跑，需要调整 scripts/server-h3.sh 里的 H3_TE_DEVICE 等环境变量。"
fi

# 3. conda 检查
command -v conda &>/dev/null || fail "未找到 conda，请先安装 Miniconda"

# 4. 初始化 H3 submodule
if [ ! -f "$H3_DIR/app.py" ]; then
    echo "📥 拉取 Diffusers_minimax-h3 子模块..."
    git -C "$ROOT" submodule update --init -- vendor/Diffusers_minimax-h3 \
        || fail "子模块拉取失败"
fi

# 5. 创建独立 conda 环境（上游要求 Python 3.12，与主环境的 3.11 不同）
create_env() {
    echo "🐍 创建 conda 环境 ${ENV_NAME}（python 3.12）..."
    conda create -n "$ENV_NAME" --override-channels -c conda-forge python=3.12 pip -y \
        || fail "conda 环境创建失败"
}
env_python_ok() {
    conda run -n "$ENV_NAME" python -c 'import sys; sys.exit(0 if sys.version_info[:2]==(3,12) else 1)' 2>/dev/null
}
if conda env list | grep -qE "^${ENV_NAME}[[:space:]]"; then
    if env_python_ok; then
        echo "✅ conda 环境 ${ENV_NAME} 已存在（Python 3.12），跳过创建"
    else
        echo "♻️  环境 ${ENV_NAME} 已存在但 Python 版本不是 3.12，删除重建..."
        conda remove -n "$ENV_NAME" --all -y
        create_env
    fi
else
    create_env
fi

run_pip() { conda run --no-capture-output -n "$ENV_NAME" python -m pip "$@"; }

# 6. 安装依赖 —— 版本/commit 与 vendor/Diffusers_minimax-h3 当前 README 的
#    「pip install」代码块一致（2026-08 抓取，若上游更新请对照子模块自己的
#    README 调整此脚本，不要凭旧假设改）。
echo "📦 [1/6] torch==2.9.0 (cu128) ..."
run_pip install "torch==2.9.0" --index-url https://download.pytorch.org/whl/cu128 \
    || fail "torch 安装失败"

echo "📦 [2/6] diffusers（固定 commit f37ab93e，PR #14355 合并版）..."
run_pip install \
    "git+https://github.com/huggingface/diffusers.git@f37ab93e621d5ce206c9662e8291ca8b67d9c555" \
    || fail "diffusers 安装失败"

echo "📦 [3/6] transformers / accelerate / safetensors / huggingface_hub ..."
run_pip install "transformers==5.14.1" "accelerate==1.12.0" safetensors "huggingface_hub[cli]" \
    || fail "transformers 等安装失败"

echo "📦 [4/6] av / fastapi / uvicorn / python-multipart / pillow / numpy / torchvision ..."
run_pip install "av==16.0.1" "fastapi==0.104.1" "uvicorn==0.24.0" python-multipart pillow numpy \
    || fail "FastAPI 运行时依赖安装失败"
# torchvision 未出现在上游 README 的 pip 代码块里，但 transformers 的
# Qwen3VLVideoProcessor（processor 组件）实际依赖它，缺了会导致 processor
# 组件静默失败（diffusers 的 ModularPipeline 只打印警告、不抛错），后续
# t2va 走到 processor.create_mm_token_type_ids() 时才炸 —— 实测验证过，
# T2VA 必需，不是可选项。必须跟 torch 用同一个 cu128 索引装（不能走
# PIP_INDEX_URL 那个普通镜像，索引不一致 pip 会长时间 backtrack 卡住）。
run_pip install torchvision --index-url https://download.pytorch.org/whl/cu128 \
    || fail "torchvision 安装失败（processor 组件依赖它，t2va 无法正常工作）"

echo "📦 [5/6] bitsandbytes（默认 TE 量化 bnb-4bit 依赖，必需）..."
run_pip install "bitsandbytes==0.49.0" || fail "bitsandbytes 安装失败"

echo "📦 [6/6] torchao（int8 量化，可选，失败不阻断）..."
run_pip install "torchao==0.17.0" || echo "⚠️  torchao 安装失败，跳过（仅影响可选的 int8 量化路径）"

# 7. HuggingFace 缓存目录 + 生成产物目录（统一放 /data）
ensure_dir "$HF_HOME"
ensure_dir "$H3_OUTPUT_DIR"

# 上游 outputs/ 路径是硬编码在 app.py 里的（BASE_DIR / "outputs"），不可配置，
# 最小侵入方案：软链到 /data，不改 vendor 代码。
OUT_LINK="$H3_DIR/outputs"
if [ -d "$OUT_LINK" ] && [ ! -L "$OUT_LINK" ]; then
    find "$OUT_LINK" -mindepth 1 -maxdepth 1 -exec mv -t "$H3_OUTPUT_DIR" {} + 2>/dev/null
    rmdir "$OUT_LINK" 2>/dev/null
fi
if [ ! -e "$OUT_LINK" ]; then
    ln -s "$H3_OUTPUT_DIR" "$OUT_LINK"
    echo "📁 生成产物目录: $OUT_LINK -> $H3_OUTPUT_DIR"
fi

echo ""
echo "✅ H3 runtime 环境配置完成（模型权重尚未下载）。下一步："
echo "   HF_HOME=$HF_HOME bash scripts/download_h3.sh    # 下载模型（约 144GB，请显式执行）"
echo "   ./scripts/server-h3.sh                           # 启动 H3 runtime（127.0.0.1:18611）"
