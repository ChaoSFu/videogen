#!/bin/bash
# 启动 Pixelle-Video Web UI（Streamlit，默认 http://localhost:8501）
set -e
cd "$(dirname "$0")/../vendor/Pixelle-Video"
exec uv run streamlit run web/app.py "$@"
