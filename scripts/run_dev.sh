#!/bin/zsh

source .venv/bin/activate
# --no-access-log: log_requests 미들웨어와 중복이라 제외 (run_prod.sh와 동일)
uvicorn main:app --app-dir src --host 0.0.0.0 --port 8000 --reload --no-access-log