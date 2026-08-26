"""요청 지연시간/결과 롤링 통계 + GPU 사용률 샘플링. /status 운영 지표 대시보드에서 사용."""

import subprocess
import threading
import time
from collections import deque

# 롤링 통계의 기본 관측 윈도우. 알림 룰이 "최근 5분"을 기준으로 판단하므로
# 통계도 같은 기준이어야 한다. 건수 기준(마지막 N건)만 쓰면 트래픽이 끊긴 뒤에도
# 오래된 샘플이 남아, 이미 지나간 포화 구간으로 계속 알림이 울린다.
DEFAULT_WINDOW_SECONDS = 300.0


class LatencyTracker:
    """모델별 요청 지연시간을 시간 윈도우로 기록해 p50/p95를 계산.

    maxlen은 트래픽이 몰릴 때 메모리가 무한정 늘지 않게 하는 상한이고,
    실제 집계 범위를 결정하는 것은 window_seconds다.
    """

    def __init__(self, maxlen: int = 500, window_seconds: float = DEFAULT_WINDOW_SECONDS):
        self._samples = deque(maxlen=maxlen)  # (기록 시각, 지연시간)
        self._window = window_seconds
        self._lock = threading.Lock()

    def record(self, seconds: float):
        with self._lock:
            self._samples.append((time.time(), seconds))

    def percentiles(self, window_seconds: float | None = None) -> dict:
        window = self._window if window_seconds is None else window_seconds
        cutoff = time.time() - window

        with self._lock:
            samples = sorted(value for ts, value in self._samples if ts >= cutoff)

        if not samples:
            return {
                "window_seconds": window,
                "count": 0,
                "p50": None,
                "p95": None,
                "max": None,
            }

        def pct(p):
            idx = min(len(samples) - 1, int(len(samples) * p))
            return round(samples[idx], 3)

        return {
            "window_seconds": window,
            "count": len(samples),
            "p50": pct(0.50),
            "p95": pct(0.95),
            "max": round(samples[-1], 3),
        }


class OutcomeTracker:
    """모델별 요청 결과(성공/타임아웃/에러)를 시간 윈도우로 집계.

    "타임아웃 비율이 높다"와 "이 모델 요청이 전부 타임아웃이다(엔진 행 의심)"를
    구분하려면 지연시간만으로는 부족하고 결과 분포가 필요하다.
    """

    OK = "ok"
    TIMEOUT = "timeout"
    ERROR = "error"

    def __init__(self, maxlen: int = 1000, window_seconds: float = DEFAULT_WINDOW_SECONDS):
        self._samples = deque(maxlen=maxlen)  # (기록 시각, 결과)
        self._window = window_seconds
        self._lock = threading.Lock()

    def record(self, outcome: str):
        with self._lock:
            self._samples.append((time.time(), outcome))

    def counts(self, window_seconds: float | None = None) -> dict:
        window = self._window if window_seconds is None else window_seconds
        cutoff = time.time() - window

        with self._lock:
            outcomes = [outcome for ts, outcome in self._samples if ts >= cutoff]

        total = len(outcomes)
        timeout = outcomes.count(self.TIMEOUT)

        return {
            "window_seconds": window,
            "total": total,
            "ok": outcomes.count(self.OK),
            "timeout": timeout,
            "error": outcomes.count(self.ERROR),
            # 표본이 없을 때 0.0을 돌려주면 "정상"과 구분되지 않으므로 None.
            "timeout_rate": round(timeout / total, 4) if total else None,
        }


_GPU_CACHE_TTL = 1.0  # 초. /status 폴링이 잦아도 nvidia-smi를 매번 호출하지 않도록 캐싱.
_gpu_cache = {"ts": 0.0, "data": None}
_gpu_lock = threading.Lock()


def get_gpu_stats() -> dict | None:
    """nvidia-smi에서 GPU 사용률/VRAM/온도를 읽어 반환. nvidia-smi가 없거나 실패하면 None.

    온도는 가정용 데스크탑 특유의 실패 모드(여름철 쓰로틀링·과열 셧다운) 때문에 함께 본다.
    """
    with _gpu_lock:
        now = time.time()
        if _gpu_cache["data"] is not None and now - _gpu_cache["ts"] < _GPU_CACHE_TTL:
            return _gpu_cache["data"]

        try:
            out = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=2,
            )
            line = out.stdout.strip().splitlines()[0]
            util, mem_used, mem_total, temp = [x.strip() for x in line.split(",")]
            data = {
                "util_pct": float(util),
                "mem_used_mib": float(mem_used),
                "mem_total_mib": float(mem_total),
                "temp_c": float(temp),
            }
        except Exception:
            data = None

        _gpu_cache["ts"] = now
        _gpu_cache["data"] = data
        return data
