"""
동시 사용자 부하에서 "복기"(화면 대기형, 판 전체를 analyzeTurns로 한번에 분석) 요청당
지연시간이 어떻게 변하는지 측정한다. 서비스 오픈 후 동시 접속자가 늘어도 모델/설정을
낮추지 않아도 되는지 가늠하기 위한 용도.

동시성 레벨(concurrency)마다 서로 다른 실제 대국을 사용해서(같은 워커 세션 안에서도)
nnCache 재사용으로 인한 착시가 섞이지 않게 한다. (모델x maxVisits) 조합이 바뀔 때는
워커를 재시작해서 완전히 콜드 상태에서 측정한다.

사용 예:
  python scripts/test_concurrency.py --models b28c512nbt,b18c384nbt \
      --max-visits 100,200 --concurrency 1,4,8,16
"""

import argparse
import asyncio
import csv
import random
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from kg_bench_lib import (  # noqa: E402
    load_manifest, load_positions, start_worker, stop_worker,
    build_payload, time_query, full_analyze_turns,
)


async def run_concurrency_level(worker, games, max_visits, args):
    """games(concurrency개)를 동시에 "판 전체 analyzeTurns" 요청으로 보내고
    요청별 개별 완료 시간(다른 유저 입장에서 체감하는 대기시간)을 측정한다."""

    async def one_review(game):
        turns = full_analyze_turns(game["num_moves"])
        payload = build_payload(
            game["moves"], max_visits, game["board_size"], game["komi"], game["rules"],
            analyze_turns=turns, query_id=f"c_{game['game_id']}",
        )
        elapsed, _ = await time_query(worker, payload, timeout=args.timeout)
        return elapsed, len(turns)

    start = time.perf_counter()
    results = await asyncio.gather(*[one_review(g) for g in games])
    wall = time.perf_counter() - start

    per_req_times = [r[0] for r in results]
    return wall, per_req_times


async def main_async(args):
    manifest = load_manifest(args.manifest, args.models.split(",") if args.models else None)
    all_games = load_positions(args.positions)
    rng = random.Random(args.seed)
    rng.shuffle(all_games)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "model", "max_visits", "concurrency", "wall_sec",
        "per_request_p50_sec", "per_request_p95_sec", "per_request_max_sec",
        "reviews_per_min",
    ]
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for model_cfg in manifest:
            for max_visits in args.max_visits:
                print(f"\n=== [{model_cfg['name']}] maxVisits={max_visits} (콜드 재시작) ===")
                worker = start_worker(model_cfg)
                warmup_game = all_games[0]
                warmup = build_payload(warmup_game["moves"][:2], 50, warmup_game["board_size"],
                                        warmup_game["komi"], warmup_game["rules"], query_id="warmup")
                await time_query(worker, warmup, timeout=120)

                pool = all_games[1:]  # warmup에 쓴 0번 제외
                cursor = 0
                try:
                    for conc in args.concurrency:
                        games_slice = pool[cursor:cursor + conc]
                        cursor += conc
                        if len(games_slice) < conc:
                            raise SystemExit(
                                f"표본 게임이 부족합니다 (필요 {cursor}, 보유 {len(pool)}). "
                                f"--positions에 더 큰 표본을 쓰거나 --concurrency를 줄이세요."
                            )
                        wall, per_req = await run_concurrency_level(worker, games_slice, max_visits, args)
                        p50 = statistics.median(per_req)
                        p95 = sorted(per_req)[int(len(per_req) * 0.95)] if len(per_req) > 1 else per_req[0]
                        row = {
                            "model": model_cfg["name"],
                            "max_visits": max_visits,
                            "concurrency": conc,
                            "wall_sec": round(wall, 2),
                            "per_request_p50_sec": round(p50, 2),
                            "per_request_p95_sec": round(p95, 2),
                            "per_request_max_sec": round(max(per_req), 2),
                            "reviews_per_min": round(conc / wall * 60, 1),
                        }
                        writer.writerow(row)
                        f.flush()
                        print(
                            f"  concurrency={conc:3d}  요청당 p50={row['per_request_p50_sec']}s  "
                            f"p95={row['per_request_p95_sec']}s  max={row['per_request_max_sec']}s  "
                            f"처리량={row['reviews_per_min']}건/분"
                        )
                finally:
                    stop_worker(worker)

    print(f"\n결과 저장: {out_path}")


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--manifest", default=str(REPO_ROOT / "scripts" / "bench_models.json"))
    p.add_argument("--models", default=None)
    p.add_argument("--max-visits", default="100,200")
    p.add_argument("--concurrency", default="1,4,8,16")
    p.add_argument("--positions", default=str(REPO_ROOT / "scripts" / "bench_data" / "positions.jsonl"))
    p.add_argument("--timeout", type=float, default=180.0)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--output", default=str(REPO_ROOT / "scripts" / "bench_results" / "concurrency.csv"))
    args = p.parse_args()
    args.max_visits = [int(v) for v in args.max_visits.split(",")]
    args.concurrency = [int(v) for v in args.concurrency.split(",")]
    return args


if __name__ == "__main__":
    asyncio.run(main_async(parse_args()))
