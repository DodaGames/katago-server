#!/bin/bash
set -e

# 가상환경 활성화 (존재하는 경우)
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
fi

# KataGo(TensorRT 백엔드)가 필요로 하는 공유 라이브러리 경로.
# 인터랙티브 zsh 세션(~/.zshrc)에서만 설정되던 값이라, systemd처럼
# 셸 rc를 거치지 않는 실행 환경에서는 libnvinfer.so.10 로딩에 실패한다.
export LD_LIBRARY_PATH="$HOME/TensorRT-10.9.0.34/lib:/usr/local/cuda-12.8/lib64:$LD_LIBRARY_PATH"

echo "Starting Uvicorn server for production..."
# exec를 사용하여 쉘 프로세스를 uvicorn 프로세스로 대체
# --reload 옵션 제거 (프로덕션 환경)
exec uvicorn main:app --app-dir src --host 0.0.0.0 --port 8000