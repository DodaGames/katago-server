"""
nvidia-smi를 주기적으로 폴링해 timestamp,utilization.gpu,memory.used를 CSV로 남기는
백그라운드 샘플러. Test A 실행 동안 별도 프로세스로 띄워두고, 실행이 끝나면
run_sweep.py가 남긴 combo_start_ts / combo_wall_sec 구간과 join해서 조합별
GPU 사용률/VRAM 평균·피크를 구하는 데 쓴다.

사용 예:
  python scripts/gpu_monitor.py --interval 1 --output scripts/bench_results/gpu_samples.csv
  (Ctrl-C 또는 SIGTERM으로 종료)
"""

import argparse
import csv
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def sample_once():
    out = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=utilization.gpu,memory.used,memory.total",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True, text=True, timeout=10,
    )
    line = out.stdout.strip().splitlines()[0]
    util, mem_used, mem_total = [x.strip() for x in line.split(",")]
    return float(util), float(mem_used), float(mem_total)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--interval", type=float, default=1.0)
    p.add_argument("--output", default=str(REPO_ROOT / "scripts" / "bench_results" / "gpu_samples.csv"))
    args = p.parse_args()

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["ts", "util_pct", "mem_used_mib", "mem_total_mib"])
        print(f"[gpu_monitor] 샘플링 시작 -> {out_path} (interval={args.interval}s)")
        try:
            while True:
                ts = time.time()
                try:
                    util, mem_used, mem_total = sample_once()
                    writer.writerow([f"{ts:.3f}", util, mem_used, mem_total])
                    f.flush()
                except Exception as e:
                    print(f"[gpu_monitor] sample failed: {e}", file=sys.stderr)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            pass
    print("[gpu_monitor] 종료")


if __name__ == "__main__":
    main()
