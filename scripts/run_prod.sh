#!/bin/bash
set -e

# 가상환경 활성화 (존재하는 경우)
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
fi

echo "Starting Uvicorn server for production..."
# exec를 사용하여 쉘 프로세스를 uvicorn 프로세스로 대체
# --reload 옵션 제거 (프로덕션 환경)
exec uvicorn main:app --host 0.0.0.0 --port 8000