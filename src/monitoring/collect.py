"""알림 룰이 평가할 스냅샷 수집.

앱 지표(/health, /status)와 호스트 지표(systemd 유닛 상태, cgroup 메모리, 디스크)를
한 번에 모은다. 수집 실패는 예외로 터뜨리지 않고 스냅샷의 None/에러 필드로 남긴다 —
"지표를 못 읽었다"와 "지표가 나쁘다"는 다른 상황이고, 전자 때문에 워치독이 죽으면
감시가 통째로 사라진다.
"""

import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .config import Config


@dataclass
class Snapshot:
    now: float

    # /health
    reachable: bool = False
    health_status: int | None = None
    health_error: str | None = None
    dead_models: list[str] = field(default_factory=list)

    # /status
    models: dict = field(default_factory=dict)
    gpu: dict | None = None
    status_error: str | None = None

    # 호스트
    unit_active: str | None = None
    unit_substate: str | None = None
    unit_restarts: int | None = None
    memory_current: int | None = None
    disk_free_pct: float | None = None

    # 직전 실행 대비 재시작 증가분 (watchdog.py가 상태 파일과 비교해 채운다)
    restarts_delta: int | None = None

    @property
    def deadman_ok(self) -> bool:
        """데드맨 스위치에 성공 ping을 보내도 되는 상태인가."""
        return self.reachable and self.health_status == 200 and not self.dead_models

    def deadman_detail(self) -> str:
        if not self.reachable:
            return f"/health 응답 없음: {self.health_error}"
        if self.dead_models:
            return "KataGo 워커 다운: " + ", ".join(self.dead_models)
        if self.health_status != 200:
            return f"/health HTTP {self.health_status}"
        return "ok"


def _get_json(url: str, cfg: Config) -> tuple[int | None, dict | None, str | None]:
    """(상태코드, 본문 JSON, 에러). 503처럼 본문이 있는 에러 응답도 본문을 돌려준다."""
    headers = {"Accept": "application/json"}
    if cfg.monitoring_token:
        headers["X-Monitoring-Token"] = cfg.monitoring_token

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=cfg.http_timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except Exception:
            body = None
        return exc.code, body, None
    except Exception as exc:
        return None, None, f"{type(exc).__name__}: {exc}"


def _dig(value, *keys) -> dict:
    """중첩 dict를 안전하게 따라간다. 중간에 dict가 아닌 값이 나오면 빈 dict.

    응답 형태가 예상과 다를 수 있다(구버전이 떠 있거나, 프록시가 끼어들거나).
    그때 워치독이 예외로 죽으면 감시가 통째로 사라지므로 조용히 빈 값으로 떨어뜨린다.
    """
    for key in keys:
        if not isinstance(value, dict):
            return {}
        value = value.get(key)
    return value if isinstance(value, dict) else {}


def _dead_list(value) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _collect_health(snap: Snapshot, cfg: Config) -> None:
    status, body, error = _get_json(cfg.health_url, cfg)
    snap.health_status = status
    snap.health_error = error

    if status is None:
        snap.reachable = False
        return

    snap.reachable = True

    if status == 200:
        snap.dead_models = _dead_list(_dig(body, "result").get("dead"))
        return

    # 503: 죽은 model_id는 error.details.dead에 담겨 온다 (message 파싱 불필요).
    snap.dead_models = _dead_list(_dig(body, "error", "details").get("dead"))
    if not snap.health_error:
        message = _dig(body, "error").get("message")
        snap.health_error = message if isinstance(message, str) else f"HTTP {status}"


def _collect_status(snap: Snapshot, cfg: Config) -> None:
    status, body, error = _get_json(cfg.status_url, cfg)
    if status != 200 or not body:
        snap.status_error = error or f"HTTP {status}"
        return

    result = _dig(body, "result")
    # 룰이 모델 엔트리마다 .get()을 부르므로 dict인 것만 남긴다.
    snap.models = {
        model_id: entry
        for model_id, entry in _dig(result, "models").items()
        if isinstance(entry, dict)
    }
    gpu = result.get("gpu")
    snap.gpu = gpu if isinstance(gpu, dict) else None


def _collect_unit(snap: Snapshot, cfg: Config) -> None:
    """systemctl show로 유닛 상태를 읽는다. 읽기 전용이라 sudo가 필요 없다."""
    try:
        out = subprocess.run(
            [
                "systemctl",
                "show",
                cfg.service_unit,
                "--property=ActiveState,SubState,NRestarts,MemoryCurrent",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return

    values = {}
    for line in out.stdout.splitlines():
        key, _, value = line.partition("=")
        values[key] = value

    snap.unit_active = values.get("ActiveState") or None
    snap.unit_substate = values.get("SubState") or None

    for key, attr in (("NRestarts", "unit_restarts"), ("MemoryCurrent", "memory_current")):
        try:
            # 값이 없으면 "[not set]"이 온다.
            setattr(snap, attr, int(values[key]))
        except (KeyError, ValueError):
            pass


def _collect_disk(snap: Snapshot, cfg: Config) -> None:
    try:
        usage = shutil.disk_usage(cfg.disk_path)
        snap.disk_free_pct = usage.free / usage.total * 100
    except Exception:
        pass


def collect_snapshot(cfg: Config) -> Snapshot:
    snap = Snapshot(now=time.time())
    _collect_health(snap, cfg)
    if snap.reachable:
        _collect_status(snap, cfg)
    _collect_unit(snap, cfg)
    _collect_disk(snap, cfg)
    return snap
