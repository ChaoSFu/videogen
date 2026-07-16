#!/bin/bash
# 服务器端一键配置：创建 conda 环境并安装 Pixelle-Video 全部依赖
# 用法: bash scripts/setup_conda.sh          （默认环境名 videogen）
#       ENV_NAME=myenv bash scripts/setup_conda.sh
set -e

ENV_NAME="${ENV_NAME:-videogen}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# pip 源：默认清华全量镜像（服务器原有源缺包会导致 ResolutionImpossible），
# 可用 PIP_INDEX_URL 环境变量覆盖
export PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
# pip 下载缓存放 /data，避免撑爆根盘
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-/data/pip-cache}"

# 1. 检查 conda
if ! command -v conda &>/dev/null; then
    echo "❌ 未找到 conda，请先安装 Miniconda："
    echo "   wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh"
    echo "   bash Miniconda3-latest-Linux-x86_64.sh"
    exit 1
fi

# 2. 确认子模块已拉取
if [ ! -f "$ROOT/vendor/Pixelle-Video/pyproject.toml" ]; then
    echo "📥 拉取 Pixelle-Video 子模块..."
    git -C "$ROOT" submodule update --init
fi

# 3. 创建环境（python 3.11 + ffmpeg 一并装进环境，无需系统级安装）
create_env() {
    echo "🐍 创建 conda 环境 ${ENV_NAME}（python 3.11 + ffmpeg）..."
    # --override-channels: 只用 conda-forge，绕开需要接受 ToS 的 Anaconda 官方渠道
    conda create -n "$ENV_NAME" --override-channels -c conda-forge python=3.11 pip ffmpeg -y
}

env_python_ok() {
    conda run -n "$ENV_NAME" python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null
}

if conda env list | grep -qE "^${ENV_NAME}[[:space:]]"; then
    if env_python_ok; then
        echo "✅ conda 环境 ${ENV_NAME} 已存在（Python >= 3.11），跳过创建"
    else
        echo "♻️  环境 ${ENV_NAME} 已存在但 Python 版本不满足 >=3.11，删除重建..."
        conda remove -n "$ENV_NAME" --all -y
        create_env
    fi
else
    create_env
fi

# 4. 确保环境内有 pip（conda-forge 的 python 不自带 pip；
#    缺了会串到系统 pip，装错 Python 版本）
if ! conda run -n "$ENV_NAME" python -m pip --version &>/dev/null; then
    echo "🔧 环境内缺少 pip，安装中..."
    conda install -n "$ENV_NAME" --override-channels -c conda-forge pip -y
fi

# 5. 安装 Pixelle-Video 及其全部 Python 依赖（用 python -m pip 确保装进环境内）
echo "📦 安装 Pixelle-Video 依赖（首次较慢）..."
conda run -n "$ENV_NAME" python -m pip install -e "$ROOT/vendor/Pixelle-Video"

# 6. 安装 Playwright 浏览器（HTML 视频帧渲染需要）
echo "🌐 安装 Playwright chromium..."
if ! conda run -n "$ENV_NAME" python -m playwright install chromium; then
    echo "⚠️  chromium 安装失败，可能缺系统库，可尝试（需要 sudo）："
    echo "   conda run -n $ENV_NAME python -m playwright install --with-deps chromium"
fi

# 7. 生成产物目录放 /data（output/ 软链到 /data/pixelle-output）
PIXELLE_OUTPUT_DIR="${PIXELLE_OUTPUT_DIR:-/data/pixelle-output}"
OUT_LINK="$ROOT/vendor/Pixelle-Video/output"
if [ -d "$(dirname "$PIXELLE_OUTPUT_DIR")" ]; then
    mkdir -p "$PIXELLE_OUTPUT_DIR" 2>/dev/null || {
        sudo mkdir -p "$PIXELLE_OUTPUT_DIR" && sudo chown "$USER" "$PIXELLE_OUTPUT_DIR"
    }
    if [ -d "$OUT_LINK" ] && [ ! -L "$OUT_LINK" ]; then
        # 搬迁全部内容（含隐藏文件）后移除原目录
        find "$OUT_LINK" -mindepth 1 -maxdepth 1 -exec mv -t "$PIXELLE_OUTPUT_DIR" {} +
        rmdir "$OUT_LINK"
    fi
    if [ ! -e "$OUT_LINK" ]; then
        ln -s "$PIXELLE_OUTPUT_DIR" "$OUT_LINK"
        echo "📁 生成产物目录: $OUT_LINK -> $PIXELLE_OUTPUT_DIR"
    fi
fi

echo ""
echo "✅ 环境配置完成。启动方式（仅监听本机，经 SSH 隧道访问）："
echo "   $ROOT/scripts/server-web.sh   # Web UI  127.0.0.1:17861"
echo "   $ROOT/scripts/server-api.sh   # API 服务 127.0.0.1:18001"
echo ""
echo "   本地建立隧道: ssh -L 17861:localhost:17861 -L 18001:localhost:18001 chao@<服务器IP>"
echo "   然后浏览器打开 http://localhost:17861"
