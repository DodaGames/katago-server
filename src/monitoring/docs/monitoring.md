# 모니터링 운영 가이드

이 서버는 가정용 데스크탑 한 대에서 서빙된다. 그래서 모니터링의 목표는 대시보드가 아니라
**고장 났을 때 내가 알게 되는 것**이고, 설계는 두 가지 전제 위에 있다.

1. **"죽었다"는 사실을 알려주는 경로는 이 PC 밖에 있어야 한다.** 같은 호스트에 감시를 두면
   정전·회선 장애·커널 OOM 같은 진짜 장애에서 감시도 같이 죽어 알림이 안 온다.
2. **아웃바운드 방식만 쓴다.** 외부에서 우리 IP를 찌르는 방식은 포트포워딩·공인 IP 변경·
   CGNAT에 전부 발목이 잡힌다. 서버가 나가는 요청만 쓰면 그 문제가 없다.

## 구성

```
                  [ 이 데스크탑 ]                         [ 외부 ]

  katago-server.service ──┐
      /health  (워커 생사) │
      /status  (운영 지표) │
                          ├─→ katago-watchdog.timer (1분)
  systemd/디스크/GPU ──────┘         │
                                     ├─ 룰 평가 → Slack ────→ 맥북 + 폰
                                     └─ 정상일 때만 ping ──→ Healthchecks.io
                                                              └→ 같은 Slack 채널
```

- **Slack**: "무엇이 잘못됐는지"를 알린다. 워치독이 돌 수 있을 때만 나간다.
- **Healthchecks.io**: "서버가 살아있는지"를 알린다. 호스트가 통째로 죽어 워치독이
  아예 못 도는 상황을 잡는 유일한 경로다. 이쪽 알림도 Slack 연동으로 같은 채널에 떨어진다.

알림 채널을 Slack 하나로 둔 이유는 주 확인 단말이 노트북이기 때문이다. 데스크탑
네이티브 앱이 있고, **같은 채널이 폰 앱에도 그대로 뜬다.** 후자가 설계상 중요하다 —
회선 장애로 집 인터넷이 끊기면 같은 랜에 있는 노트북도 알림을 못 받는데, 그게 바로
데드맨 스위치가 잡으려는 상황이다. 폰은 LTE로 받는다.

코드 위치는 `src/monitoring/`이다. 앱 안에서 도는 부분(`health.py`, `auth.py`)과
앱 밖에서 도는 부분(`watchdog.py` 및 하위 모듈)이 나뉘어 있다.
워치독은 표준 라이브러리만 쓴다 — 감시 대상과 같은 venv에 의존하면 그 venv가 깨졌을 때
감시도 같이 죽기 때문이다. 그래서 systemd 유닛도 `.venv`가 아니라 `/usr/bin/python3`를 부른다.

## 엔드포인트

### `GET /health`

KataGo 워커 생사를 반영한다. 워커가 전부 살아있으면 200, 하나라도 죽었으면 503이다.

```json
// 200
{"success": true, "result": {"status": "ok", "dead": [],
 "models": {"best": {"alive": 1, "total": 1}}}}

// 503 — 죽은 model_id는 error.details.dead에 담긴다 (message 문자열 파싱 불필요)
{"success": false, "error": {"code": "SERVICE_UNAVAILABLE",
 "message": "KataGo 워커 다운: best",
 "details": {"status": "degraded", "dead": ["best"],
             "models": {"best": {"alive": 0, "total": 1}}}}}
```

부하 상태(큐 적체, 게이트 대기)는 **일부러 반영하지 않는다.** 포화는 고장이 아니고,
여기서 503을 내면 정상적인 혼잡 구간마다 데드맨 스위치가 울린다. 포화는 P2 룰이 담당한다.

### `GET /status`

모델별 워커 큐 depth·요청 지연시간·요청 결과 분포·동시성 게이트 상태와 GPU 사용률/온도.
**지연시간과 결과 분포는 최근 5분 윈도우 기준이다** (`analysis/metrics.py`).
알림 룰이 "최근 5분"으로 판단하므로 지표도 같은 기준이어야 하고, 건수 기준(마지막 N건)만
쓰면 트래픽이 끊긴 뒤에도 오래된 샘플이 남아 이미 지나간 포화 구간으로 계속 알림이 울린다.

### 접근 제어

두 엔드포인트 모두 **루프백에서만** 열린다. 서버는 `0.0.0.0:8000`에 바인딩되고 CORS도
전면 허용이라, 공유기에서 포트포워딩을 하고 있으면 내부 지표가 그대로 공개되기 때문이다.

외부에서 봐야 하면 `.env`에 `MONITORING_TOKEN`을 넣고 헤더로 보낸다.

```bash
curl -s http://127.0.0.1:8000/status | jq            # 루프백 — 그냥 됨
curl -s -H "X-Monitoring-Token: $TOKEN" http://<ip>:8000/status | jq
# Authorization: Bearer <token> 도 동일하게 동작
```

- 같은 PC에서라도 `http://<랜IP>:8000/health`로 찌르면 차단된다. 루프백 주소를 쓸 것.
- 앞단에 리버스 프록시를 두면 모든 요청의 client host가 127.0.0.1이 되어 루프백 판정이
  무의미해진다. 그때는 `MONITORING_TOKEN`을 반드시 설정하고 프록시에서 이 경로를 막아야 한다.

## 설치

### 0. 서비스 재시작

`/health`·`/status` 변경은 앱 코드라 재시작해야 적용된다.

```bash
sudo systemctl restart katago-server
curl -s http://127.0.0.1:8000/health | jq   # result가 {"status": "ok", ...} 형태여야 함
```

재시작 전에는 `/health`가 예전처럼 `"result": "ok"` 문자열을 돌려준다. 워치독은 그 형태도
정상(200)으로 처리하므로 순서가 뒤바뀌어도 깨지지는 않지만, 그동안은 워커 사망을 감지하지 못한다.

### 1. 알림 채널 준비

**Slack 채널 + Incoming Webhook** — 알림 전용 채널을 하나 만든다(예: `#katago-alerts`).
운영 대화와 섞으면 알림을 놓친다.

1. <https://api.slack.com/apps> → **Create New App** → From scratch → 워크스페이스 선택
2. **Incoming Webhooks** → 활성화 → **Add New Webhook to Workspace** → 위 채널 선택
3. 발급된 `https://hooks.slack.com/services/...` URL을 받아둔다

이 URL 자체가 채널과 인증을 모두 담고 있다. **즉 비밀값이다** — `.env`에만 두고
커밋하지 않는다. 유출되면 앱 설정에서 해당 웹훅을 지우고 다시 발급받으면 된다.

**Healthchecks.io** — 무료 계정에서 체크 하나를 만든다.

- Period: `1분` (워치독 주기와 동일)
- Grace: `5분` — 부팅 후 KataGo 엔진이 전부 초기화되는 시간을 덮어야 한다.
  이보다 짧으면 재부팅마다 알림이 뜨고, 길면 하드 다운 감지가 그만큼 늦어진다.
  이 설정에서 하드 다운은 최대 6분 안에 잡힌다.
- **Integrations → Slack**에서 같은 채널을 연결하고 Ping URL을 받아둔다.
  여기는 워치독 웹훅과 별개로 Healthchecks가 직접 보내는 경로다. 워치독이 못 도는
  상황을 알리는 게 목적이라 이 PC를 거치지 않아야 한다.

### 2. `.env`에 설정 추가

```
HEALTHCHECKS_URL=https://hc-ping.com/<uuid>
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T000/B000/xxxxxxxx
# MONITORING_TOKEN=...       # 외부에서 /status를 봐야 할 때만
```

`.env`는 커밋되지 않는다. systemd가 `EnvironmentFile=`로 같은 파일을 읽으므로
**`KEY=VALUE` 형식만 쓸 것** — 파싱 못 하는 줄이 있으면 워치독 유닛이 실패한다.

두 URL 중 없는 것은 해당 채널만 조용히 건너뛴다. 하나만 먼저 붙여도 동작한다.

### 3. 유닛 등록

유닛 파일은 `deploy/` 아래 원본을 `/etc`로 링크해서 쓴다(기존 서비스와 같은 방식).

```bash
sudo ln -sf "$PWD/deploy/katago-watchdog.service" /etc/systemd/system/katago-watchdog.service
sudo ln -sf "$PWD/deploy/katago-watchdog.timer"   /etc/systemd/system/katago-watchdog.timer
sudo systemctl daemon-reload
sudo systemctl enable --now katago-watchdog.timer
```

`/etc/systemd/system/` 쪽 파일을 직접 고치지 말 것. 그 파일은 `deploy/` 원본으로의
심볼릭 링크다. **`deploy/`를 고치고 커밋한 뒤** `sudo systemctl daemon-reload`.

### 4. 확인

```bash
systemctl list-timers katago-watchdog          # 다음 실행 시각
journalctl -u katago-watchdog -f               # 매 분 요약 한 줄
```

정상이면 이런 줄이 1분마다 찍힌다.

```
[watchdog] reachable=True health=200 dead=- unit=active/running mem=19.0GiB gpu=30.0% vram=5000.0MiB temp=55.0C
[watchdog] healthchecks ok=True -> HTTP 200
```

알림을 실제로 보내지 않고 무엇이 나갈지만 보려면:

```bash
PYTHONPATH=src /usr/bin/python3 -m monitoring.watchdog --dry-run
```

Slack 발송이 실제로 되는지는 웹훅을 직접 찔러보는 게 빠르다.
**`.env`는 systemd와 파이썬이 읽는 파일이지 셸이 읽는 파일이 아니다.** 손으로 확인할
때는 셸에 먼저 로드해야 한다.

```bash
set -a; . ./.env; set +a                       # .env를 현재 셸로 로드
curl -X POST -H 'Content-Type: application/json' \
  -d '{"text":"watchdog 연결 테스트"}' "$SLACK_WEBHOOK_URL"   # 응답이 ok면 성공
```

`curl: (3) URL rejected: Malformed input to a URL function`이 뜨면 웹훅 URL이 잘못된 게
아니라 **변수가 비어 있는 것이다**(로드를 건너뛴 경우). `echo ${#SLACK_WEBHOOK_URL}`로
길이가 0인지 먼저 본다.

웹훅 URL이 틀리면 워치독 로그에 이유가 그대로 찍힌다
(`slack P1 worker_dead fire -> HTTP 404 (no_service)`).

### 5. 수신 설정

알림을 만들어 보내는 것과 **내가 실제로 보는 것**은 다른 문제다. 여기까지 해야 설치가 끝난다.

- **채널 알림**: `#katago-alerts` → 알림 설정을 **모든 새 메시지**로. 기본값(멘션만)이면
  워치독 알림은 멘션이 없어서 배지도 안 뜬다.
- **맥북**: 시스템 설정 → 집중 모드 → 사용하는 모드마다 Slack을 **허용된 앱**에 추가.
  이게 P1이 방해금지를 뚫는 유일한 경로다.
- **폰**: Slack 모바일 앱에서 같은 채널의 알림을 켠다. 노트북을 닫아두는 시간대와
  집 회선이 끊긴 상황을 이쪽이 담당한다. 이 둘 중 하나라도 빠지면 P1 등급의 의미가 없어진다.

### 6. 셀프테스트

위 curl은 평문 `text`만 보내므로 **연결만** 확인된다. 알림이 실제로 어떤 모양으로 오고
내 눈까지 도달하는지는 셀프테스트로 본다. `.env`를 스스로 읽으므로 `set -a`가 필요 없다.

```bash
PYTHONPATH=src /usr/bin/python3 -m monitoring.selftest
```

세 등급의 샘플 알림을 실제 알림과 **같은 코드 경로**(`watchdog.format_event`)로 보낸다.
제목에 `[셀프테스트]`가 붙는 것만 다르다.

```
[Slack 발송]
  ✓ P1 worker_dead      -> HTTP 200
  ✓ P2 timeout_rate     -> HTTP 200
  ✓ P3 disk_free        -> HTTP 200
```

발송 성공은 **도달을 뜻하지 않는다.** 채널 알림 설정이나 집중 모드에 막히면 `HTTP 200`을
받고도 사람에게는 아무것도 오지 않는다. 그래서 스크립트가 마지막에 눈으로 확인할 항목을
같이 출력한다 — 색 막대 구분, 맥북 데스크탑 알림, 폰 푸시.

| 옵션 | 용도 |
| --- | --- |
| `--check` | 발송 없이 설정만 점검(웹훅 URL 유무·형식) |
| `--severity P1` | 한 등급만 보낸다 |
| `--rule worker_dead` | 특정 룰의 제목·조치 문구 그대로 보낸다 |
| `--deadman` | Healthchecks.io에 성공 ping을 보내 연결 확인 |
| `--deadman-fail` | 체크를 실제로 down 시켰다가 복구 — 저쪽 Slack 연동까지 확인 |

`--deadman-fail`은 Healthchecks가 보내는 **진짜 down/up 알림을 울린다.** 이게 유일하게
그 경로를 끝까지 검증하는 방법이라 넣어뒀지만, 설치 직후 한 번이면 충분하다.
워치독 웹훅과 Healthchecks 연동은 완전히 별개 경로라, 앞의 3건이 잘 왔다고
이쪽이 살아있다는 보장은 없다.

## 알림 룰

등급은 **언제 반응할지에 대한 약속**이다.

- **P1** — 지금 일어나서 조치. 서비스가 실질적으로 죽은 상태. (Slack 빨강)
- **P2** — 아침에 봐도 됨. 느리거나 한계에 근접했지만 응답은 되는 상태. (주황)
- **P3** — 주간 점검. (회색)

Slack에는 우선순위 개념이 없어서 등급은 색 막대와 제목의 `[P1]` 접두사로만 구분된다.
채널을 훑을 때 색으로 먼저 걸러내라는 뜻이다.

`지속`은 그 조건이 연속으로 참이어야 하는 시간이다. **임계값보다 이쪽이 중요하다.**
복기 요청 하나가 순간 504 났다고 새벽에 울리면 며칠 만에 알림을 꺼버리게 된다.
워치독이 1분 간격이므로 300초는 5틱 연속을 뜻한다.

| 룰 | 조건 | 지속 | 등급 |
| --- | --- | --- | --- |
| `worker_dead` | `/health` 503 (워커 사망) | 즉시 | P1 |
| `service_failed` | systemd 유닛 `failed` | 즉시 | P1 |
| `crash_loop` | 1분 안에 재시작 2회 이상 | 즉시 | P1 |
| `server_unreachable` | `/health` 응답 없음 | 2분 | P1 |
| `model_stalled` | 특정 모델의 최근 5분 요청이 전부 타임아웃(3건 이상) | 2분 | P2 |
| `timeout_rate` | 최근 5분 타임아웃 비율 > 5% (20건 이상일 때) | 5분 | P2 |
| `gate_wait` | 동시성 게이트 대기 p95 > 10s | 5분 | P2 |
| `judge_latency` | `best_judge` 지연 p95 > 2s | 5분 | P2 |
| `vram` | VRAM 사용 > 14 GiB (전체 16GB) | 5분 | P2 |
| `gpu_temp` | GPU 온도 > 83°C | 5분 | P2 |
| `cgroup_memory` | 서비스 메모리 > 22 GiB | 5분 | P2 |
| `restart_detected` | 재시작 1회 | 즉시 | P3 |
| `disk_free` | `/var` 여유 < 10% | 즉시 | P3 |

지연시간·결과 분포 지표 자체가 이미 5분 윈도우다. 따라서 지속 5분인 P2 룰은
"5분 윈도우가 5분 연속 나쁨"이라 발화까지 약 10분이 걸린다. P2는 즉시 대응 대상이
아니므로 이 지연은 오탐을 줄이는 쪽으로만 작용한다.

**반복 발송 간격**: P1 1시간, P2 6시간, P3 24시간. 죽은 워커는 스스로 살아나지 않으므로
(재spawn 없음) 조치할 때까지 계속 재발송되지만, 5분마다 울리면 알림 자체를 신뢰하지
않게 되므로 간격을 둔다. 조건이 해소되면 "…해소" 알림이 한 번 나간다.

**재시작 알림이 P1과 P3로 나뉜 이유**: 1분 안에 2회 이상은 크래시 루프(P1)지만,
1회는 대부분 사람이 한 배포다. 배포할 때마다 P1이 울리면 알림을 신뢰하지 않게 된다.

임계값은 전부 환경변수로 덮어쓸 수 있다. 이름은 `src/monitoring/config.py`의
`Config.from_env()`에 모여 있다.

## 알림이 왔을 때

각 알림 본문에는 조치 명령이 함께 실려 온다. 요약하면:

| 알림 | 첫 조치 |
| --- | --- |
| KataGo 워커 다운 | `sudo systemctl restart katago-server`. returncode `-9`면 OOM 의심 → `journalctl -k \| grep -i oom` |
| 서비스 failed / 크래시 루프 | **자동복구에 맡기지 말 것.** `journalctl -u katago-server -p warning -n 50`으로 원인부터 |
| 서버 응답 없음 | `systemctl status katago-server`, `journalctl -u katago-server -n 100` |
| 모델 응답 불가 의심 | `nvidia-smi`로 GPU 상태 확인 후 재시작 |
| 타임아웃 비율 상승 | `/status`의 모델별 `queue_size`·게이트 대기와 `nvidia-smi`. GPU 포화면 동시성 상한 재검토 |
| 동시성 게이트 포화 | 지속되면 `REVIEW_MAX_CONCURRENT_REQUESTS` 또는 `maxVisits` 재검토 |
| 판정 지연 SLO 위반 | 복기(`best`) 부하가 판정으로 번졌는지 확인. 두 모델은 프로세스가 분리돼 있어야 한다 |
| VRAM 높음 | `nvidia-smi`로 점유 프로세스 확인. 고아 KataGo 프로세스가 남아있는지 |
| GPU 온도 높음 | 케이스 흡배기·먼지 확인 |
| 서비스 메모리 높음 | `systemctl show katago-server -p MemoryCurrent,MemoryPeak`로 추이 확인 |
| 디스크 여유 부족 | `journalctl --disk-usage` 후 `sudo journalctl --vacuum-size=1G` |

**Healthchecks 알림만 오고 워치독 알림은 조용한 경우** = 호스트나 회선이 죽어 워치독이
못 돈 것이다. 두 알림은 같은 채널에 떨어지지만 보낸 주체가 다르다 — 워치독 알림은 등급
색 막대와 `host:` 줄이 붙어 있고, Healthchecks 알림은 그쪽 봇 이름으로 온다.
정전, 커널 패닉, 인터넷 끊김을 의심한다. 재부팅 후에도 서비스는 자동 기동된다
(`enabled`). 재부팅 자체가 예상 밖이었다면 정전을 의심할 것.

## 정비 절차

`systemctl restart` 정도의 짧은 재시작은 그대로 해도 된다. `server_unreachable`은 2틱
여유가 있어 발화하지 않고, 재시작 1회는 P3로만 기록된다.

**서비스를 오래 내려둘 때는 워치독 타이머를 먼저 멈춘다.**

```bash
sudo systemctl stop katago-watchdog.timer
# ... 정비 ...
sudo systemctl start katago-watchdog.timer
```

워치독은 유닛이 `inactive`(사람이 의도적으로 정지)면 로컬 알림을 내지 않지만,
**성공 ping도 보내지 않기 때문에** 그대로 두면 Healthchecks 쪽이 grace time 후 울린다.

## 알려진 한계

- **죽은 워커는 자동으로 되살아나지 않는다.** 코드에 재spawn이 없어서 재시작 전까지
  계속 다운 상태다. 자동 재시작을 붙이기 전에 워커가 왜 죽는지(returncode `-9`인지
  아닌지) 데이터를 먼저 쌓는 편이 낫다 — 자동 복구는 원인을 가린다.
- **`process_alive`는 "응답할 수 있나"가 아니라 "프로세스가 살아있나"만 본다.**
  GPU가 행에 걸려 프로세스는 떠 있는데 응답만 못 하는 상태는 `worker_dead`로 안 잡힌다.
  `model_stalled` 룰이 결과 분포로 이걸 메우지만, **그 모델로 요청이 실제로 들어와야**
  판정할 수 있다. 트래픽이 없는 시간대의 행은 감지되지 않는다. 확실히 잡으려면 헬스체크가
  각 모델에 `maxVisits=1` 카나리 쿼리를 날려야 하는데 GPU에 상시 부하가 얹히므로,
  행 사례가 실제로 관측되면 그때 붙이는 것을 권한다.
- **`best`와 `best_judge`는 같은 모델 파일이지만 별개 프로세스다.** 한쪽만 죽을 수 있고
  알림도 model_id 단위로 온다.
- **cgroup `MemoryCurrent`에는 페이지 캐시가 포함된다.** 회수 가능한 메모리라
  이 값이 높다고 곧바로 위험한 것은 아니다. 추세와 `MemoryPeak`을 함께 볼 것.
- **Healthchecks.io는 외부 서비스다.** 그쪽이 죽으면 데드맨 스위치도 죽는다.
  이 구조에서 그 위험을 없앨 방법은 없고(외부에 둬야 한다는 게 전제다),
  자체 호스팅으로 옮기면 "우리 집 밖"이라는 조건만 유지하면 된다.

## 다음 단계 (아직 안 함)

시계열 지표(Prometheus + Grafana)는 넣지 않았다. 그건 장애 감지가 아니라 튜닝·용량산정용이고,
지금 구조로 "고장 났을 때 알게 되는 것"은 이미 충족된다. 필요해지는 시점은 설정값
(`REVIEW_MAX_CONCURRENT_REQUESTS`, `maxVisits`)을 실사용 추이를 보고 조정하고 싶을 때다.

붙인다면 `/status`가 내보내는 값을 그대로 `/metrics`(prometheus 텍스트 포맷)로 노출하고,
알림 룰은 이 파일의 표를 Prometheus 룰로 옮기면 된다. 그때 모니터링 스택 자체가 OOM
원인이 되지 않도록 별도 systemd slice에 `MemoryMax`를 걸 것 — RAM 30GiB 중 24GiB가
이미 서비스 상한이라 여유가 크지 않다.
