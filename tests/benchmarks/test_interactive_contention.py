"""
테스트 B(요청서 §2-B, ⭐가장 중요): 복기가 돌고 있을 때 인터랙티브 요청이 얼마나
느려지는지 = 포화 임계점을 찾는다.

실제 아키텍처(pool.py/main.py) 반영:
  - "복기"와 "자동종료/결과판정"은 같은 모델(best=b18c384nbt)의 같은 KataGoWorker
    프로세스를 공유한다(network-selection.md 결론). 그래서 복기 부하와 판정 요청은
    "같은 프로세스 큐"에서 직접 경합한다.
  - "AI 대국"(착수)은 약한 모델(level*)의 별도 프로세스를 쓴다. 그래서 복기 부하와
    착수 요청은 프로세스가 분리돼 있고, GPU 하드웨어(단일 CUDA 디바이스)를 통해서만
    간접적으로 경합한다.
  두 경합 유형을 모두 재는 게 이 테스트의 핵심이라 워커를 2개(best/light) 띄운다.

방법:
  - N(=0,1,2,4,8)개의 "복기" 배치를 best 워커에서 계속 돌리면서(하나 끝나면 바로
    다음 게임으로 재투입해 N개를 상시 유지), 그동안
    - "착수" 단발 쿼리 50회+ (light 워커, maxVisits=100 기본)
    - "판정" 단발 쿼리 50회+ (best 워커, maxVisits=500 기본, 복기와 큐를 공유)
    를 순차 발사해 개별 응답시간 p50/p95/p99를 잰다.
  - 캐시 오염 방지를 위해 N 레벨이 바뀔 때마다 두 워커 모두 콜드 재시작하고,
    인터랙티브 쿼리 포지션 풀은 매 N 레벨에서 동일하게 고정(같은 50개 포지션)해서
    N 간 비교가 순수하게 "복기 부하 유무"만의 차이가 되도록 한다.

사용 예:
  python scripts/test_interactive_contention.py --concurrency 0,1,2,4,8
"""

import argparse
import asyncio
import csv
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_ROOT = REPO_ROOT / "src"
THIS_DIR = Path(__file__).resolve().parent
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from kg_bench_lib import (  # noqa: E402
    load_positions, build_payload, time_query, full_analyze_turns,
)
from analysis.worker import KataGoWorker  # noqa: E402
from analysis.config import SERVING_MODELS, analysis_models, base_model_path, config_path as default_config_path  # noqa: E402


def resolve_model_path(name_or_file):
    """config.py의 analysis_models(약한 level 모델) 또는 SERVING_MODELS["best"](강한
    모델) 이름을 실제 파일 경로로 변환. 이미 .gz 파일명이면 그대로 사용."""
    if name_or_file in analysis_models:
        return analysis_models[name_or_file]
    if name_or_file == "best":
        info = SERVING_MODELS["best"]
        return info["main_model"] if isinstance(info, dict) else info
    return name_or_file  # 이미 파일명


def start_named_worker(model_file, config_path):
    full_path = str(Path(base_model_path) / model_file)
    return KataGoWorker(main_model_path=full_path, config_path=config_path)


def stop_worker(worker):
    worker.process.terminate()
    try:
        worker.process.wait(timeout=15)
    except Exception:
        worker.process.kill()


def build_interactive_pool(games, kind, visits, query_prefix):
    """kind='next_move' -> 게임 중반(40~70%) 포지션에서 단발 쿼리.
    kind='judge' -> 게임 종반(전체 수순) 포지션에서 단발 쿼리(자동종료/결과판정 근사)."""
    pool = []
    for i, g in enumerate(games):
        n = g["num_moves"]
        if n < 4:
            continue
        if kind == "next_move":
            cut = max(2, int(n * 0.55))
        else:
            cut = n
        moves = g["moves"][:cut]
        payload = build_payload(
            moves, visits, g["board_size"], g["komi"], g["rules"],
            analyze_turns=None, query_id=f"{query_prefix}_{i}",
        )
        pool.append(payload)
    return pool


async def sustained_review_load(worker, games, visits, n_concurrent, stop_event, timeout, scenes_multiplier=2):
    """N개 슬롯이 각자 '복기 1건'(analyzeTurns 배치 x scenes_multiplier 동시요청)을
    끝나는 대로 계속 재투입해 동시 N건을 상시 유지한다."""

    async def slot_loop(slot_id):
        cursor = slot_id
        while not stop_event.is_set():
            game = games[cursor % len(games)]
            cursor += n_concurrent
            turns = full_analyze_turns(game["num_moves"])
            payloads = [
                build_payload(
                    game["moves"], visits, game["board_size"], game["komi"], game["rules"],
                    analyze_turns=turns, query_id=f"load_{slot_id}_{cursor}_{k}",
                )
                for k in range(scenes_multiplier)
            ]
            await asyncio.gather(*[worker.analyze(p, timeout=timeout) for p in payloads])

    return [asyncio.create_task(slot_loop(i)) for i in range(n_concurrent)]


async def measure_pool(worker, pool, n_requests, timeout):
    times = []
    for i in range(n_requests):
        payload = dict(pool[i % len(pool)])
        payload["id"] = f"{payload['id']}_{i}"
        elapsed, result = await time_query(worker, payload, timeout=timeout)
        times.append(elapsed)
    return times


def pct(sorted_vals, p):
    if not sorted_vals:
        return None
    idx = min(len(sorted_vals) - 1, int(len(sorted_vals) * p))
    return sorted_vals[idx]


async def run_level(args, review_games, next_move_pool_src, judge_pool_src, concurrency, csv_writer):
    print(f"\n{'='*70}\nconcurrency={concurrency} (콜드 재시작)\n{'='*70}")
    best_worker = start_named_worker(resolve_model_path("best"), args.config_path)
    light_worker = start_named_worker(resolve_model_path(args.next_move_model), args.config_path)

    warmup_best = build_payload(review_games[0]["moves"][:2], 30, review_games[0]["board_size"],
                                 review_games[0]["komi"], review_games[0]["rules"], query_id="warmup_best")
    warmup_light = build_payload(review_games[0]["moves"][:2], 30, review_games[0]["board_size"],
                                  review_games[0]["komi"], review_games[0]["rules"], query_id="warmup_light")
    await asyncio.gather(
        time_query(best_worker, warmup_best, timeout=120),
        time_query(light_worker, warmup_light, timeout=120),
    )

    next_move_pool = build_interactive_pool(next_move_pool_src, "next_move", args.next_move_visits, "nm")
    judge_pool = build_interactive_pool(judge_pool_src, "judge", args.judge_visits, "jd")

    stop_event = asyncio.Event()
    load_tasks = []
    try:
        if concurrency > 0:
            load_tasks = await sustained_review_load(
                best_worker, review_games, args.review_visits, concurrency, stop_event, args.timeout,
            )
            await asyncio.sleep(args.ramp_up_sec)  # 부하가 실제로 N건 상시 유지되도록 잠깐 대기

        t0 = time.perf_counter()
        next_move_times, judge_times = await asyncio.gather(
            measure_pool(light_worker, next_move_pool, args.n_requests, args.timeout),
            measure_pool(best_worker, judge_pool, args.n_requests, args.timeout),
        )
        measure_wall = time.perf_counter() - t0
    finally:
        stop_event.set()
        if load_tasks:
            await asyncio.gather(*load_tasks, return_exceptions=True)
        stop_worker(best_worker)
        stop_worker(light_worker)

    nm_sorted = sorted(next_move_times)
    jd_sorted = sorted(judge_times)

    row = {
        "concurrency": concurrency,
        "review_visits": args.review_visits,
        "next_move_visits": args.next_move_visits,
        "judge_visits": args.judge_visits,
        "measure_wall_sec": round(measure_wall, 2),
        "next_move_p50": round(statistics.median(nm_sorted), 3),
        "next_move_p95": round(pct(nm_sorted, 0.95), 3),
        "next_move_p99": round(pct(nm_sorted, 0.99), 3),
        "next_move_max": round(max(nm_sorted), 3),
        "next_move_slo_1s_met": all(t <= 1.0 for t in nm_sorted),
        "judge_p50": round(statistics.median(jd_sorted), 3),
        "judge_p95": round(pct(jd_sorted, 0.95), 3),
        "judge_p99": round(pct(jd_sorted, 0.99), 3),
        "judge_max": round(max(jd_sorted), 3),
        "judge_slo_2s_met": all(t <= 2.0 for t in jd_sorted),
    }
    csv_writer.writerow(row)
    print(
        f"  착수(next_move) p50={row['next_move_p50']}s p95={row['next_move_p95']}s "
        f"p99={row['next_move_p99']}s max={row['next_move_max']}s SLO(<=1s) 충족={row['next_move_slo_1s_met']}"
    )
    print(
        f"  판정(judge)     p50={row['judge_p50']}s p95={row['judge_p95']}s "
        f"p99={row['judge_p99']}s max={row['judge_max']}s SLO(<=2s) 충족={row['judge_slo_2s_met']}"
    )
    return row


async def main_async(args):
    all_games = load_positions(args.positions)
    review_games = all_games  # 부하생성용 (측정 대상 아님, 재사용 무방)
    interactive_games = all_games  # 측정용 포지션 풀 (모든 N레벨에서 동일하게 고정)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "concurrency", "review_visits", "next_move_visits", "judge_visits", "measure_wall_sec",
        "next_move_p50", "next_move_p95", "next_move_p99", "next_move_max", "next_move_slo_1s_met",
        "judge_p50", "judge_p95", "judge_p99", "judge_max", "judge_slo_2s_met",
    ]
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for concurrency in args.concurrency:
            await run_level(args, review_games, interactive_games, interactive_games, concurrency, writer)
            f.flush()

    print(f"\n결과 저장: {out_path}")


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--positions", default=str(THIS_DIR / "bench_data" / "positions.jsonl"))
    p.add_argument("--concurrency", default="0,1,2,4,8")
    p.add_argument("--review-visits", type=int, default=100, help="복기 부하의 maxVisits (운영 권장치)")
    p.add_argument("--next-move-visits", type=int, default=100, help="착수(AI 대국) maxVisits")
    p.add_argument("--judge-visits", type=int, default=500, help="자동종료/결과판정 maxVisits")
    p.add_argument("--next-move-model", default="level3", help="착수에 쓰는 약한 모델(config.analysis_models 키)")
    p.add_argument("--config-path", default=default_config_path)
    p.add_argument("--n-requests", type=int, default=50)
    p.add_argument("--ramp-up-sec", type=float, default=2.0)
    p.add_argument("--timeout", type=float, default=180.0)
    p.add_argument("--output", default=str(THIS_DIR / "bench_results" / "test_b_contention.csv"))
    args = p.parse_args()
    args.concurrency = [int(v) for v in args.concurrency.split(",")]
    return args


if __name__ == "__main__":
    asyncio.run(main_async(parse_args()))
