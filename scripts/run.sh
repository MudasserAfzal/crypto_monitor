#!/usr/bin/env bash
# One-command helpers for CryptoSignal
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"

case "${1:-help}" in
  up)
    docker compose -f "$ROOT/docker-compose.yml" up -d postgres redis
    ;;
  api)
    cd "$ROOT/backend" && source .venv/bin/activate
    PYTHONPATH=. uvicorn app.main:app --reload --port 8000
    ;;
  analysis|run)
    cd "$ROOT/backend" && source .venv/bin/activate
    shift || true
    PYTHONPATH=. python -m app.cli "$@"
    ;;
  ui)
    cd "$ROOT/frontend" && npm run dev
    ;;
  test)
    cd "$ROOT/backend" && source .venv/bin/activate
    PYTHONPATH=. pytest -q
    ;;
  help|*)
    echo "Usage: ./scripts/run.sh [up|api|analysis|ui|test]"
    echo "  up        Start Postgres + Redis"
    echo "  api       Start FastAPI on :8000"
    echo "  analysis  Run daily signal report (pass --symbols etc.)"
    echo "  ui        Start React dashboard"
    echo "  test      Run unit tests"
    ;;
esac
