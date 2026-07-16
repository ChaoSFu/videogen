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
# 程序本体和模型库都放 /data（根盘空间有限），/usr/local/bin 里只放软链接
OLLAMA_HOME="${OLLAMA_HOME:-/data/ollama}"
OLLAMA_MODELS_DIR="${OLLAMA_MODELS_DIR:-$OLLAMA_HOME/models}"

# 1. 安装 Ollama（需要 sudo）
# 官方 install.sh 也是从 github.com 下载，国内直连超时/极慢，
# 因此直接下二进制包，优先走 GitHub 加速镜像，全部失败才试直连
install_ollama() {
    local pkg=""
    local ok=""
    mkdir -p "$OLLAMA_HOME/dist" 2>/dev/null || {
        sudo mkdir -p "$OLLAMA_HOME/dist" && sudo chown -R "$USER" "$OLLAMA_HOME"
    }
    # 解压/校验 zst 包需要 zstd
    command -v zstd &>/dev/null || sudo apt-get install -y zstd
    # 新版发布包为 .tar.zst，旧版为 .tgz，两种都尝试；
    # 若发现已有完整安装包（如手动 scp 上传的），直接使用
    for asset in ollama-linux-amd64.tar.zst ollama-linux-amd64.tgz; do
        pkg="$OLLAMA_HOME/$asset"
        if [ -s "$pkg" ] && tar -tf "$pkg" >/dev/null 2>&1; then
            echo "✅ 发现已有完整安装包，跳过下载: $pkg"
            ok=1; break
        fi
        local gh_url="github.com/ollama/ollama/releases/latest/download/$asset"
        for prefix in "https://ghfast.top/https://" "https://gh-proxy.com/https://" "https://ghproxy.net/https://" "https://"; do
            echo "⬇️  下载: ${prefix}${gh_url}"
            if curl --http1.1 -fL -C - --connect-timeout 15 --retry 2 -o "$pkg" "${prefix}${gh_url}"; then
                ok=1; break 2
            fi
            echo "⚠️  该源失败，换下一个..."
        done
    done
    [ -n "$ok" ] || { echo "❌ 所有下载源均失败"; return 1; }
    # 程序解压到 /data，/usr/local/bin 只放软链接
    case "$pkg" in
        *.zst) tar --zstd -C "$OLLAMA_HOME/dist" -xf "$pkg" ;;
        *)     tar -C "$OLLAMA_HOME/dist" -xzf "$pkg" ;;
    esac
    rm -f "$pkg"
    sudo ln -sf "$OLLAMA_HOME/dist/bin/ollama" /usr/local/bin/ollama
    # 注册 systemd 服务（以当前用户运行）
    sudo tee /etc/systemd/system/ollama.service >/dev/null <<UNIT
[Unit]
Description=Ollama Service
After=network-online.target

[Service]
ExecStart=$OLLAMA_HOME/dist/bin/ollama serve
Environment="OLLAMA_MODELS=$OLLAMA_MODELS_DIR"
User=$USER
Restart=always

[Install]
WantedBy=multi-user.target
UNIT
    sudo systemctl daemon-reload
    sudo systemctl enable --now ollama
}

# 不仅检查命令存在，还要能真正执行（防止此前中断的安装留下残缺文件）
if ! ollama --version &>/dev/null; then
    echo "📥 安装 Ollama（可能需要输入 sudo 密码）..."
    sudo rm -rf /usr/local/bin/ollama /usr/local/lib/ollama   # 清理残缺安装
    install_ollama
fi
ollama --version &>/dev/null || { echo "❌ Ollama 安装失败，请检查网络后重试"; exit 1; }

# 2. 把模型存储目录指到 /data
sudo mkdir -p "$OLLAMA_MODELS_DIR"
# 目录属主给服务运行者（官方脚本装的是 ollama 用户，二进制方式是当前用户）
if id ollama &>/dev/null; then OWNER=ollama; else OWNER="$USER"; fi
sudo chown -R "$OWNER" "$(dirname "$OLLAMA_MODELS_DIR")"
if systemctl list-unit-files 2>/dev/null | grep -q "^ollama.service"; then
    # systemd 方式：通过 override 配置 OLLAMA_MODELS
    if ! grep -qs "OLLAMA_MODELS=$OLLAMA_MODELS_DIR" /etc/systemd/system/ollama.service.d/override.conf 2>/dev/null; then
        echo "🔧 配置 Ollama 模型目录为 $OLLAMA_MODELS_DIR ..."
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
