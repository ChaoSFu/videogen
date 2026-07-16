#!/bin/bash
# 部署本地 LLM：Ollama + Qwen（OpenAI 兼容接口，供 Pixelle-Video 文案生成用）
#
# 默认 qwen3:32b（4bit 约 20GB 显存）：单卡 A100 80G 上与 ComfyUI
# 共存的效果/显存最优档。更大的 72B 档会和 Wan2.1 视频生成互相挤爆，不要用。
#
# 用法: bash scripts/setup_ollama.sh
#       LLM_MODEL=qwen2.5:14b bash scripts/setup_ollama.sh   # 换小模型
set -e

LLM_MODEL="${LLM_MODEL:-qwen3:32b}"
# 模型库放 /data（大文件统一放 /data）
OLLAMA_MODELS_DIR="${OLLAMA_MODELS_DIR:-/data/ollama/models}"

# 1. 安装 Ollama（官方脚本，需要 sudo）
if ! command -v ollama &>/dev/null; then
    echo "📥 安装 Ollama（可能需要输入 sudo 密码）..."
    curl -fsSL https://ollama.com/install.sh | sh
fi

# 2. 把模型存储目录指到 /data
sudo mkdir -p "$OLLAMA_MODELS_DIR"
if systemctl list-unit-files 2>/dev/null | grep -q "^ollama.service"; then
    # systemd 方式安装：通过 override 配置 OLLAMA_MODELS
    if ! grep -qs "OLLAMA_MODELS=$OLLAMA_MODELS_DIR" /etc/systemd/system/ollama.service.d/override.conf 2>/dev/null; then
        echo "🔧 配置 Ollama 模型目录为 $OLLAMA_MODELS_DIR ..."
        sudo chown -R ollama:ollama "$(dirname "$OLLAMA_MODELS_DIR")" 2>/dev/null || true
        sudo mkdir -p /etc/systemd/system/ollama.service.d
        printf '[Service]\nEnvironment="OLLAMA_MODELS=%s"\n' "$OLLAMA_MODELS_DIR" | \
            sudo tee /etc/systemd/system/ollama.service.d/override.conf >/dev/null
        sudo systemctl daemon-reload
        sudo systemctl restart ollama
        sleep 3
    fi
else
    export OLLAMA_MODELS="$OLLAMA_MODELS_DIR"
fi

# 3. 确保服务在运行
if ! curl -s http://127.0.0.1:11434/api/tags >/dev/null; then
    echo "🚀 启动 Ollama 服务..."
    nohup env OLLAMA_MODELS="$OLLAMA_MODELS_DIR" ollama serve >/tmp/ollama.log 2>&1 &
    sleep 3
fi

# 3. 拉取模型
echo "📦 拉取模型 ${LLM_MODEL}..."
ollama pull "$LLM_MODEL"

echo ""
echo "✅ 完成。在 Pixelle-Video Web UI 的「大语言模型」里填："
echo "   Base URL : http://127.0.0.1:11434/v1"
echo "   API Key  : ollama   （随便填，非空即可）"
echo "   模型名   : ${LLM_MODEL}"
