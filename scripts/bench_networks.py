"""
모델 x maxVisits 조합별 지연시간을 재는 범용/재사용 가능한 빠른 벤치마크 도구.
합성(무작위) 착수를 사용하므로 정확도 비교에는 쓰지 말 것 — 실제 대국 데이터 기반
정확도/지연 sweep은 scripts/run_sweep.py를 사용한다.

용도:
  - "single": 대국 중 자동종료 / 결과 판정처럼 대국당 1회만 호출되는 단발 분석의 지연시간 측정
  - "batch" : 복기처럼 한 판을 독립된 analyzeTurns 요청 여러 개로 동시에 분석하는
              배치 지연시간 측정 (analyzeTurns 안에 중복 turn을 넣으면 KataGo가
              dedup해서 응답 라인이 모자라 타임아웃까지 block되는 버그를 겪었어서,
              "N배 장면"은 독립 요청 N개를 동시에 보내는 방식으로 구현했다)

사용 예:
  python scripts/bench_networks.py \
      --manifest scripts/bench_models.json \
      --max-visits 50,100,200,300,500 \
      --num-moves 50 \
      --output scripts/bench_results/bench.csv
"""

import argparse
import asyncio
import csv
import json
import random
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from utils.generate_random_moves import generate_random_moves  # noqa: E402
from kg_bench_lib import (  # noqa: E402
    load_manifest, start_worker, stop_worker, build_payload,
    time_query, extract_root_info, full_analyze_turns,
)


async def bench_one_model(model_cfg, moves, args, csv_writer, raw_dir: Path):
    name = model_cfg["name"]
    analyze_turns = full_analyze_turns(args.num_moves)

    for max_visits in args.max_visits:
        # 중요: maxVisits 조합마다 워커를 새로 띄워 nnCache를 비운다. 워커를
        # 재사용하면 낮은 maxVisits에서 이미 계산된 포지션이 캐시에 남아
        # 다음 maxVisits 측정(같은 moves를 재분석)이 실제보다 훨씬 빠르게
        # 나오는 캐시 오염 버그가 있었다. repeats>1도 같은 이유로 2번째부터는
        # 이미 캐시가 데워진 상태이니 "cold" 값은 항상 rep 0만 신뢰할 것.
        print(f"\n=== [{name}] loading (visits={max_visits}, model={model_cfg['model_path']}) ===")
        worker = start_worker(model_cfg)
        warmup_payload = build_payload(moves[:6], 50, args.board_size, args.komi, args.rules, query_id="warmup")
        await time_query(worker, warmup_payload, timeout=120)

        try:
            single_times, batch_times = [], []
            single_root = {}

            for rep in range(args.repeats):
                single_payload = build_payload(
                    moves, max_visits, args.board_size, args.komi, args.rules,
                    analyze_turns=None, query_id=f"single_{rep}",
                )
                elapsed, result = await time_query(worker, single_payload, timeout=args.timeout)
                single_times.append(elapsed)
                single_root = extract_root_info(result)

                # "N배 장면" = 독립된 analyzeTurns 요청 N개를 동시에 전송
                batch_payloads = [
                    build_payload(
                        moves, max_visits, args.board_size, args.komi, args.rules,
                        analyze_turns=analyze_turns, query_id=f"batch_{rep}_{k}",
                    )
                    for k in range(args.scenes_multiplier)
                ]
                start = time.perf_counter()
                await asyncio.gather(*[worker.analyze(p, timeout=args.timeout) for p in batch_payloads])
                batch_times.append(time.perf_counter() - start)

            # repeats>1이면 rep 0(콜드)이 가장 현실적인 값이고, 나머지는 캐시가 데워진
            # 상태라 중앙값을 쓰면 낙관적으로 치우친다. 그래서 median이 아니라 rep 0을 쓴다.
            single_cold = single_times[0]
            batch_cold = batch_times[0]
            n_scenes = len(analyze_turns) * args.scenes_multiplier

            row = {
                "model": name,
                "max_visits": max_visits,
                "single_latency_sec": round(single_cold, 4),
                "single_winrate": single_root.get("winrate"),
                "single_score_lead": single_root.get("scoreLead"),
                "batch_scenes": n_scenes,
                "batch_latency_sec": round(batch_cold, 4),
                "batch_sec_per_scene": round(batch_cold / n_scenes, 4),
            }
            csv_writer.writerow(row)
            print(
                f"  visits={max_visits:5d}  single(cold)={single_cold:6.3f}s  "
                f"batch(cold,{n_scenes})={batch_cold:7.3f}s  ({batch_cold / n_scenes:.4f}s/scene)  "
                f"winrate={single_root.get('winrate')}  scoreLead={single_root.get('scoreLead')}"
            )
        finally:
            stop_worker(worker)

    print(f"=== [{name}] done, process terminated ===")


async def main_async(args):
    manifest = load_manifest(args.manifest, args.models.split(",") if args.models else None)

    random.seed(args.seed)
    moves = generate_random_moves(args.board_size, args.num_moves)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    raw_dir = out_path.parent / (out_path.stem + "_raw")
    raw_dir.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "model", "max_visits", "single_latency_sec", "single_winrate",
        "single_score_lead", "batch_scenes", "batch_latency_sec", "batch_sec_per_scene",
    ]
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for model_cfg in manifest:
            await bench_one_model(model_cfg, moves, args, writer, raw_dir)
            f.flush()

    print(f"\nResults written to {out_path}")


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--manifest", default=str(REPO_ROOT / "scripts" / "bench_models.json"))
    p.add_argument("--models", default=None, help="쉼표구분 모델 이름 필터 (미지정시 manifest 전체)")
    p.add_argument("--max-visits", default="50,100,200,300,500", help="쉼표구분 maxVisits 목록")
    p.add_argument("--num-moves", type=int, default=50)
    p.add_argument("--scenes-multiplier", type=int, default=2,
                    help="복기 시나리오: 동시에 보낼 독립 analyzeTurns 요청 개수")
    p.add_argument("--board-size", type=int, default=19)
    p.add_argument("--komi", type=float, default=6.5)
    p.add_argument("--rules", default="korean")
    p.add_argument("--repeats", type=int, default=1,
                    help="조합당 반복 횟수. rep 0(콜드)만 결과에 사용하므로 기본 1이면 충분. "
                         "웜캐시 정상상태를 보고 싶을 때만 늘릴 것")
    p.add_argument("--timeout", type=float, default=180.0)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--output", default=str(REPO_ROOT / "scripts" / "bench_results" / "bench.csv"))
    args = p.parse_args()
    args.max_visits = [int(v) for v in args.max_visits.split(",")]
    return args


if __name__ == "__main__":
    asyncio.run(main_async(parse_args()))
