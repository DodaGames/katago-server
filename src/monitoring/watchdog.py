"""워치독 본체. systemd 타이머(katago-watchdog.timer)가 1분마다 한 번씩 실행한다.

한 틱에서 하는 일:

1. /health, /status, systemd 유닛 상태, 디스크 여유를 모아 스냅샷을 만든다.
2. 알림 룰을 평가한다. 지속시간을 만족하고 반복 억제 간격을 넘긴 것만 Slack으로 보낸다.
3. 정상일 때만 Healthchecks.io에 ping한다(데드맨 스위치). 비정상이면 /fail에
   원인을 실어 보내고, 워치독이 아예 못 도는 상황은 저쪽의 grace time이 잡는다.

수동 확인:
    python3 -m monitoring.watchdog --dry-run
"""

import argparse
import sys
from dataclasses import dataclass

from .collect import Snapshot, collect_snapshot
from .config import Config, load_env_file
from .notify import healthchecks_ping, send_slack
from .rules import REPEAT_SECONDS, Rule, build_rules
from .state import WatchdogState


@dataclass
class Event:
    rule: Rule
    kind: str  # "fire" | "resolve"
    message: str


def _log(message: str) -> None:
    # 워치독 유닛의 stdout은 journald로 간다 (journalctl -u katago-watchdog).
    print(f"[watchdog] {message}", flush=True)


def evaluate(
    rules: list[Rule], snap: Snapshot, cfg: Config, state: WatchdogState
) -> list[Event]:
    """룰을 평가해 발송할 이벤트만 골라낸다. state는 제자리에서 갱신된다."""
    events: list[Event] = []
    now = snap.now

    for rule in rules:
        message = rule.evaluate(snap, cfg)
        record = state.alerts.get(rule.name)

        if message is None:
            # 조건이 해소됐다. 이미 알림을 보낸 건에 대해서만 복구 알림을 낸다.
            if record and record.get("notified"):
                events.append(Event(rule, "resolve", ""))
            state.alerts.pop(rule.name, None)
            continue

        if record is None:
            record = {"active_since": now, "last_notified": 0.0, "notified": False}
            state.alerts[rule.name] = record

        # 지속시간 미달: 아직 관찰만 한다.
        if now - record["active_since"] < rule.for_seconds:
            continue

        # 반복 억제 간격 이내: 이미 알린 건이다.
        if now - record["last_notified"] < REPEAT_SECONDS[rule.severity]:
            continue

        record["last_notified"] = now
        record["notified"] = True
        events.append(Event(rule, "fire", message))

    return events


def format_event(event: Event) -> tuple[str, str]:
    """이벤트를 알림 제목/본문으로 만든다.

    셀프테스트(monitoring.selftest)가 실제와 똑같은 형식을 재현하려고 같이 쓴다.
    여기서 갈라지면 "테스트는 잘 보이는데 진짜 알림은 다르게 온다"가 된다.
    """
    if event.kind == "fire":
        return event.rule.title, f"{event.message}\n\n조치: {event.rule.action}"
    return f"{event.rule.title} 해소", "조건이 더 이상 참이 아닙니다."


def dispatch(events: list[Event], cfg: Config, dry_run: bool) -> None:
    for event in events:
        title, body = format_event(event)

        if dry_run:
            _log(f"[dry-run] slack {event.rule.severity} {title} | {event.message}")
            continue

        ok, detail = send_slack(cfg, title, body, event.rule.severity)
        _log(f"slack {event.rule.severity} {event.rule.name} {event.kind} -> {detail}")
        if not ok and cfg.slack_webhook_url:
            print(f"[watchdog] 알림 발송 실패: {event.rule.name} ({detail})", file=sys.stderr)


def run_deadman(snap: Snapshot, cfg: Config, dry_run: bool) -> None:
    detail = snap.deadman_detail()

    if dry_run:
        _log(f"[dry-run] healthchecks ok={snap.deadman_ok} detail={detail}")
        return

    ok, result = healthchecks_ping(cfg, snap.deadman_ok, detail)
    _log(f"healthchecks ok={snap.deadman_ok} -> {result}")
    if not ok and cfg.healthchecks_url:
        # ping 실패 자체는 알릴 방법이 없다(그게 알림 경로이므로). 로그로만 남긴다.
        print(f"[watchdog] 데드맨 ping 실패: {result}", file=sys.stderr)


def summarize(snap: Snapshot) -> str:
    parts = [
        f"reachable={snap.reachable}",
        f"health={snap.health_status}",
        f"dead={snap.dead_models or '-'}",
        f"unit={snap.unit_active}/{snap.unit_substate}",
    ]
    if snap.memory_current is not None:
        parts.append(f"mem={snap.memory_current / 1024**3:.1f}GiB")
    if snap.gpu:
        parts.append(
            f"gpu={snap.gpu.get('util_pct')}% "
            f"vram={snap.gpu.get('mem_used_mib')}MiB "
            f"temp={snap.gpu.get('temp_c')}C"
        )
    if snap.status_error:
        parts.append(f"status_error={snap.status_error}")
    return " ".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KataGo 서버 워치독 (1회 실행)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="알림을 실제로 보내지 않고 무엇이 나갈지만 출력한다. 상태 파일도 쓰지 않는다.",
    )
    args = parser.parse_args(argv)

    load_env_file()
    cfg = Config.from_env()
    snap = collect_snapshot(cfg)
    state = WatchdogState.load(cfg.state_path)

    # 재시작 증가분. 첫 실행에는 비교 대상이 없으므로 판정하지 않는다.
    if snap.unit_restarts is not None and state.unit_restarts is not None:
        snap.restarts_delta = max(0, snap.unit_restarts - state.unit_restarts)
    state.unit_restarts = snap.unit_restarts

    _log(summarize(snap))

    # 사람이 의도적으로 멈춘 상태에서는 알림을 내지 않는다. 정비할 때마다 P1이 울리면
    # 알림을 신뢰하지 않게 된다. 단, 이 상태에서도 성공 ping은 보내지 않으므로
    # 긴 정비 전에는 워치독 타이머를 함께 멈춰야 한다(docs/monitoring.md "정비 절차").
    if snap.unit_active == "inactive":
        _log("서비스가 정지 상태(inactive) — 알림/ping 생략")
        state.alerts.clear()
        if not args.dry_run:
            state.save(cfg.state_path)
        return 0

    # 룰 평가에서 예외가 나도 데드맨 ping은 반드시 나가야 한다. 그게 "서버가 살아있는지"를
    # 알리는 유일한 경로라, 알림 룰 하나의 버그로 감시가 통째로 멈추면 안 된다.
    try:
        events = evaluate(build_rules(cfg), snap, cfg, state)
        dispatch(events, cfg, args.dry_run)
    except Exception as exc:
        print(f"[watchdog] 룰 평가 실패: {type(exc).__name__}: {exc}", file=sys.stderr)

    run_deadman(snap, cfg, args.dry_run)

    if not args.dry_run:
        state.save(cfg.state_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
