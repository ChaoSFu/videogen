# videogen

视频生成模型的统一包装层：一个 FastAPI 统一入口，后面接多个相互独立的视频生成
backend。每个 backend 是一个单独进程/单独 HTTP 服务，videogen 只做请求转发与
响应归一化，不在自己进程里加载任何模型。

## Backends

```
videogen
├── Pixelle-Video   —— 自动化短视频工作流（文案 → 配图/视频 → TTS → 合成，经 ComfyUI）
└── MiniMax-H3      —— 原生 文本/图像/参考 → 音视频 生成模型，不经过 ComfyUI
```

两者用途不同、互不依赖：

- **Pixelle-Video** = 自动化短视频工作流引擎，内部通过 ComfyUI 调度 Wan2.1/Flux/Qwen-Image 等模型完成配图与合成，适合"一句话主题 → 成片"。
- **MiniMax-H3** = MiniMax 的原生全模态生成模型（文本/首尾帧/参考图像视频音频 → 视频+音频），直接以 [animede/Diffusers_minimax-h3](https://github.com/animede/Diffusers_minimax-h3)（diffusers 参考实现）作为独立 runtime 接入。**MiniMax-H3 不依赖 ComfyUI**，是与 Pixelle-Video/ComfyUI 完全并行的第二条链路。

均以 git submodule 方式引入 `vendor/`，模型权重不进 git，统一放 `/data`。

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

## 进程管理（ctl.sh）

各服务（H3、videogen 统一 API、ComfyUI、Pixelle Web/API）都有自己的
`scripts/server-*.sh` 前台启动脚本；`scripts/ctl.sh` 在它们外面包了一层
后台生命周期管理，统一用 PID 文件 + 进程组管理，不需要手动开 tmux/nohup：

```bash
scripts/ctl.sh start   h3          # 后台启动，日志写到 run/h3.log
scripts/ctl.sh status               # 查看所有服务状态（不带参数=全部）
scripts/ctl.sh restart h3           # 先停后起，避免端口占用残留
scripts/ctl.sh logs    h3           # tail -f 对应日志
scripts/ctl.sh stop    all          # 停止全部

# service 可选: h3 | videogen | comfyui | pixelle-web | pixelle-api
```

`restart`/`stop` 按**进程组**整体发信号（不是只 kill 记录的那一个 PID）：
`conda run` 有时不会把 SIGTERM 正确转发给它启动的子进程（真正干活的
uvicorn/python），只 kill 父进程会留下孤儿进程继续占着端口和显存。
`ctl.sh` 用 `setsid` 把每个服务放进独立进程组，`stop` 时对整个组发
SIGTERM，10 秒未退出再 `SIGKILL` 兜底，保证不留残留进程。

环境变量覆盖方式不变，照常在调用前 export（或写进 `scripts/h3.env`），
`ctl.sh` 只负责后台化，不改各服务自己的配置逻辑：

```bash
H3_TE_DEVICE=cuda:0 scripts/ctl.sh restart h3
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
bash scripts/setup_ollama.sh    # LLM：Ollama + qwen3:32b（约 20GB）
bash scripts/setup_comfyui.sh   # ComfyUI + 模型（约 65GB，耗时较长）

./scripts/server-comfyui.sh     # 启动 ComfyUI（127.0.0.1:8188）
```

大文件统一放在 `/data` 下：ComfyUI 及模型在 `/data/ComfyUI`，HuggingFace
缓存在 `/data/hf-cache`，Ollama 程序与模型库在 `/data/ollama`，生成的
视频产物在 `/data/pixelle-output`（`vendor/Pixelle-Video/output` 软链接过去）。

Web UI 中配置：
- 大语言模型：Base URL `http://127.0.0.1:11434/v1`，API Key 随意填，模型 `qwen3:32b`
- 本地 ComfyUI：`http://127.0.0.1:8188`
- TTS：默认 edge-tts（免费）

## MiniMax-H3 backend

原生文本/首尾帧/参考 → 视频+音频生成，**不依赖 ComfyUI**，与 Pixelle-Video/ComfyUI 是两条独立链路：

```
client
 ↓ POST /v1/videos/generate
videogen API (127.0.0.1:18010)
 ↓ HTTP
MiniMaxH3Backend
 ↓ HTTP (POST /api/t2va)
H3 runtime (127.0.0.1:18611, vendor/Diffusers_minimax-h3)
 ↓
2 × A6000 48GB
 ↓
MP4
```

测试硬件：**2 × RTX A6000 48GB**（Ampere 架构，不支持 SageAttention 的 sm_120
编译版，因此 `H3_ATTN_BACKEND` 默认 `default`，不用上游默认的 `sage`）。

### 安装

```bash
git submodule update --init -- vendor/Diffusers_minimax-h3   # 首次 clone 用 --recurse-submodules 已包含
bash scripts/setup_h3.sh
```

创建独立 conda 环境 `videogen-h3`（Python 3.12，diffusers 固定 commit
`f37ab93e`、torch 固定 cu128 —— 与主 `videogen` 环境的 3.11 完全隔离，互不
干扰）。只装环境和依赖，**不下载模型**。

### 下载模型（约 144GB，需显式执行）

```bash
bash scripts/download_h3.sh
```

调用上游自带的 `scripts/download_t2va.py`（`huggingface_hub.snapshot_download`
按需拉取 T2VA/FL2VA 必需子目录，不是仓库全量 498GB），下载到
`HF_HOME=/data/hf-cache`。会先检查 `/data` 剩余空间并提示确认；中断后重跑本脚本
即可续传，不会重新下载已完成部分。若仓库需要鉴权，设置 `HF_TOKEN` 环境变量。

### 启动

前台调试用原始脚本，日常使用推荐上面的 `scripts/ctl.sh`（后台 + PID 管理，
避免手动开 tmux）：

```bash
# 前台（看实时日志、首次调试用）
./scripts/server-h3.sh          # H3 runtime，127.0.0.1:18611
./scripts/server-videogen.sh    # videogen 统一 API，127.0.0.1:18010（/docs 有接口文档，/ui 是网页界面）

# 或后台（日常使用）
scripts/ctl.sh start h3
scripts/ctl.sh start videogen
```

两个都只监听 `127.0.0.1`，不直接暴露公网。`server-h3.sh` 默认套用上游 README
"48GB (RTX PRO 5000) – Recommended" 配置到双卡 A6000：

```bash
H3_LOWVRAM=1 H3_TE_PRUNE=1 H3_TE_DEVICE=cuda:1 H3_VIDEO_VAE_FP16=1 \
H3_KEEP_TRANSFORMER=1 H3_ATTN_BACKEND=default
CUDA_VISIBLE_DEVICES=0,1 PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
```

任意变量可在调用时覆盖，例如 `H3_LOWVRAM=0 ./scripts/server-h3.sh`；也可以建一个
不提交 git 的 `scripts/h3.env` 持久化自定义配置。若上游子模块的 README 后续给出
更适合双卡 A6000 的配置，以子模块当前 README 为准，并同步更新这里。

不影响已有的 `scripts/pixelle-*.sh`、`scripts/server-web.sh`、
`scripts/server-api.sh`、`scripts/server-comfyui.sh`、Ollama —— 端口互不重叠
（Pixelle Web 17861、Pixelle API 18001、ComfyUI 8188、Ollama 11434、H3 18611、
videogen API 18010）。

### SSH tunnel

```bash
ssh -L 18010:localhost:18010 \
    -L 18611:localhost:18611 \
    chao@<服务器IP>
```

然后本地访问 `http://localhost:18010/docs`（videogen 统一 API 接口文档）、
`http://localhost:18010/ui`（videogen 自带的网页界面，见下）、可选
`http://localhost:18611`（H3 自带的单页 Web UI，直接体验原始功能）。

### Web 界面 + 历史记录

`videogen` 自带一个零构建依赖的单页前端（纯静态 HTML/JS，由 `server-videogen.sh`
一起启动，无需单独装 Node/跑 npm build），打开 `http://localhost:18010/ui`：

- 填 prompt / 时长 / 分辨率 / seed / 推理步数，点"生成"，H3 是分钟级推理，页面
  会显示已用时长，完成后原地播放结果视频
- 下方"历史记录"列出过往生成（成功的带视频预览，失败的显示错误信息），刷新
  页面不会丢失
- backend 状态徽章会显示 minimax-h3 是否可达/忙碌（对应 `/v1/backends`）

历史记录存在 `run/history.jsonl`（追加写的 JSON Lines 文件，不是数据库，
不进 git），每次 `/v1/videos/generate` 调用（无论成功失败）都会记一条；
`GET /v1/videos?limit=50` 是同一份数据的 API 出口，前端和你自己写脚本都能用。

### curl 测试

```bash
curl -X POST http://127.0.0.1:18010/v1/videos/generate \
  -H "Content-Type: application/json" \
  -d '{
    "backend": "minimax-h3",
    "mode": "t2va",
    "prompt": "A cinematic wide shot of ocean waves under moonlight",
    "duration": 5,
    "width": 768,
    "height": 768,
    "seed": 42
  }'
```

也可以直接跑 `bash scripts/test_h3.sh`（health → backends → 真实生成一次）。
**注意**：这是需要真实 GPU + 模型权重的人工冒烟测试，不是单元测试，H3 推理是
分钟级的，`H3_REQUEST_TIMEOUT` 默认 1800 秒。

### 第一阶段能力

- **T2VA**（文本 → 视频+音频）：P0，已完整接入。
- **FL2VA**（首/尾帧 + 文本 → 视频）：P1，代码结构已预留（`mode="fl2va"`），尚未接入，请求会收到明确的 400 `invalid_request`，不是裸 500。
- **Ref2VA**（参考图像/视频/音频 + 文本 → 视频）：P2，同上，尚未接入。

H3 runtime 当前生成是全局串行的（同一时间只处理一个请求），第二个请求会收到
H3 的 409，videogen 统一转换为 `{"error": "backend_busy", "backend": "minimax-h3"}`
（HTTP 409）。第一期不做排队/调度，接受这个限制。

### 故障排查

| 现象 | 排查方向 |
|---|---|
| `/v1/backends` 里 `minimax-h3.available=false` | H3 runtime 没启动，或 `H3_BASE_URL` 配错；先单独 `curl http://127.0.0.1:18611/api/status` |
| 生成返回 409 `backend_busy` | H3 全局串行锁，等上一个任务结束再试 |
| 生成返回 502 `generation_failed` | 看 `scripts/server-h3.sh` 所在终端的日志，通常是 CUDA OOM 或权重未下载完整 |
| 生成返回 502，detail 是 `'NoneType' object has no attribute 'config'` | H3 自身的已知粗糙点：某个组件（常见是 `H3_TE_DEVICE` 指向的那张卡上的 text_encoder）加载失败后没有正确抛错，日志却打印"加载成功"。**顶层报错看不出真实原因**，去 `server-h3.sh` 终端翻找更早的 `Failed to create component ...` traceback，那里才是根因（实测遇到的是共享 GPU 被其他进程占满导致 OOM，见下一条） |
| 共享 GPU 上显存被别的用户/进程占用 | 多用户机器常见；`nvidia-smi` 看 `H3_TE_DEVICE` 指向的那张卡剩余显存是否够（bnb-4bit + 剪枝的 32B TE 约需 17GB+）。不够就等对方任务结束，或临时把 `H3_TE_DEVICE` 换到空闲的卡，或改用体积小得多的 `H3_TE_PROJ` 投影 TE（~5GB，见上游 README） |
| 生成一直不返回直到超时 | 正常现象之一（H3 推理是分钟级），确认 `H3_REQUEST_TIMEOUT` 是否设置得太小 |
| `setup_h3.sh` 报 GPU 数量不足 2 | 单卡也能跑，但需要去掉/调整 `H3_TE_DEVICE=cuda:1`（没有第二张卡） |
| `setup_h3.sh` 在建目录时报 `Permission denied`（如 `/data`） | 默认路径 `/data/...` 是给特定服务器约定的，不是所有机器都有；用 `HF_HOME=$HOME/hf-cache H3_OUTPUT_DIR=$HOME/videogen-output/minimax-h3 TMPDIR=$HOME/tmp bash scripts/setup_h3.sh` 改到你有权限的目录，`server-h3.sh` 同理，可以建一个不提交 git 的 `scripts/h3.env` 持久化这些覆盖 |
| `download_h3.sh` 中途失败 | 直接重跑，`snapshot_download` 会跳过已下载完整的文件，不会重新下载 |

## 更新子模块到上游最新

```bash
git submodule update --remote vendor/Pixelle-Video
git submodule update --remote vendor/Diffusers_minimax-h3
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
videogen/            # 统一 API 核心包
  ├── api.py          # FastAPI: /health /v1/backends /v1/videos/generate /v1/videos /ui
  ├── schemas.py       # 统一请求/响应模型 + 历史记录模型
  ├── config.py        # 环境变量配置
  ├── history.py       # 生成历史（JSONL 文件，非数据库）
  ├── static/          # 零构建依赖的单页前端（/ui）
  │   └── index.html
  └── backends/        # 各 backend 的 HTTP 客户端（不加载模型）
      ├── base.py       # VideoBackend 协议 + 错误分类
      └── minimax_h3.py # MiniMax-H3 backend
tests/               # 测试（mock HTTP，不需要 GPU）
scripts/             # 启动/部署脚本 + ctl.sh（后台进程管理）
run/                 # ctl.sh 的 PID/日志 + 生成历史（gitignore，运行时产生）
vendor/              # 外部项目（git submodule，模型权重不进 git）
  ├── Pixelle-Video/
  └── Diffusers_minimax-h3/
```

## 配置

Pixelle-Video 的本地配置放在 `config.yaml` / `.env`（已在 .gitignore 中忽略，
勿提交密钥）。videogen 统一 API 与 H3 backend 全部走环境变量，无配置框架：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `VIDEOGEN_HOST` / `VIDEOGEN_PORT` | `127.0.0.1` / `18010` | 统一 API 监听地址 |
| `H3_BASE_URL` | `http://127.0.0.1:18611` | videogen 访问 H3 runtime 的地址 |
| `H3_REQUEST_TIMEOUT` | `1800` 秒 | 生成请求超时（分钟级推理，不能用默认 5/30 秒） |
| `H3_HEALTH_TIMEOUT` | `10` 秒 | `/v1/backends` 健康检查超时，独立于上面 |
| `H3_HOST` / `H3_PORT` | `127.0.0.1` / `18611` | H3 runtime 自身监听地址（`server-h3.sh`） |
| `HF_HOME` | `/data/hf-cache` | HuggingFace 缓存目录 |
| `VIDEOGEN_HISTORY_FILE` | `run/history.jsonl` | 生成历史存储文件（JSON Lines） |

GPU/显存相关的 `H3_LOWVRAM` 等见上面 [MiniMax-H3 backend](#minimax-h3-backend) 一节。
