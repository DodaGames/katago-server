"""모니터링 엔드포인트 접근 제어.

서버는 0.0.0.0:8000에 바인딩되고 CORS도 전면 허용이라, 공유기에서 포트포워딩을
하고 있으면 /status의 내부 지표(모델 구성, 큐 상태, GPU/VRAM)가 그대로 공개된다.
운영 지표는 서비스 API와 노출 범위가 달라야 한다.

기본 정책은 "루프백만 허용"이다. 워치독은 같은 호스트에서 돌기 때문에 이걸로 충분하고,
외부에서 봐야 할 일이 생기면 MONITORING_TOKEN을 설정해 토큰으로 연다.
"""

import hmac
import os

from fastapi import HTTPException, Request

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
TOKEN_HEADER = "X-Monitoring-Token"


def _is_loopback(request: Request) -> bool:
    client = request.client
    return bool(client) and client.host in LOOPBACK_HOSTS


def _presented_token(request: Request) -> str | None:
    token = request.headers.get(TOKEN_HEADER)
    if token:
        return token

    authorization = request.headers.get("Authorization", "")
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() == "bearer" and value:
        return value

    return None


def require_monitoring_access(request: Request) -> None:
    """루프백 요청이거나 MONITORING_TOKEN이 일치하면 통과, 아니면 403.

    주의: 앞단에 리버스 프록시를 두면 모든 요청의 client.host가 127.0.0.1이 되어
    루프백 판정이 무의미해진다. 그때는 MONITORING_TOKEN을 반드시 설정하고
    프록시에서 이 경로를 차단해야 한다.
    """
    if _is_loopback(request):
        return

    expected = os.getenv("MONITORING_TOKEN")
    presented = _presented_token(request)
    if expected and presented and hmac.compare_digest(presented, expected):
        return

    raise HTTPException(status_code=403, detail="Monitoring endpoints are not publicly accessible.")
