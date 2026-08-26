"""워치독 설정. 표준 라이브러리만 사용한다.

임계값은 전부 환경변수로 덮어쓸 수 있다. 기본값은 이 호스트의 실제 구성
(RAM 30GiB / MemoryHigh=20G, VRAM 16GB, best_judge SLO p95 2s)에서 역산한 값이다.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"

GIB = 1024**3


def load_env_file(path: Path = DEFAULT_ENV_FILE) -> None:
    """최소 .env 로더.

    프로덕션에서는 systemd가 `EnvironmentFile=`로 같은 파일을 읽어주므로 불필요하지만,
    손으로 `python3 -m monitoring.watchdog --dry-run`을 돌릴 때를 위해 둔다.
    python-dotenv를 쓰지 않는 이유는 워치독이 venv에 의존하지 않아야 하기 때문이다.
    이미 환경에 설정된 값은 덮어쓰지 않는다.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        os.environ.setdefault(key, value)


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ[key])
    except (KeyError, ValueError):
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ[key])
    except (KeyError, ValueError):
        return default


def _default_state_dir() -> Path:
    # systemd가 StateDirectory=로 만들어 준 경로를 우선 쓴다.
    from_systemd = os.getenv("STATE_DIRECTORY")
    if from_systemd:
        return Path(from_systemd.split(":")[0])
    return Path.home() / ".local" / "state" / "katago-watchdog"


@dataclass
class Config:
    # 감시 대상
    health_url: str
    status_url: str
    monitoring_token: str | None
    service_unit: str
    http_timeout: float

    # 알림 채널 (미설정 시 해당 채널은 조용히 건너뛴다)
    healthchecks_url: str | None
    slack_webhook_url: str | None

    # 상태 저장 (지속시간 판정과 반복 알림 억제에 필요)
    state_path: Path

    # 임계값
    timeout_rate: float
    timeout_min_samples: int
    stalled_min_samples: int
    gate_wait_p95_seconds: float
    judge_model_id: str
    judge_p95_seconds: float
    judge_min_samples: int
    vram_warn_mib: float
    gpu_temp_warn_c: float
    memory_warn_bytes: int
    disk_path: str
    disk_free_warn_pct: float

    hostname: str = field(default_factory=lambda: os.uname().nodename)

    @classmethod
    def from_env(cls) -> "Config":
        base = os.getenv("MONITORING_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
        return cls(
            health_url=os.getenv("MONITORING_HEALTH_URL", f"{base}/health"),
            status_url=os.getenv("MONITORING_STATUS_URL", f"{base}/status"),
            monitoring_token=os.getenv("MONITORING_TOKEN") or None,
            service_unit=os.getenv("MONITORING_SERVICE_UNIT", "katago-server.service"),
            http_timeout=_env_float("MONITORING_HTTP_TIMEOUT", 5.0),
            healthchecks_url=(os.getenv("HEALTHCHECKS_URL") or "").rstrip("/") or None,
            # 웹훅 URL은 경로 끝까지가 의미를 가지므로 rstrip("/")를 하지 않는다.
            slack_webhook_url=(os.getenv("SLACK_WEBHOOK_URL") or "").strip() or None,
            state_path=Path(
                os.getenv("MONITORING_STATE_PATH", str(_default_state_dir() / "state.json"))
            ),
            # 최근 5분 타임아웃 비율. 표본이 적으면 한두 건으로 비율이 튀므로 하한을 둔다.
            timeout_rate=_env_float("MONITORING_TIMEOUT_RATE", 0.05),
            timeout_min_samples=_env_int("MONITORING_TIMEOUT_MIN_SAMPLES", 20),
            # "이 모델 요청이 전부 타임아웃" 판정에 필요한 최소 건수.
            stalled_min_samples=_env_int("MONITORING_STALLED_MIN_SAMPLES", 3),
            # 복기 동시성 게이트 대기. 상한(4)이 포화됐는지 보는 신호.
            gate_wait_p95_seconds=_env_float("MONITORING_GATE_WAIT_P95", 10.0),
            # 자동종료/판정 SLO (docs/review-ai-capacity-test-results.md 테스트 B/E).
            judge_model_id=os.getenv("MONITORING_JUDGE_MODEL_ID", "best_judge"),
            judge_p95_seconds=_env_float("MONITORING_JUDGE_P95", 2.0),
            judge_min_samples=_env_int("MONITORING_JUDGE_MIN_SAMPLES", 5),
            # VRAM 16GB 중 14GiB.
            vram_warn_mib=_env_float("MONITORING_VRAM_WARN_MIB", 14 * 1024),
            gpu_temp_warn_c=_env_float("MONITORING_GPU_TEMP_WARN_C", 83.0),
            # katago-server.service는 MemoryHigh=20G / MemoryMax=24G인데, 평상시 사용량이
            # 이미 19GiB 안팎이다(SERVING_MODELS 기준). 20G를 임계로 잡으면 알림이 상시 켜져
            # 무의미해지므로, 하드 상한(24G)에 다가가는 22G를 경고선으로 둔다.
            # 서빙 모델을 늘리면 baseline이 함께 올라가므로 이 값도 다시 재야 한다.
            memory_warn_bytes=_env_int("MONITORING_MEMORY_WARN_BYTES", 22 * GIB),
            disk_path=os.getenv("MONITORING_DISK_PATH", "/var"),
            disk_free_warn_pct=_env_float("MONITORING_DISK_FREE_WARN_PCT", 10.0),
        )
