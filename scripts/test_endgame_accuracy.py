"""
테스트 E(요청서 §2-E): 자동 종료 / 결과 판정의 최소 요구 성능.

"자동 종료 수순 제공"과 "결과 판정"이 정말 강한 모델 + 높은 visits가 필요한지
확인한다. 둘 다 실제 운영 모델(best=b18c384nbt, network-selection.md 결론)로
단발 쿼리(analyzeTurns 없이 그 판 전체 moves 한 번) 기준으로 측정한다.

포지션은 실제 대국 30판(positions.jsonl에서 층화추출)에서 두 지점을 뽑는다:
  - auto_end: 수순의 90% 지점 (자동종료 여부를 판단하는 시점 근사)
  - final   : 수순 100%, 즉 종국 포지션 (결과 판정 시점)

800 visits 결과를 그 모델 자신의 정답(reference)으로 두고, 400/200/100/50 visits
결과와 winrate/scoreLead 오차 및 소요시간을 비교한다(테스트 C와 동일하게 자기
자신 대비 내구성 비교 방식 - 사람 검수 라벨은 없음).

사용 예:
  python scripts/test_endgame_accuracy.py --model best
"""

import argparse
import asyncio
import csv
import random
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from kg_bench_lib import load_positions, build_payload, time_query, extract_root_info  # noqa: E402
from katago_worker import KataGoWorker  # noqa: E402
from config import SERVING_MODELS, base_model_path, config_path as default_config_path  # noqa: E402


def stratified_sample(games, sample_size, seed):
    rng = random.Random(seed)
    by_size = defaultdict(list)
    for g in games:
        by_size[g["board_size"]].append(g)
    total = len(games)
    sampled = []
    for size, bucket in sorted(by_size.items()):
        rng.shuffle(bucket)
        quota = max(1, round(sample_size * len(bucket) / total))
        sampled.extend(bucket[:quota])
    rng.shuffle(sampled)
    return sampled[:sample_size] if len(sampled) > sample_size else sampled


def start_named_worker(model_file, config_path):
    full_path = str(Path(base_model_path) / model_file)
    return KataGoWorker(main_model_path=full_path, config_path=config_path)


def stop_worker(worker):
    worker.process.terminate()
    try:
        worker.process.wait(timeout=15)
    except Exception:
        worker.process.kill()


def build_positions(games):
    """게임마다 (auto_end=90% 지점, final=100% 지점) 포지션 페이로드 소스를 만든다."""
    out = {"auto_end": [], "final": []}
    for g in games:
        n = g["num_moves"]
        if n < 4:
            continue
        cut_auto = max(2, int(n * 0.9))
        out["auto_end"].append({"game_id": g["game_id"], "moves": g["moves"][:cut_auto],
                                 "board_size": g["board_size"], "komi": g["komi"], "rules": g["rules"]})
        out["final"].append({"game_id": g["game_id"], "moves": g["moves"], "board_size": g["board_size"],
                              "komi": g["komi"], "rules": g["rules"]})
    return out


async def run_visits_level(worker, positions_by_kind, max_visits, timeout):
    """kind별로 모든 포지션을 단발 쿼리하고 {(kind, game_id): (winrate, scoreLead, elapsed)}를 반환."""
    out = {}
    for kind, pos_list in positions_by_kind.items():
        for pos in pos_list:
            payload = build_payload(
                pos["moves"], max_visits, pos["board_size"], pos["komi"], pos["rules"],
                analyze_turns=None, query_id=f"{kind}_{pos['game_id']}",
            )
            elapsed, result = await time_query(worker, payload, timeout=timeout)
            root = extract_root_info(result)
            out[(kind, pos["game_id"])] = {
                "winrate": root.get("winrate"), "scoreLead": root.get("scoreLead"), "elapsed": elapsed,
            }
    return out


async def main_async(args):
    all_games = load_positions(args.positions)
    sample = stratified_sample(all_games, args.sample_size, args.seed)
    print(f"Test E 대상: {len(sample)}판 (자동종료 90%지점 / 결과판정 종국지점), "
          f"모델={args.model}, visits={[args.reference_visits] + args.visits}")

    positions_by_kind = build_positions(sample)
    model_file = SERVING_MODELS["best"]["main_model"] if args.model == "best" else args.model

    visits_levels = [args.reference_visits] + args.visits
    by_visits = {}
    for visits in visits_levels:
        print(f"\n=== visits={visits} (콜드 재시작) ===")
        worker = start_named_worker(model_file, args.config_path)
        warmup = build_payload(sample[0]["moves"][:2], 30, sample[0]["board_size"], sample[0]["komi"],
                                sample[0]["rules"], query_id="warmup")
        await time_query(worker, warmup, timeout=120)
        t0 = time.perf_counter()
        try:
            by_visits[visits] = await run_visits_level(worker, positions_by_kind, visits, args.timeout)
        finally:
            stop_worker(worker)
        print(f"    완료 ({time.perf_counter() - t0:.1f}s)")

    ref = by_visits[args.reference_visits]

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "purpose", "max_visits", "n_positions", "winrate_mae", "winrate_p95_err", "score_mae",
        "latency_p50_sec", "latency_p95_sec",
    ]
    purpose_label = {"auto_end": "자동 종료 수순", "final": "결과 판정"}
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for visits in args.visits:
            cmp = by_visits[visits]
            for kind in ("auto_end", "final"):
                wr_errs, score_errs, lat = [], [], []
                for pos in positions_by_kind[kind]:
                    key = (kind, pos["game_id"])
                    r, c = ref.get(key), cmp.get(key)
                    if not r or not c:
                        continue
                    if r["winrate"] is not None and c["winrate"] is not None:
                        wr_errs.append(abs(r["winrate"] - c["winrate"]))
                    if r["scoreLead"] is not None and c["scoreLead"] is not None:
                        score_errs.append(abs(r["scoreLead"] - c["scoreLead"]))
                    lat.append(c["elapsed"])
                lat_sorted = sorted(lat)
                row = {
                    "purpose": purpose_label[kind],
                    "max_visits": visits,
                    "n_positions": len(lat),
                    "winrate_mae": round(statistics.mean(wr_errs), 5) if wr_errs else None,
                    "winrate_p95_err": round(sorted(wr_errs)[int(len(wr_errs) * 0.95)], 5) if wr_errs else None,
                    "score_mae": round(statistics.mean(score_errs), 3) if score_errs else None,
                    "latency_p50_sec": round(statistics.median(lat_sorted), 4) if lat_sorted else None,
                    "latency_p95_sec": round(lat_sorted[int(len(lat_sorted) * 0.95)], 4) if lat_sorted else None,
                }
                writer.writerow(row)
                print(
                    f"  [{purpose_label[kind]}] v{visits} vs v{args.reference_visits}(기준): "
                    f"winrate_MAE={row['winrate_mae']} score_MAE={row['score_mae']} "
                    f"p50={row['latency_p50_sec']}s p95={row['latency_p95_sec']}s"
                )

    print(f"\n결과 저장: {out_path}")


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--positions", default=str(REPO_ROOT / "scripts" / "bench_data" / "positions.jsonl"))
    p.add_argument("--sample-size", type=int, default=30)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--model", default="best", help='"best" 또는 모델 파일명')
    p.add_argument("--config-path", default=default_config_path)
    p.add_argument("--reference-visits", type=int, default=800)
    p.add_argument("--visits", default="400,200,100,50")
    p.add_argument("--timeout", type=float, default=180.0)
    p.add_argument("--output", default=str(REPO_ROOT / "scripts" / "bench_results" / "test_e_endgame.csv"))
    args = p.parse_args()
    args.visits = [int(v) for v in args.visits.split(",")]
    return args


if __name__ == "__main__":
    asyncio.run(main_async(parse_args()))
