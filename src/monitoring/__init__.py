"""서버 모니터링.

두 부분으로 나뉜다.

- 앱 안에서 도는 부분(`health`, `auth`): FastAPI가 `/health`, `/status`로 노출하는
  실질 상태 신호와 그 접근 제어.
- 앱 밖에서 도는 부분(`watchdog` 및 그 하위 모듈): systemd 타이머가 1분마다 실행해
  위 엔드포인트와 호스트 상태를 읽고, 알림 룰을 평가해 Slack으로 보내고,
  정상일 때만 외부 데드맨 스위치(Healthchecks.io)에 ping한다.

워치독은 표준 라이브러리만 쓴다. 감시 대상과 같은 venv에 의존하면
그 venv가 깨졌을 때 감시도 같이 죽기 때문이다.

운영 문서: src/monitoring/docs/monitoring.md
"""
