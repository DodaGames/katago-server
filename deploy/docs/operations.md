# 서버 운영 가이드

2026-08-25부터 프로덕션 서버는 tmux 수동 실행 대신 systemd(`katago-server.service`)로 관리한다.
tmux + 수동 재시작 방식은 auto-reload와 수동 재시작이 겹쳐 발생한 OOM 사고(2026-08) 이후 폐기했다.

## 서비스 관리

```
sudo systemctl start|stop|restart|status katago-server
journalctl -u katago-server -f          # 실시간 로그
journalctl -u katago-server -n 100      # 최근 로그
```

- **재시작은 반드시 `systemctl restart`로만 한다.** tmux를 열어 프로세스를 직접 Ctrl+C 하고 다시 실행하지 않는다 — 그 절차가 지난 OOM 사고의 원인이었다.
- 서버 재부팅 시 자동 기동됨(`enabled`).
- 크래시 시 자동 재시작(`Restart=on-failure`, 5초 후). 60초 내 5회 이상 반복 실패하면 systemd가 재시작을 멈춘다(`StartLimitBurst`) — 이 상태(`systemctl status`에 `Failed` 또는 rate-limit 메시지)를 보면 자동복구에 맡기지 말고 로그부터 확인한다.

## 절대 하지 말 것

- `python src/main.py` 직접 실행 금지. `reload=False`로 고쳐두긴 했지만, 정식 기동 경로는 `scripts/run_prod.sh`(systemd가 호출) 하나뿐이어야 한다.
- `scripts/run_dev.sh`(--reload 켜짐)를 prod에서 실행 금지. 개발 전용.
- 유닛 파일을 `/etc/systemd/system/katago-server.service`에서 직접 수정하지 말 것. 그 파일은 `deploy/katago-server.service`로의 심볼릭 링크다. **`deploy/katago-server.service`를 고치고 커밋한 뒤** `sudo systemctl daemon-reload`.

## 설정 변경 시 절차

```
# deploy/katago-server.service 수정 후
sudo systemctl daemon-reload
sudo systemctl restart katago-server
```

## 알려진 의존성 함정: TensorRT LD_LIBRARY_PATH

KataGo(TensorRT 백엔드)는 `libnvinfer.so.10` 등을 `~/TensorRT-10.9.0.34/lib`, `/usr/local/cuda-12.8/lib64`에서 찾는다.
이 경로는 인터랙티브 zsh 세션(`~/.zshrc`)에서만 설정되어 있고, systemd는 셸 rc 파일을 거치지 않는다.
그래서 `scripts/run_prod.sh`가 `exec uvicorn ...` 하기 전에 **직접** `LD_LIBRARY_PATH`를 export하도록 고쳐놨다.

**증상**: `/health`는 200을 반환하는데(문자열만 반환, 엔진 상태 미확인) `journalctl`에 아래처럼 각 모델마다 뜨면 이 문제다.

```
[KataGo Log] .../usr/bin/katago: error while loading shared libraries: libnvinfer.so.10: cannot open shared object file: No such file or directory
```

**확인**: 정상이면 모델 개수만큼 `KataGo Log] KataGo v...` / `Started, ready to begin handling requests`가 찍힌다.

TensorRT를 재설치하거나 경로/버전을 바꾸면 `scripts/run_prod.sh`의 `LD_LIBRARY_PATH` 경로도 같이 업데이트해야 한다.

## 모델 워커 초기화는 지연 로딩

프로세스가 뜬 직후(`Application startup complete`)에는 KataGo 엔진이 아직 안 떠 있다. 첫 요청이 들어와야 `Initializing Analysis Workers...`가 시작된다.
재시작 직후 바로 `/health`만 찍어보면 "떠 있다"고 착각할 수 있으니, 실제 엔진 로딩 확인은 요청을 한 번 보낸 뒤 로그로 확인한다.

## 메모리

- `MemoryHigh=20G` / `MemoryMax=24G` (호스트 RAM 30GiB 기준, OS/IDE 등 여유분 남김). `OOMPolicy=stop`으로 이 cgroup을 넘으면 커널이 전역 OOM killer로 엉뚱한 프로세스를 죽이기 전에 이 서비스부터 정리한다.
- GPU VRAM(16GB)은 systemd `MemoryMax`로 제어되지 않는다 — 별도 항목. GPU OOM 의심되면 `nvidia-smi`로 확인.
- 실제 사용량 추이 보고 `MemoryMax` 값을 조정할 것 (`systemctl status katago-server`의 `Memory:` 라인, 또는 `systemctl show katago-server -p MemoryCurrent,MemoryPeak`).

## 트러블슈팅 체크리스트

1. `systemctl status katago-server` — Active 상태, 최근 재시작 여부(반복 재시작 중이면 크래시 루프 의심)
2. `journalctl -u katago-server -n 100` — 에러 메시지, `libnvinfer` 관련 여부
3. `curl -s localhost:8000/health` 후 다시 로그 확인 — 모델 5개 전부 `Started, ready to begin handling requests` 찍히는지
4. 메모리: `systemctl status katago-server`의 `Memory:` 라인, `nvidia-smi`
5. 최근 OOM 발생 여부: `journalctl -k | grep -i "out of memory\|oom"`
