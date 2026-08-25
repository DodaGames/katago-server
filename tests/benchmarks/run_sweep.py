"""
실제 대국 표본(scripts/prepare_positions.py 출력)과 ground truth
(scripts/generate_ground_truth.py 출력)를 이용해, 후보 모델 x maxVisits
조합별로 "속도"와 "정확도"를 함께 측정하는 최종 sweep.

- 정확도: 각 판의 모든 turn을 한 번 analyzeTurns로 분석해서, ground truth
  대비 winrate/scoreLead 절대오차(MAE)를 낸다. (canRecommendEnd는 검증에 쓰지 않음)
- 속도:
  - single: 마지막 수 기준 1회 분석 지연시간 (자동종료/결과판정 시나리오)
  - batch : 한 판 전체를 독립 analyzeTurns 요청 2개를 동시에 보내는 지연시간
            (복기 시나리오, "장면 수 = 수순 수 * 2")

사용 예:
  python scripts/run_sweep.py --max-visits 50,100,200,300,500
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
sys.path.insert(0, str(SRC_ROOT))

from kg_bench_lib import (  # noqa: E402
    load_manifest, load_positions, start_worker, stop_worker,
    build_payload, time_query, extract_root_info, full_analyze_turns,
)


def load_ground_truth(path):
    gt = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        import json
        rec = json.loads(line)
        gt[str(rec["game_id"])] = {t["turn"]: t for t in rec["turns"] if t["winrate"] is not None}
    return gt


async def eval_accuracy(worker, games, ground_truth, max_visits, args):
    """각 게임 전체 turn을 1회 분석해서 ground truth 대비 오차를 모은다."""
    winrate_errs, score_errs = [], []
    for game in games:
        gt_turns = ground_truth.get(str(game["game_id"]))
        if not gt_turns:
            continue
        analyze_turns = full_analyze_turns(game["num_moves"])
        payload = build_payload(
            game["moves"], max_visits, game["board_size"], game["komi"], game["rules"],
            analyze_turns=analyze_turns, query_id=f"acc_{game['game_id']}",
        )
        _, result = await time_query(worker, payload, timeout=args.timeout)
        if not isinstance(result, list):
            continue
        for r in result:
            turn = r.get("turnNumber")
            gt = gt_turns.get(turn)
            if gt is None:
                continue
            root = r.get("rootInfo", {})
            wr, sl = root.get("winrate"), root.get("scoreLead")
            if wr is not None and gt["winrate"] is not None:
                winrate_errs.append(abs(wr - gt["winrate"]))
            if sl is not None and gt["scoreLead"] is not None:
                score_errs.append(abs(sl - gt["scoreLead"]))
    return winrate_errs, score_errs


async def eval_latency(worker, games, max_visits, args):
    single_times, batch_times, batch_scene_counts = [], [], []
    for game in games:
        single_payload = build_payload(
            game["moves"], max_visits, game["board_size"], game["komi"], game["rules"],
            analyze_turns=None, query_id=f"single_{game['game_id']}",
        )
        elapsed, _ = await time_query(worker, single_payload, timeout=args.timeout)
        single_times.append(elapsed)

        analyze_turns = full_analyze_turns(game["num_moves"])
        batch_payloads = [
            build_payload(
                game["moves"], max_visits, game["board_size"], game["komi"], game["rules"],
                analyze_turns=analyze_turns, query_id=f"batch_{game['game_id']}_{k}",
            )
            for k in range(args.scenes_multiplier)
        ]
        start = time.perf_counter()
        await asyncio.gather(*[worker.analyze(p, timeout=args.timeout) for p in batch_payloads])
        batch_times.append(time.perf_counter() - start)
        batch_scene_counts.append(len(analyze_turns) * args.scenes_multiplier)

    return single_times, batch_times, batch_scene_counts


async def run_one_model(model_cfg, games, ground_truth, args, csv_writer):
    name = model_cfg["name"]

    for max_visits in args.max_visits:
        # 중요: maxVisits 조합마다 워커를 새로 띄워 nnCache를 완전히 비운 상태에서
        # 시작한다. 같은 게임 포지션을 accuracy 패스로 먼저 훑고 나서 곧바로
        # latency(batch)를 재는 식으로 하나의 워커를 재사용하면, latency 측정이
        # 사실상 "이미 계산된 값 재조회"가 되어 실제보다 5~10배 빠르게 나오는
        # 캐시 오염 버그를 겪었다. latency를 먼저(콜드 상태) 재고, accuracy는
        # 그 뒤에(캐시 상태가 결과값에 영향 없으므로 순서 무관) 측정한다.
        print(f"\n=== [{name}] loading (maxVisits={max_visits}, 캐시 오염 방지를 위해 조합마다 재시작) ===")
        worker = start_worker(model_cfg)
        warmup = build_payload(games[0]["moves"][:2], 50, games[0]["board_size"], games[0]["komi"],
                                games[0]["rules"], query_id="warmup")
        await time_query(worker, warmup, timeout=120)

        try:
            t0 = time.perf_counter()
            combo_start_ts = time.time()
            single_times, batch_times, scene_counts = await eval_latency(worker, games, max_visits, args)
            latency_end_ts = time.time()
            wr_errs, score_errs = await eval_accuracy(worker, games, ground_truth, max_visits, args)
            wall = time.perf_counter() - t0

            row = {
                "model": name,
                "max_visits": max_visits,
                "n_games": len(games),
                "combo_start_ts": round(combo_start_ts, 3),
                "latency_end_ts": round(latency_end_ts, 3),
                "visits_per_sec": round(max_visits / (sum(batch_times) / sum(scene_counts)), 2),
                "winrate_mae": round(statistics.mean(wr_errs), 5) if wr_errs else None,
                "winrate_p95_err": round(sorted(wr_errs)[int(len(wr_errs) * 0.95)], 5) if wr_errs else None,
                "score_mae": round(statistics.mean(score_errs), 4) if score_errs else None,
                "single_latency_p50_sec": round(statistics.median(single_times), 4),
                "single_latency_p95_sec": round(sorted(single_times)[int(len(single_times) * 0.95)], 4),
                "batch_latency_p50_sec": round(statistics.median(batch_times), 4),
                "batch_sec_per_scene": round(sum(batch_times) / sum(scene_counts), 5),
                "combo_wall_sec": round(wall, 1),
            }
            csv_writer.writerow(row)
            print(
                f"  visits={max_visits:5d}  winrate_MAE={row['winrate_mae']}  score_MAE={row['score_mae']}  "
                f"single_p50={row['single_latency_p50_sec']}s  batch_p50={row['batch_latency_p50_sec']}s  "
                f"({row['batch_sec_per_scene']}s/scene)  [{row['combo_wall_sec']}s]"
            )
        finally:
            stop_worker(worker)

    print(f"=== [{name}] done ===")


async def main_async(args):
    manifest = load_manifest(args.manifest, args.models.split(",") if args.models else None)
    games = load_positions(args.positions)
    ground_truth = load_ground_truth(args.ground_truth)
    games = [g for g in games if str(g["game_id"]) in ground_truth]
    print(f"sweep 대상: 모델 {len(manifest)}개 x maxVisits {args.max_visits} x 대국 {len(games)}판 "
          f"(ground truth 있는 판만)")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "model", "max_visits", "n_games", "combo_start_ts", "latency_end_ts", "visits_per_sec",
        "winrate_mae", "winrate_p95_err", "score_mae",
        "single_latency_p50_sec", "single_latency_p95_sec",
        "batch_latency_p50_sec", "batch_sec_per_scene", "combo_wall_sec",
    ]
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for model_cfg in manifest:
            await run_one_model(model_cfg, games, ground_truth, args, writer)
            f.flush()

    print(f"\n결과 저장: {out_path}")


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--manifest", default=str(THIS_DIR / "bench_models.json"))
    p.add_argument("--models", default=None)
    p.add_argument("--max-visits", default="50,100,200,300,500")
    p.add_argument("--positions", default=str(THIS_DIR / "bench_data" / "positions.jsonl"))
    p.add_argument("--ground-truth", default=str(THIS_DIR / "bench_data" / "ground_truth.jsonl"))
    p.add_argument("--scenes-multiplier", type=int, default=2)
    p.add_argument("--timeout", type=float, default=180.0)
    p.add_argument("--output", default=str(THIS_DIR / "bench_results" / "sweep.csv"))
    args = p.parse_args()
    args.max_visits = [int(v) for v in args.max_visits.split(",")]
    return args


if __name__ == "__main__":
    asyncio.run(main_async(parse_args()))
