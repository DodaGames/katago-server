"""알림 룰 정의.

룰 하나 = 조건 + 지속시간 + 등급이다.

- **조건**: 스냅샷을 보고 지금 문제인지 판단한다. 문제면 알림 본문을, 아니면 None을 돌려준다.
- **지속시간(for_seconds)**: 그 조건이 연속으로 얼마나 참이어야 알림을 보낼지.
  임계값보다 이쪽이 중요하다. 복기 요청 하나가 순간 504 났다고 새벽에 울리면
  며칠 만에 알림을 꺼버리게 된다. 워치독은 1분 간격이므로 300초는 5틱 연속을 뜻한다.
- **등급**: 언제 반응할지에 대한 약속.
  P1 = 지금 일어나서 조치(서비스가 실질적으로 죽음),
  P2 = 아침에 봐도 됨(느리거나 한계 근접, 응답은 됨),
  P3 = 주간 점검.

지연시간·결과 분포 지표 자체가 이미 최근 5분 윈도우다(analysis/metrics.py).
따라서 for_seconds=300인 P2 룰은 "5분 윈도우가 5분 연속 나쁨"이라 발화까지 약 10분이
걸린다. P2는 즉시 대응 대상이 아니므로 이 지연은 오탐을 줄이는 쪽으로만 작용한다.
"""

from dataclasses import dataclass
from typing import Callable

from .collect import Snapshot
from .config import Config

P1, P2, P3 = "P1", "P2", "P3"

# 조치할 때까지 같은 알림을 다시 보내는 간격.
# 죽은 워커는 스스로 살아나지 않으므로(재spawn 없음) P1도 반복 발송이 필요하지만,
# 5분마다 울리면 알림을 꺼버리게 되므로 1시간으로 둔다.
REPEAT_SECONDS = {P1: 3600.0, P2: 21600.0, P3: 86400.0}


@dataclass(frozen=True)
class Rule:
    name: str
    title: str
    severity: str
    for_seconds: float
    evaluate: Callable[[Snapshot, Config], str | None]
    action: str  # 알림에 함께 실어 보낼 조치 안내


def _server_unreachable(snap: Snapshot, cfg: Config) -> str | None:
    if snap.reachable:
        return None
    return f"{cfg.health_url} 응답 없음 ({snap.health_error})."


def _worker_dead(snap: Snapshot, cfg: Config) -> str | None:
    if not snap.dead_models:
        return None
    return (
        f"KataGo 워커 다운: {', '.join(snap.dead_models)}. "
        "이 모델로 가는 요청은 이후 전부 타임아웃된다."
    )


def _service_failed(snap: Snapshot, cfg: Config) -> str | None:
    if snap.unit_active != "failed":
        return None
    return (
        f"{cfg.service_unit} 상태가 failed ({snap.unit_substate}). "
        "짧은 시간에 재시작이 반복되면 systemd가 재시작을 멈춘다(StartLimitBurst=5)."
    )


def _crash_loop(snap: Snapshot, cfg: Config) -> str | None:
    """한 틱(1분) 안에 2회 이상 재시작 = 크래시 루프.

    1회는 사람이 한 `systemctl restart`일 가능성이 높아 여기서 제외한다.
    배포할 때마다 P1이 울리면 알림 자체를 신뢰하지 않게 된다.
    """
    if not snap.restarts_delta or snap.restarts_delta < 2:
        return None
    return (
        f"{cfg.service_unit}가 1분 안에 {snap.restarts_delta}회 재시작됨 "
        f"(NRestarts={snap.unit_restarts}). 크래시 루프."
    )


def _restart_detected(snap: Snapshot, cfg: Config) -> str | None:
    """재시작 1회. 대부분 사람이 한 배포라 조용한 등급으로만 기록한다."""
    if snap.restarts_delta != 1:
        return None
    return f"{cfg.service_unit} 재시작 1회 감지 (NRestarts={snap.unit_restarts})."


def _timeout_rate(snap: Snapshot, cfg: Config) -> str | None:
    total = timeout = 0
    for model in snap.models.values():
        outcomes = model.get("outcomes") or {}
        total += outcomes.get("total") or 0
        timeout += outcomes.get("timeout") or 0

    # 표본이 적으면 한두 건으로 비율이 튄다.
    if total < cfg.timeout_min_samples:
        return None

    rate = timeout / total
    if rate <= cfg.timeout_rate:
        return None

    return (
        f"최근 5분 타임아웃 비율 {rate:.1%} ({timeout}/{total}건, "
        f"임계 {cfg.timeout_rate:.0%})."
    )


def _model_stalled(snap: Snapshot, cfg: Config) -> str | None:
    """프로세스는 살아있는데 응답만 못 하는 상태(GPU 행) 탐지.

    process_alive는 이 상황을 잡지 못한다. 증상이 워커 사망과 똑같이
    "그 모델 요청이 전부 타임아웃"이므로 결과 분포로 판별한다.
    """
    stalled = []
    for model_id, model in snap.models.items():
        outcomes = model.get("outcomes") or {}
        total = outcomes.get("total") or 0
        if (
            total >= cfg.stalled_min_samples
            and (outcomes.get("ok") or 0) == 0
            and (outcomes.get("timeout") or 0) >= cfg.stalled_min_samples
        ):
            stalled.append(f"{model_id}({total}건)")

    if not stalled:
        return None

    return (
        f"모델 {', '.join(stalled)}의 최근 5분 요청이 전부 타임아웃. "
        "프로세스는 살아있으나 응답 불가(GPU 행) 의심."
    )


def _gate_wait(snap: Snapshot, cfg: Config) -> str | None:
    hits = []
    for model_id, model in snap.models.items():
        gate = model.get("concurrency_gate")
        if not gate:
            continue
        wait = gate.get("wait_latency") or {}
        p95 = wait.get("p95")
        if p95 is None or (wait.get("count") or 0) < 5:
            continue
        if p95 > cfg.gate_wait_p95_seconds:
            hits.append(
                f"{model_id} p95={p95:.1f}s (limit={gate.get('limit')}, "
                f"대기 중={gate.get('waiting')})"
            )

    if not hits:
        return None

    return (
        f"동시성 게이트 대기 지연: {', '.join(hits)} "
        f"(임계 {cfg.gate_wait_p95_seconds:.0f}s)."
    )


def _judge_latency(snap: Snapshot, cfg: Config) -> str | None:
    model = snap.models.get(cfg.judge_model_id) or {}
    latency = model.get("latency") or {}
    p95 = latency.get("p95")
    if p95 is None or (latency.get("count") or 0) < cfg.judge_min_samples:
        return None
    if p95 <= cfg.judge_p95_seconds:
        return None

    return (
        f"{cfg.judge_model_id} 지연 p95={p95:.2f}s "
        f"(SLO {cfg.judge_p95_seconds:.0f}s, 최근 {latency['count']}건)."
    )


def _vram(snap: Snapshot, cfg: Config) -> str | None:
    if not snap.gpu:
        return None
    used = snap.gpu.get("mem_used_mib")
    if used is None or used <= cfg.vram_warn_mib:
        return None

    total = snap.gpu.get("mem_total_mib") or 0
    return f"VRAM {used:.0f}/{total:.0f} MiB 사용 (임계 {cfg.vram_warn_mib:.0f} MiB)."


def _gpu_temp(snap: Snapshot, cfg: Config) -> str | None:
    if not snap.gpu:
        return None
    temp = snap.gpu.get("temp_c")
    if temp is None or temp <= cfg.gpu_temp_warn_c:
        return None

    return f"GPU 온도 {temp:.0f}°C (임계 {cfg.gpu_temp_warn_c:.0f}°C). 쓰로틀링 구간."


def _cgroup_memory(snap: Snapshot, cfg: Config) -> str | None:
    if snap.memory_current is None or snap.memory_current <= cfg.memory_warn_bytes:
        return None

    gib = snap.memory_current / 1024**3
    limit_gib = cfg.memory_warn_bytes / 1024**3
    return (
        f"서비스 메모리 {gib:.1f} GiB (임계 {limit_gib:.0f} GiB). "
        "MemoryMax(24G)에 근접 — 넘으면 OOMPolicy=stop으로 서비스가 정리된다."
    )


def _disk_free(snap: Snapshot, cfg: Config) -> str | None:
    if snap.disk_free_pct is None or snap.disk_free_pct >= cfg.disk_free_warn_pct:
        return None

    return (
        f"{cfg.disk_path} 여유 {snap.disk_free_pct:.1f}% "
        f"(임계 {cfg.disk_free_warn_pct:.0f}%)."
    )


def build_rules(cfg: Config) -> list[Rule]:
    """평가 순서대로 룰 목록을 만든다. 심각한 것부터 둔다."""
    return [
        Rule(
            name="server_unreachable",
            title="서버 응답 없음",
            severity=P1,
            # 정상 재시작(수 초)에 발화하지 않도록 2틱을 준다. 진짜 하드 다운은
            # 어차피 워치독이 성공 ping을 못 보내 데드맨 스위치가 먼저 잡는다.
            for_seconds=120,
            evaluate=_server_unreachable,
            action="systemctl status katago-server / journalctl -u katago-server -n 100",
        ),
        Rule(
            name="worker_dead",
            title="KataGo 워커 다운",
            severity=P1,
            for_seconds=0,
            evaluate=_worker_dead,
            action="sudo systemctl restart katago-server. returncode -9면 OOM 의심(journalctl -k | grep -i oom)",
        ),
        Rule(
            name="service_failed",
            title="서비스 failed",
            severity=P1,
            for_seconds=0,
            evaluate=_service_failed,
            action="자동복구에 맡기지 말 것. journalctl -u katago-server -p warning -n 50으로 원인부터 확인",
        ),
        Rule(
            name="crash_loop",
            title="크래시 루프",
            severity=P1,
            for_seconds=0,
            evaluate=_crash_loop,
            action="journalctl -u katago-server -p warning -n 50으로 원인 확인. StartLimitBurst=5에 걸리면 자동 재시작이 멈춘다",
        ),
        Rule(
            name="model_stalled",
            title="모델 응답 불가 의심",
            severity=P2,
            # 결과 분포 자체가 5분 윈도우라 증거가 이미 강하다. 2틱만 확인한다.
            for_seconds=120,
            evaluate=_model_stalled,
            action="nvidia-smi로 GPU 상태 확인 후 sudo systemctl restart katago-server",
        ),
        Rule(
            name="timeout_rate",
            title="타임아웃 비율 상승",
            severity=P2,
            for_seconds=300,
            evaluate=_timeout_rate,
            action="/status의 모델별 queue_size·게이트 대기와 nvidia-smi 확인. GPU 포화면 동시성 상한 재검토",
        ),
        Rule(
            name="gate_wait",
            title="동시성 게이트 포화",
            severity=P2,
            for_seconds=300,
            evaluate=_gate_wait,
            action="지속되면 REVIEW_MAX_CONCURRENT_REQUESTS 또는 maxVisits 재검토",
        ),
        Rule(
            name="judge_latency",
            title="판정 지연 SLO 위반",
            severity=P2,
            for_seconds=300,
            evaluate=_judge_latency,
            action="복기(best) 부하가 판정으로 번졌는지 확인. 두 모델은 프로세스가 분리돼 있어야 한다",
        ),
        Rule(
            name="vram",
            title="VRAM 사용량 높음",
            severity=P2,
            for_seconds=300,
            evaluate=_vram,
            action="nvidia-smi로 점유 프로세스 확인. 고아 KataGo 프로세스가 남아있는지 볼 것",
        ),
        Rule(
            name="gpu_temp",
            title="GPU 온도 높음",
            severity=P2,
            for_seconds=300,
            evaluate=_gpu_temp,
            action="케이스 흡배기·먼지 확인. 지속되면 성능 저하와 과열 셧다운으로 이어진다",
        ),
        Rule(
            name="cgroup_memory",
            title="서비스 메모리 높음",
            severity=P2,
            for_seconds=300,
            evaluate=_cgroup_memory,
            action="systemctl show katago-server -p MemoryCurrent,MemoryPeak로 추이 확인 후 MemoryMax 조정 검토",
        ),
        Rule(
            name="restart_detected",
            title="서비스 재시작",
            severity=P3,
            for_seconds=0,
            evaluate=_restart_detected,
            action="의도한 배포면 무시. 아니면 journalctl -u katago-server -p warning으로 원인 확인",
        ),
        Rule(
            name="disk_free",
            title="디스크 여유 부족",
            severity=P3,
            for_seconds=0,
            evaluate=_disk_free,
            action="journalctl --disk-usage 확인 후 sudo journalctl --vacuum-size=1G",
        ),
    ]
