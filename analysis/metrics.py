"""요청 지연시간 롤링 통계 + GPU 사용률 샘플링. /status 운영 지표 대시보드에서 사용."""

import subprocess
import threading
import time
from collections import deque


class LatencyTracker:
    """모델별 요청 지연시간을 롤링 윈도우로 기록해 p50/p95를 계산."""

    def __init__(self, maxlen: int = 500):
        self._samples = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def record(self, seconds: float):
        with self._lock:
            self._samples.append(seconds)

    def percentiles(self) -> dict:
        with self._lock:
            samples = sorted(self._samples)

        if not samples:
            return {"count": 0, "p50": None, "p95": None, "max": None}

        def pct(p):
            idx = min(len(samples) - 1, int(len(samples) * p))
            return round(samples[idx], 3)

        return {
            "count": len(samples),
            "p50": pct(0.50),
            "p95": pct(0.95),
            "max": round(samples[-1], 3),
        }


_GPU_CACHE_TTL = 1.0  # 초. /status 폴링이 잦아도 nvidia-smi를 매번 호출하지 않도록 캐싱.
_gpu_cache = {"ts": 0.0, "data": None}
_gpu_lock = threading.Lock()


def get_gpu_stats() -> dict | None:
    """nvidia-smi에서 GPU 사용률/VRAM을 읽어 반환. nvidia-smi가 없거나 실패하면 None."""
    with _gpu_lock:
        now = time.time()
        if _gpu_cache["data"] is not None and now - _gpu_cache["ts"] < _GPU_CACHE_TTL:
            return _gpu_cache["data"]

        try:
            out = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu,memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=2,
            )
            line = out.stdout.strip().splitlines()[0]
            util, mem_used, mem_total = [x.strip() for x in line.split(",")]
            data = {
                "util_pct": float(util),
                "mem_used_mib": float(mem_used),
                "mem_total_mib": float(mem_total),
            }
        except Exception:
            data = None

        _gpu_cache["ts"] = now
        _gpu_cache["data"] = data
        return data
