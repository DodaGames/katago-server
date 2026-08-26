# 서버 운영 가이드

2026-08-25부터 프로덕션 서버는 tmux 수동 실행 대신 systemd(`katago-server.service`)로 관리한다.
tmux + 수동 재시작 방식은 auto-reload와 수동 재시작이 겹쳐 발생한 OOM 사고(2026-08) 이후 폐기했다.

## 서비스 관리

```
sudo systemctl start|stop|restart|status katago-server
journalctl -u katago-server -f          # 실시간 로그
journalctl -u katago-server -n 100      # 최근 로그

journalctl -u katago-server -f | ccze -A # 실시간 로그 (컬러 적용)
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

### 최초 설치 시 심볼릭 링크

유닛 파일과 tmpfiles 설정 모두 `deploy/` 아래 원본을 `/etc`로 링크해서 쓴다.

```
sudo ln -sf "$PWD/deploy/katago-server.service" /etc/systemd/system/katago-server.service
sudo ln -sf "$PWD/deploy/katago-server-tmpfiles.conf" /etc/tmpfiles.d/katago-server.conf
sudo systemctl daemon-reload
```

tmpfiles 규칙이 인식됐는지 확인(설정한 `d ... 7d` 한 줄이 출력되면 정상):

```
systemd-tmpfiles --tldr | grep analysis_logs
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

## 모델 워커 초기화 시점

`src/analysis/pool.py`의 워커 초기화는 모듈 레벨 코드이고 `src/main.py`가 이를 임포트하므로,
**엔진 기동은 `Application startup complete`보다 먼저** 끝난다. 즉 startup이 완료됐다면 엔진도 이미 떠 있다.

> 과거 이 문서는 "첫 요청이 들어와야 초기화되는 지연 로딩"이라고 설명했으나 오진이었다.
> stdout 버퍼링(아래 "로그" 절) 때문에 `Initializing Analysis Workers...`가 journald에 늦게 도착해,
> 첫 요청 뒤에 초기화가 시작된 것처럼 보였을 뿐이다. `PYTHONUNBUFFERED=1` 적용 후로는 실제 순서대로 찍힌다.

## 로그

로그는 전부 journald 한 곳으로 모인다. **별도 로그 파일은 쓰지 않는다** — journald가 이미 영속 저장(`/var/log/journal`)에
자동 rotate까지 하므로, 파일로 이중 저장하면 logrotate 설정과 디스크 관리 부담만 늘어난다.

```
journalctl -u katago-server -f            # 실시간
journalctl -u katago-server -n 100        # 최근 100줄
journalctl -u katago-server -p warning    # 경고 이상만 (에러 추적 시 첫 번째로)
journalctl -u katago-server --since "1 hour ago"
```

### 용량 확인

```
journalctl --disk-usage       # 저널 총 용량
du -sh /var/log/journal       # 실제 디스크 점유
```

두 값이 다른 건 앞의 것이 활성/아카이브 저널 파일만 세기 때문이다. 실사용은 `du` 쪽을 본다.
**유닛별 용량을 보는 명령은 없다.** 근사치가 필요하면 출력 바이트로 잰다:

```
journalctl -u katago-server --since "7 days ago" | wc -c
```

`journald.conf`는 전부 기본값이라 상한은 `SystemMaxUse` = min(`/var` 파일시스템의 10%, 4GB) = **4GB**다.
유닛별 쿼터는 없으므로, 다른 서비스가 로그를 폭주시키면 이 서비스 로그가 먼저 밀려날 수 있다. 수동 정리는:

```
sudo journalctl --vacuum-size=1G
sudo journalctl --vacuum-time=90d
```

### 반드시 `PYTHONUNBUFFERED=1`이어야 한다

Python stdout은 파이프(journald)에 연결되면 8KB 블록 버퍼링된다. 이걸 끄지 않으면
`worker.py`/`pool.py`의 `print()` 출력(`[KataGo Log]`, 엔진 기동 로그)이 버퍼가 찰 때까지 도달하지 않고,
**OOM으로 SIGKILL되면 버퍼 내용이 통째로 유실된다** — 원인 규명이 가장 필요한 순간에.
또 stderr(line-buffered)로 나가는 uvicorn 로그와 순서가 뒤바뀌어 보인다(위 "모델 워커 초기화 시점" 참고).

`deploy/katago-server.service`에 `Environment=PYTHONUNBUFFERED=1`로 설정돼 있다. 지우지 말 것.

### 놓치면 안 되는 로그 줄

| 로그                                                      | 의미                                                                   | 대응                                                       |
| --------------------------------------------------------- | ---------------------------------------------------------------------- | ---------------------------------------------------------- |
| `[KataGo Worker] model=... 프로세스가 예기치 않게 종료됨` | KataGo 프로세스 사망. 이 워커로 가는 요청은 **이후 전부 타임아웃**된다 | 즉시 `systemctl restart`. returncode 확인(`-9`면 OOM 의심) |
| `HTTPException: ... -> 504`                               | KataGo 응답 타임아웃(100초)                                            | 엔진 살아있는지, GPU 포화인지 확인                         |
| `Unhandled exception:`                                    | 미처리 예외. 스택 트레이스가 이어서 찍힌다                             | 코드 버그. 트레이스백 확인                                 |
| `[ConcurrencyGate] model=best 슬롯 대기 N초`              | 복기 동시성 상한(4) 포화                                               | 지속되면 상한/용량 재검토                                  |

`[KataGo Worker] ... 프로세스 정상 종료`는 `systemctl stop` 시 나오는 정상 메시지다(SIGTERM). 경보 아님.

### KataGo 엔진 로그 파일(`analysis_logs/`)

KataGo가 `logDir` 설정(`src/analysis/configs/*.cfg`)에 따라 실행마다 남기는 파일이다.
내용은 config dump + 기동 로그뿐이고(`logAllRequests`/`logSearchInfo` 모두 off),
같은 정보가 `configs/*.cfg`와 journald에 이미 있어 **운영 진단 가치는 사실상 없다.**

KataGo는 `logDir`이 없으면 기동하지 않으므로 디렉터리는 유지하되, 자동 정리를 붙여뒀다
(`deploy/katago-server-tmpfiles.conf` → `/etc/tmpfiles.d/katago-server.conf` 심볼릭 링크, 7일 경과분 삭제).
`systemd-tmpfiles-clean.timer`가 매일 자동 실행하므로 프로덕션에서 `scripts/clean_logs.sh`를 돌릴 일은 없다
(그 스크립트는 벤치마크 로그 정리용으로 계속 남아 있다).

## 메모리

- `MemoryHigh=20G` / `MemoryMax=24G` (호스트 RAM 30GiB 기준, OS/IDE 등 여유분 남김). `OOMPolicy=stop`으로 이 cgroup을 넘으면 커널이 전역 OOM killer로 엉뚱한 프로세스를 죽이기 전에 이 서비스부터 정리한다.
- GPU VRAM(16GB)은 systemd `MemoryMax`로 제어되지 않는다 — 별도 항목. GPU OOM 의심되면 `nvidia-smi`로 확인.
- 실제 사용량 추이 보고 `MemoryMax` 값을 조정할 것 (`systemctl status katago-server`의 `Memory:` 라인, 또는 `systemctl show katago-server -p MemoryCurrent,MemoryPeak`).

## 트러블슈팅 체크리스트

1. `systemctl status katago-server` — Active 상태, 최근 재시작 여부(반복 재시작 중이면 크래시 루프 의심)
2. `journalctl -u katago-server -p warning -n 50` — 경고/에러만. 워커 사망·504·미처리 예외가 여기 다 잡힌다
3. `journalctl -u katago-server -n 100` — 전체 맥락, `libnvinfer` 관련 여부
4. 기동 확인 — 모델 5개 전부 `[KataGo Log] Started, ready to begin handling requests`가 찍혔는지.
   초기화는 startup 이전에 끝나므로 요청을 따로 보낼 필요 없다
5. 메모리: `systemctl status katago-server`의 `Memory:` 라인, `nvidia-smi`
6. 최근 OOM 발생 여부: `journalctl -k | grep -i "out of memory\|oom"`
