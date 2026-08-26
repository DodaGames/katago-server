"""알림 발송: Slack(무엇이 잘못됐는지)과 Healthchecks.io(데드맨 스위치).

두 채널의 역할이 다르다.

- Slack: "무엇이 잘못됐는지" 알린다. 서버가 살아서 워치독이 돌 때만 나간다.
- Healthchecks.io: "서버가 살아있는지" 알린다. 정상일 때만 ping을 보내고, ping이
  끊기면 저쪽에서 알림을 만든다. 호스트가 통째로 죽거나 정전·회선 장애로
  워치독이 아예 못 도는 상황을 잡는 유일한 경로라서, 반드시 이 PC 밖에 있어야 한다.

둘 다 아웃바운드 요청만 쓴다. 공유기 포트포워딩·공인 IP 변경·CGNAT와 무관하다.

Slack을 고른 이유는 주 확인 단말이 노트북이기 때문이다. 데스크탑 네이티브 앱이 있고,
같은 채널이 폰 앱에도 그대로 뜬다. 후자가 중요하다 — 회선 장애로 집 인터넷이 끊기면
같은 랜에 있는 노트북도 알림을 못 받는데, 그게 바로 데드맨 스위치가 잡으려는 상황이다.
"""

import json
import urllib.error
import urllib.request

from .config import Config

# 등급별 첨부 색상(왼쪽 세로 막대). 채널을 훑을 때 등급을 색으로 먼저 구분하기 위한 것이다.
_SLACK_COLOR = {"P1": "#d93025", "P2": "#f2a900", "P3": "#9aa0a6"}
_SLACK_EMOJI = {"P1": ":rotating_light:", "P2": ":warning:", "P3": ":information_source:"}


def _request(url: str, *, data: bytes | None, headers: dict, timeout: float) -> tuple[bool, str]:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300, f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        # Slack 웹훅은 실패 이유를 본문에 평문으로 준다(invalid_token, channel_not_found 등).
        # 설치 시점에 웹훅 URL이 틀린 것을 코드 없이 구분하려면 이 문자열이 필요하다.
        try:
            reason = exc.read().decode("utf-8", "replace").strip()[:100]
        except Exception:
            reason = ""
        return False, f"HTTP {exc.code}{f' ({reason})' if reason else ''}"
    except Exception as exc:  # 네트워크 단절 등
        return False, f"{type(exc).__name__}: {exc}"


def _escape(text: str) -> str:
    """Slack mrkdwn 예약문자 이스케이프.

    룰의 조치 안내에 `journalctl -k | grep -i oom` 같은 셸 명령이 그대로 실려 오므로
    `<`, `>`, `&`가 링크 문법으로 잘못 해석되지 않게 막는다.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send_slack(cfg: Config, title: str, message: str, severity: str) -> tuple[bool, str]:
    """Slack Incoming Webhook으로 알림을 보낸다. SLACK_WEBHOOK_URL이 없으면 건너뛴다.

    웹훅 URL 자체가 채널과 인증을 모두 담고 있어 별도 토큰 설정이 없다.
    그래서 이 URL은 비밀값이다 — `.env`에만 두고 커밋하지 않는다.
    """
    if not cfg.slack_webhook_url:
        return False, "SLACK_WEBHOOK_URL 미설정 — 건너뜀"

    headline = f"{_SLACK_EMOJI.get(severity, '')} *[{severity}] {_escape(title)}*"
    payload = {
        # text는 폰 푸시 미리보기와 알림 목록에 쓰인다. blocks만 보내면 "첨부 파일"로만
        # 표시돼 무슨 알림인지 열어봐야 알 수 있으므로 반드시 함께 채운다.
        "text": f"[{severity}] {title}",
        "attachments": [
            {
                "color": _SLACK_COLOR.get(severity, "#9aa0a6"),
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"{headline}\n{_escape(message)}",
                        },
                    },
                    {
                        "type": "context",
                        "elements": [
                            {"type": "mrkdwn", "text": f"host: `{_escape(cfg.hostname)}`"}
                        ],
                    },
                ],
            }
        ],
    }

    return _request(
        cfg.slack_webhook_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout=cfg.http_timeout,
    )


def healthchecks_ping(cfg: Config, ok: bool, detail: str = "") -> tuple[bool, str]:
    """데드맨 스위치에 상태를 알린다. HEALTHCHECKS_URL이 없으면 건너뛴다.

    정상이면 base URL로 ping, 비정상이면 `/fail`로 본문과 함께 보낸다.
    본문(다운된 model_id 등)은 Healthchecks 알림에 그대로 실려 폰까지 전달된다.
    """
    if not cfg.healthchecks_url:
        return False, "HEALTHCHECKS_URL 미설정 — 건너뜀"

    if ok:
        return _request(cfg.healthchecks_url, data=None, headers={}, timeout=cfg.http_timeout)

    return _request(
        f"{cfg.healthchecks_url}/fail",
        data=detail.encode("utf-8"),
        headers={"Content-Type": "text/plain; charset=utf-8"},
        timeout=cfg.http_timeout,
    )
