#!/bin/bash
# 手动冒烟测试：对已经跑起来的 videogen + H3 runtime 发一次真实 T2VA 生成。
#
# ⚠️ 这不是单元测试，是可选的、需要真实 GPU + 模型权重的人工验证脚本。
#    不会在 pytest/CI 里跑。H3 推理很慢（分钟级），请预期长时间等待。
#    运行前确保两个服务都已启动：
#      ./scripts/server-h3.sh
#      ./scripts/server-videogen.sh
#
# 用法: bash scripts/test_h3.sh
#       VIDEOGEN_BASE_URL=http://127.0.0.1:18010 bash scripts/test_h3.sh
set -e

VIDEOGEN_BASE_URL="${VIDEOGEN_BASE_URL:-http://127.0.0.1:18010}"

echo "1) videogen /health ..."
curl -sf "$VIDEOGEN_BASE_URL/health" | python3 -m json.tool

echo ""
echo "2) /v1/backends（检查 minimax-h3 是否可达）..."
curl -sf "$VIDEOGEN_BASE_URL/v1/backends" | python3 -m json.tool

echo ""
echo "3) POST /v1/videos/generate（真实生成，可能需要几分钟）..."
curl -X POST "$VIDEOGEN_BASE_URL/v1/videos/generate" \
    -H "Content-Type: application/json" \
    -d '{
        "backend": "minimax-h3",
        "mode": "t2va",
        "prompt": "A cinematic wide shot of ocean waves under moonlight",
        "duration": 5,
        "width": 768,
        "height": 768,
        "seed": 42
    }' | python3 -m json.tool
