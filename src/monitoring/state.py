"""워치독 실행 간 상태 저장.

워치독은 systemd 타이머가 1분마다 새 프로세스로 띄우므로 메모리에 아무것도 남지 않는다.
그런데 알림 룰에는 프로세스 수명을 넘는 판단이 두 개 있다.

- 지속시간("5분간 계속 참일 때만 알림"): 언제부터 참이었는지 기억해야 한다.
- 반복 억제("같은 알림은 1시간에 한 번"): 마지막 발송 시각을 기억해야 한다.

여기에 서비스 재시작 횟수(NRestarts)의 직전 값도 함께 둔다. 증가분이 곧 크래시 신호다.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WatchdogState:
    # {rule_name: {"active_since": float, "last_notified": float, "notified": bool}}
    alerts: dict[str, dict] = field(default_factory=dict)
    unit_restarts: int | None = None

    @classmethod
    def load(cls, path: Path) -> "WatchdogState":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # 첫 실행이거나 파일이 깨진 경우. 상태 없이 시작해도 다음 틱부터 정상화된다.
            return cls()

        return cls(
            alerts=raw.get("alerts") or {},
            unit_restarts=raw.get("unit_restarts"),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"alerts": self.alerts, "unit_restarts": self.unit_restarts}
        # 쓰다가 죽어도 반쯤 쓰인 JSON이 남지 않도록 임시 파일에 쓰고 교체한다.
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
