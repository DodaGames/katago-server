"""KataGo 워커 생사를 기준으로 한 실질 헬스체크.

`/health`가 문자열만 돌려주면 엔진이 전부 죽어도 200이 나가고, 그 상태는
이후 모든 요청이 타임아웃될 때에야 드러난다. 워커 생사를 응답에 반영해
데드맨 스위치와 알림이 이 하나에 얹힐 수 있게 한다.
"""

from dataclasses import dataclass

from analysis.pool import get_worker_liveness


@dataclass(frozen=True)
class HealthReport:
    healthy: bool
    dead: list[str]  # 워커가 하나라도 죽은 model_id 목록
    models: dict[str, dict]  # {model_id: {"alive": n, "total": n}}

    def payload(self) -> dict:
        return {
            "status": "ok" if self.healthy else "degraded",
            "dead": self.dead,
            "models": self.models,
        }

    def message(self) -> str:
        if self.healthy:
            return "ok"
        return "KataGo 워커 다운: " + ", ".join(self.dead)


def check_health() -> HealthReport:
    """워커 생사 스냅샷을 만든다.

    부하 상태(큐 적체, 게이트 대기)는 의도적으로 반영하지 않는다. 포화는 고장이 아니고,
    여기서 503을 내면 정상적인 혼잡 구간마다 데드맨 스위치가 울린다.
    포화는 /status 지표와 알림 룰(P2)이 담당한다.
    """
    models = get_worker_liveness()
    dead = sorted(
        model_id for model_id, state in models.items() if state["alive"] < state["total"]
    )
    return HealthReport(healthy=not dead, dead=dead, models=models)
