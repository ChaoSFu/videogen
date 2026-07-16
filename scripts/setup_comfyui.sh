#!/bin/bash
# 部署本地 ComfyUI + 下载 Pixelle-Video selfhost 工作流所需模型
# （Qwen-Image 生图 + Wan2.1 FusionX 生视频，模型合计约 65GB，请确保磁盘充足）
#
# 用法: bash scripts/setup_comfyui.sh
# 可覆盖的环境变量:
#   COMFY_DIR=...               ComfyUI 安装目录（默认 /data/ComfyUI，大文件统一放 /data）
#   COMFY_ENV_NAME=comfyui      conda 环境名
#   HF_ENDPOINT=...             HuggingFace 镜像（默认 hf-mirror.com）
#   PIP_INDEX_URL=...           pip 源（默认清华镜像）
set -e

COMFY_DIR="${COMFY_DIR:-/data/ComfyUI}"
ENV_NAME="${COMFY_ENV_NAME:-comfyui}"
export PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
# HuggingFace 下载缓存、pip 缓存也放 /data，避免撑爆根盘
export HF_HOME="${HF_HOME:-/data/hf-cache}"
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-/data/pip-cache}"

if ! command -v conda &>/dev/null; then
    echo "❌ 未找到 conda"; exit 1
fi

# 1. conda 环境
if ! conda env list | grep -qE "^${ENV_NAME}[[:space:]]"; then
    echo "🐍 创建 conda 环境 ${ENV_NAME}..."
    conda create -n "$ENV_NAME" --override-channels -c conda-forge python=3.11 pip -y
fi

# 2. 获取 ComfyUI 源码
if [ ! -d "$COMFY_DIR" ]; then
    echo "📥 克隆 ComfyUI 到 ${COMFY_DIR}..."
    git clone https://github.com/comfyanonymous/ComfyUI "$COMFY_DIR"
fi

# 3. 安装依赖（含 CUDA 版 torch，首次约 3GB）
echo "📦 安装 ComfyUI 依赖..."
conda run -n "$ENV_NAME" python -m pip install -r "$COMFY_DIR/requirements.txt"
conda run -n "$ENV_NAME" python -m pip install "huggingface_hub[cli]"

# 4. 下载模型
M="$COMFY_DIR/models"
STAGE="$COMFY_DIR/.hf_stage"
mkdir -p "$M/diffusion_models/wan-fusionx" "$M/text_encoders" "$M/vae" "$M/loras" "$STAGE"

# fetch <hf_repo> <repo内文件路径> <目标路径>
fetch() {
    local repo="$1" file="$2" dest="$3"
    if [ -f "$dest" ]; then
        echo "✅ 已存在，跳过: $(basename "$dest")"
        return
    fi
    echo "⬇️  下载 $repo :: $file"
    conda run -n "$ENV_NAME" huggingface-cli download "$repo" "$file" --local-dir "$STAGE"
    mv "$STAGE/$file" "$dest"
}

# --- Wan2.1 FusionX 文生视频（约 28GB + 6.7GB + 254MB）---
fetch vrgamedevgirl84/Wan14BT2VFusioniX "Wan14BT2VFusioniX_fp16_.safetensors" \
      "$M/diffusion_models/wan-fusionx/WanT2V_MasterModel.safetensors"
fetch Comfy-Org/Wan_2.1_ComfyUI_repackaged "split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors" \
      "$M/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors"
fetch Comfy-Org/Wan_2.1_ComfyUI_repackaged "split_files/vae/wan_2.1_vae.safetensors" \
      "$M/vae/wan_2.1_vae.safetensors"

# --- Qwen-Image 文生图（约 20GB + 9GB + 254MB + 60MB）---
fetch Comfy-Org/Qwen-Image_ComfyUI "split_files/diffusion_models/qwen_image_fp8_e4m3fn.safetensors" \
      "$M/diffusion_models/qwen_image_fp8_e4m3fn.safetensors"
fetch Comfy-Org/Qwen-Image_ComfyUI "split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors" \
      "$M/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors"
fetch Comfy-Org/Qwen-Image_ComfyUI "split_files/vae/qwen_image_vae.safetensors" \
      "$M/vae/qwen_image_vae.safetensors"
fetch lightx2v/Qwen-Image-Lightning "Qwen-Image-Lightning-4steps-V1.0.safetensors" \
      "$M/loras/Qwen-Image-Lightning-4steps-V1.0.safetensors"

echo ""
echo "✅ ComfyUI 部署完成。启动："
echo "   ./scripts/server-comfyui.sh   # 监听 127.0.0.1:8188"
echo "   在 Pixelle-Video Web UI 的「本地/自建 ComfyUI」填 http://127.0.0.1:8188"
