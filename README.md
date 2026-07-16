服务器部署# videogen

视频生成模型的统一包装层。

首个接入目标：[Pixelle-Video](https://github.com/ATH-MaaS/Pixelle-Video)（AI 全自动短视频生成引擎：文案 → 配图/视频 → TTS → 合成），以 git submodule 方式引入在 `vendor/Pixelle-Video`。

## 环境要求

- Python >= 3.11
- [uv](https://docs.astral.sh/uv/)（依赖管理）
- ffmpeg（Pixelle-Video 视频合成需要）

## 初始化

```bash
git clone --recurse-submodules <本仓库地址>
# 或已 clone 后补拉子模块：
git submodule update --init
```

## 运行 Pixelle-Video

```bash
# Web UI（Streamlit，http://localhost:8501）
./scripts/pixelle-web.sh

# API 服务（FastAPI，http://localhost:8000，文档见 /docs）
./scripts/pixelle-api.sh
```

首次运行会由 uv 自动安装其依赖；LLM / ComfyUI / TTS 等密钥在 Web UI 中配置，或复制
`vendor/Pixelle-Video/config.example.yaml` 为 `config.yaml` 后填写。

也可以用它自带的 Docker 方式：`cd vendor/Pixelle-Video && docker compose up`。

## 服务器部署（conda）

不依赖 uv，用 conda 管理环境（ffmpeg 随环境安装，无需 sudo）：

```bash
git clone --recurse-submodules git@github.com:ChaoSFu/videogen.git
cd videogen
bash scripts/setup_conda.sh
```

服务只监听服务器本机（127.0.0.1），不对外开放端口：

```bash
./scripts/server-web.sh   # Web UI  127.0.0.1:17861
./scripts/server-api.sh   # API 服务 127.0.0.1:18001
```

本地通过 SSH 隧道访问：

```bash
ssh -L 17861:localhost:17861 \
    -L 18001:localhost:18001 \
    chao@<服务器IP>
```

隧道建立后，本地浏览器打开 http://localhost:17861（Web UI）、
http://localhost:18001/docs（API 文档）。

## 本地免费服务（A100 服务器）

不依赖任何付费云服务，LLM 和图像/视频生成全部本地跑：

```bash
bash scripts/setup_ollama.sh    # LLM：Ollama + qwen2.5:14b（约 9GB）
bash scripts/setup_comfyui.sh   # ComfyUI + 模型（约 65GB，耗时较长）

./scripts/server-comfyui.sh     # 启动 ComfyUI（127.0.0.1:8188）
```

Web UI 中配置：
- 大语言模型：Base URL `http://127.0.0.1:11434/v1`，API Key 随意填，模型 `qwen2.5:14b`
- 本地 ComfyUI：`http://127.0.0.1:8188`
- TTS：默认 edge-tts（免费）

## 更新子模块到上游最新

```bash
git submodule update --remote vendor/Pixelle-Video
```

## 开发

```bash
# 安装依赖（含开发依赖）
uv sync

# 运行测试
uv run pytest

# 代码检查
uv run ruff check .
```

## 目录结构

```
videogen/    # 包装层核心包
tests/       # 测试
scripts/     # 启动脚本
vendor/      # 外部项目（git submodule）
  └── Pixelle-Video/
```

## 配置

本地配置放在 `config.yaml` / `.env`（已在 .gitignore 中忽略，勿提交密钥）。
