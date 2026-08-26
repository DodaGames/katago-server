"""알림이 실제로 내 눈까지 도착하는지 확인하는 셀프테스트.

워치독이 알림을 "보냈다"는 것과 내가 그것을 "받는다"는 것은 다른 문제다. 웹훅 URL 오타,
채널 알림 설정(기본값은 멘션만 알림), 집중 모드 차단은 전부 발송 성공 로그를 남기면서도
사람에게는 아무것도 도달하지 않는다. 그래서 설치 직후와 설정을 바꾼 뒤에 한 번씩 돌린다.

알림 본문은 워치독과 같은 코드(`watchdog.format_event`)로 만든다. 여기서 갈라지면
"테스트는 잘 보이는데 진짜 알림은 다르게 온다"가 되기 때문이다. 제목에만 `[셀프테스트]`를
붙여 실제 장애와 구분한다.

사용:
    PYTHONPATH=src /usr/bin/python3 -m monitoring.selftest              # 설정 점검 + P1/P2/P3 샘플 3건 발송
    PYTHONPATH=src /usr/bin/python3 -m monitoring.selftest --check      # 발송 없이 설정만 점검
    PYTHONPATH=src /usr/bin/python3 -m monitoring.selftest --severity P1
    PYTHONPATH=src /usr/bin/python3 -m monitoring.selftest --rule worker_dead
    PYTHONPATH=src /usr/bin/python3 -m monitoring.selftest --deadman    # Healthchecks 성공 ping까지
    PYTHONPATH=src /usr/bin/python3 -m monitoring.selftest --deadman-fail   # 데드맨 경로를 실제로 울려본다
"""

import argparse
import sys

from .config import Config, load_env_file
from .notify import healthchecks_ping, send_slack
from .rules import build_rules
from .watchdog import Event, format_event

TEST_PREFIX = "[셀프테스트]"

# 등급별 대표 룰. 실제로 쓰이는 룰의 제목·조치 문구를 그대로 태워 보내야
# "진짜 알림이 왔을 때 무엇을 보게 되는지"를 미리 확인할 수 있다.
_REPRESENTATIVE = {"P1": "worker_dead", "P2": "timeout_rate", "P3": "disk_free"}

# 룰의 evaluate()는 Snapshot을 요구하므로, 본문은 각 룰이 실제로 만들어내는 문장을
# 그대로 본떠 고정값으로 둔다(rules.py의 해당 함수와 같은 문형).
_SAMPLE_MESSAGE = {
    "worker_dead": (
        "KataGo 워커 다운: best. 이 모델로 가는 요청은 이후 전부 타임아웃된다."
    ),
    "timeout_rate": "최근 5분 타임아웃 비율 12.5% (5/40건, 임계 5%).",
    "disk_free": "/var 여유 7.3% (임계 10%).",
}
_FALLBACK_MESSAGE = "셀프테스트용 샘플 본문이다. 실제 장애가 아니다."


def _mask(url: str) -> str:
    """비밀값을 로그에 남기지 않으면서 어느 URL인지는 알아볼 수 있게 줄인다."""
    if len(url) <= 40:
        return url[:20] + "…"
    return f"{url[:33]}…{url[-4:]}"


def _check_config(cfg: Config) -> tuple[list[str], bool]:
    """설정을 점검해 (출력줄, 발송 가능 여부)를 돌려준다."""
    lines: list[str] = []
    can_send = True

    if not cfg.slack_webhook_url:
        lines.append("  ✗ SLACK_WEBHOOK_URL 미설정 — .env에 추가할 것")
        can_send = False
    elif not cfg.slack_webhook_url.startswith("https://hooks.slack.com/services/"):
        # 자체 프록시를 쓸 수도 있으므로 막지는 않고 경고만 한다.
        lines.append(
            f"  ! SLACK_WEBHOOK_URL 형식이 이례적: {_mask(cfg.slack_webhook_url)}"
        )
    else:
        lines.append(f"  ✓ SLACK_WEBHOOK_URL  {_mask(cfg.slack_webhook_url)}")

    if cfg.healthchecks_url:
        lines.append(f"  ✓ HEALTHCHECKS_URL   {_mask(cfg.healthchecks_url)}")
    else:
        # 데드맨 스위치가 없어도 Slack 알림 자체는 동작하므로 실패로 보지 않는다.
        lines.append(
            "  - HEALTHCHECKS_URL 미설정 — 데드맨 스위치 없음(하드 다운 감지 불가)"
        )

    lines.append(f"  · host={cfg.hostname}  timeout={cfg.http_timeout:.0f}s")
    return lines, can_send


def build_test_events(
    cfg: Config, severities: list[str], rule_name: str | None
) -> list[Event]:
    rules = {rule.name: rule for rule in build_rules(cfg)}

    if rule_name:
        rule = rules.get(rule_name)
        if rule is None:
            raise SystemExit(
                f"알 수 없는 룰: {rule_name}\n사용 가능: {', '.join(sorted(rules))}"
            )
        message = _SAMPLE_MESSAGE.get(rule.name, _FALLBACK_MESSAGE)
        return [Event(rule, "fire", message)]

    events = []
    for severity in severities:
        rule = rules[_REPRESENTATIVE[severity]]
        events.append(Event(rule, "fire", _SAMPLE_MESSAGE[rule.name]))
    return events


def send_test_events(events: list[Event], cfg: Config) -> bool:
    all_ok = True
    for event in events:
        title, body = format_event(event)
        ok, detail = send_slack(
            cfg, f"{TEST_PREFIX} {title}", body, event.rule.severity
        )
        mark = "✓" if ok else "✗"
        print(f"  {mark} {event.rule.severity} {event.rule.name:<16} -> {detail}")
        all_ok = all_ok and ok
    return all_ok


def run_deadman_test(cfg: Config, fail_first: bool) -> bool:
    """데드맨 스위치 경로를 확인한다.

    Healthchecks.io → Slack 연동은 워치독 웹훅과 완전히 별개의 경로다. 워치독 알림이
    잘 온다고 이쪽이 살아있다는 보장이 없으므로 따로 확인해야 한다.
    """
    if not cfg.healthchecks_url:
        print("  - HEALTHCHECKS_URL 미설정 — 건너뜀")
        return True

    all_ok = True
    if fail_first:
        ok, detail = healthchecks_ping(
            cfg, False, "셀프테스트: 데드맨 경로 확인 (실제 장애 아님)"
        )
        print(f"  {'✓' if ok else '✗'} /fail 전송 -> {detail}")
        all_ok = all_ok and ok

    ok, detail = healthchecks_ping(cfg, True)
    print(f"  {'✓' if ok else '✗'} 성공 ping  -> {detail}")
    return all_ok and ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Slack 알림이 실제로 도착하는지 확인한다."
    )
    parser.add_argument(
        "--check", action="store_true", help="발송 없이 설정만 점검한다."
    )
    parser.add_argument(
        "--severity",
        choices=["P1", "P2", "P3"],
        help="이 등급 하나만 보낸다. 기본은 세 등급 모두.",
    )
    parser.add_argument(
        "--rule", help="특정 룰의 제목·조치 문구 그대로 보낸다 (예: worker_dead)."
    )
    parser.add_argument(
        "--deadman",
        action="store_true",
        help="Healthchecks.io에 성공 ping을 보내 연결을 확인한다.",
    )
    parser.add_argument(
        "--deadman-fail",
        action="store_true",
        help="Healthchecks.io를 실제로 down 시켰다가 복구한다. "
        "저쪽 Slack 연동까지 확인되지만 진짜 알림이 울린다.",
    )
    args = parser.parse_args(argv)

    load_env_file()
    cfg = Config.from_env()

    print("[설정]")
    lines, can_send = _check_config(cfg)
    print("\n".join(lines))

    if args.check:
        return 0 if can_send else 1

    if not can_send:
        print("\n웹훅이 없어 발송을 건너뛴다.", file=sys.stderr)
        return 1

    severities = [args.severity] if args.severity else ["P1", "P2", "P3"]
    events = build_test_events(cfg, severities, args.rule)

    print("\n[Slack 발송]")
    slack_ok = send_test_events(events, cfg)

    deadman_ok = True
    if args.deadman or args.deadman_fail:
        print("\n[데드맨 스위치]")
        deadman_ok = run_deadman_test(cfg, fail_first=args.deadman_fail)

    print("\n[채널에서 직접 확인할 것]")
    print(f"  1. 위 {len(events)}건이 알림 채널에 보이는가 (제목에 {TEST_PREFIX})")
    print("  2. 등급별 색 막대가 다른가 (P1 빨강 / P2 주황 / P3 회색)")
    print(
        "  3. 맥북에 데스크탑 알림이 떴는가 — 안 뜨면 채널 알림이 '모든 새 메시지'인지 확인"
    )
    print("  4. 폰에도 푸시가 왔는가 — 회선 장애 때 유일하게 남는 경로다")
    if args.deadman_fail:
        print("  5. Healthchecks.io가 보낸 down/up 알림이 같은 채널에 왔는가")

    if not (slack_ok and deadman_ok):
        print("\n발송 실패가 있다. 위 detail을 확인할 것.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
