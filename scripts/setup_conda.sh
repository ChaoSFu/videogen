#!/bin/bash
# 服务器端一键配置：创建 conda 环境并安装 Pixelle-Video 全部依赖
# 用法: bash scripts/setup_conda.sh          （默认环境名 videogen）
#       ENV_NAME=myenv bash scripts/setup_conda.sh
set -e

ENV_NAME="${ENV_NAME:-videogen}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

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
if conda env list | grep -qE "^${ENV_NAME}[[:space:]]"; then
    echo "✅ conda 环境 ${ENV_NAME} 已存在，跳过创建"
else
    echo "🐍 创建 conda 环境 ${ENV_NAME}（python 3.11 + ffmpeg）..."
    conda create -n "$ENV_NAME" -c conda-forge python=3.11 ffmpeg -y
fi

# 4. 安装 Pixelle-Video 及其全部 Python 依赖
echo "📦 安装 Pixelle-Video 依赖（首次较慢）..."
conda run -n "$ENV_NAME" pip install -e "$ROOT/vendor/Pixelle-Video"

# 5. 安装 Playwright 浏览器（HTML 视频帧渲染需要）
echo "🌐 安装 Playwright chromium..."
if ! conda run -n "$ENV_NAME" playwright install chromium; then
    echo "⚠️  chromium 安装失败，可能缺系统库，可尝试（需要 sudo）："
    echo "   conda run -n $ENV_NAME playwright install --with-deps chromium"
fi

echo ""
echo "✅ 环境配置完成。启动方式（仅监听本机，经 SSH 隧道访问）："
echo "   $ROOT/scripts/server-web.sh   # Web UI  127.0.0.1:7861"
echo "   $ROOT/scripts/server-api.sh   # API 服务 127.0.0.1:8001"
echo ""
echo "   本地建立隧道: ssh -L 7861:localhost:7861 -L 8001:localhost:8001 chao@<服务器IP>"
echo "   然后浏览器打开 http://localhost:7861"
