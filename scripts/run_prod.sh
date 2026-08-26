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
# --no-access-log: main.py의 log_requests 미들웨어가 같은 내용을 지연시간까지 붙여
# 이미 남기므로, uvicorn 기본 access log는 완전 중복이다(요청당 3줄 -> 2줄).
# main.py의 uvicorn.run(access_log=False)와 같은 의도지만, 그쪽은 `python main.py`
# 경로에만 적용되어 정작 이 프로덕션 경로에는 걸리지 않았다.
exec uvicorn main:app --app-dir src --host 0.0.0.0 --port 8000 --no-access-log --use-colors