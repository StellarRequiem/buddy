#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# start_mlx_server.sh — Start an mlx-lm OpenAI-compatible inference server
#                        with speculative decoding on Apple Silicon.
#
# Usage:
#   bash scripts/start_mlx_server.sh               # defaults
#   TARGET=mlx-community/Qwen3-14B-4bit \
#   DRAFT=mlx-community/Qwen3-1.7B-4bit \
#   PORT=7439 bash scripts/start_mlx_server.sh
#
# First run downloads the model weights from HuggingFace (~8 GB for 14B-4bit).
# Subsequent starts load from the mlx cache in ~/.cache/huggingface/.
#
# Prerequisites:
#   pip install mlx-lm          # or: uv pip install mlx-lm
#
# When dflash-mlx adds tool-calling support, swap the server command to:
#   dflash-mlx-openai-server --target-model "$TARGET" --draft-model "$DRAFT" ...
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

TARGET="${TARGET:-mlx-community/Qwen3-14B-4bit}"
DRAFT="${DRAFT:-mlx-community/Qwen3-1.7B-4bit}"
PORT="${PORT:-7439}"
HOST="${HOST:-127.0.0.1}"
NUM_DRAFT_TOKENS="${NUM_DRAFT_TOKENS:-3}"

echo ""
echo "  buddy MLX server"
echo "  ─────────────────────────────────────────────"
echo "  target model : $TARGET"
echo "  draft model  : $DRAFT"
echo "  speculative  : $NUM_DRAFT_TOKENS draft tokens per step"
echo "  listening on : http://$HOST:$PORT"
echo ""
echo "  Set in buddy .env:"
echo "    USE_MLX_BACKEND=true"
echo "    MLX_MODEL=$TARGET"
echo "    MLX_HOST=http://$HOST:$PORT"
echo ""
echo "  Press Ctrl-C to stop."
echo "  ─────────────────────────────────────────────"
echo ""

# Verify mlx-lm is installed
if ! python -c "import mlx_lm" 2>/dev/null; then
    echo "  ✗  mlx-lm not found. Install with:"
    echo "       pip install mlx-lm"
    echo "       # or: uv pip install mlx-lm"
    exit 1
fi

exec python -m mlx_lm.server \
    --model "$TARGET" \
    --draft-model "$DRAFT" \
    --num-draft-tokens "$NUM_DRAFT_TOKENS" \
    --port "$PORT" \
    --host "$HOST"
