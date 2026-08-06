"""
Test A의 "추가 확인" 두 항목을 재는 보조 스크립트 (run_sweep.py로는 재지 않는 것들).

1) numAnalysisThreads / nnMaxBatchSize 튜닝 전후 차이
   configs/rtx_desktop.cfg(기본) vs configs/rtx_desktop_tuned.cfg
   (numAnalysisThreads 32->48, nnMaxBatchSize 256->384) 로 같은 모델/visits/게임을
   콜드 상태에서 반복 측정해 batch_sec_per_scene을 비교한다.

2) 배치 효율: 장면 1개씩 순차 요청 vs 한 번에 배치로 보낼 때
   같은 게임의 전체 analyzeTurns 목록을, (a) turn 하나씩 개별 쿼리로 순차 전송한
   총 시간과 (b) analyzeTurns 배열 하나로 한 번에 보낸 시간을 비교한다.

가장 무거운 test_a 게임(최다 수순) 1판, 기본 모델(best/b18c384nbt), maxVisits=200을
대표값으로 사용한다(전체 조합을 다 돌리면 시간이 배가 되므로 대표 조합만 확인).

사용 예:
  python scripts/test_a_extra.py
"""

import argparse
import asyncio
import csv
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from kg_bench_lib import (  # noqa: E402
    load_manifest, load_positions, start_worker, stop_worker,
    build_payload, time_query, full_analyze_turns,
)


async def warmup(worker, game):
    payload = build_payload(game["moves"][:2], 50, game["board_size"], game["komi"],
                             game["rules"], query_id="warmup")
    await time_query(worker, payload, timeout=120)


async def measure_thread_tuning(model_cfg, game, max_visits, timeout, scenes_multiplier=2):
    """같은 (model, maxVisits, game)을 기본/튜닝 config로 각각 콜드 측정."""
    rows = []
    for label, config_path in [
        ("baseline (numAnalysisThreads=32, nnMaxBatchSize=256)", "configs/rtx_desktop.cfg"),
        ("tuned (numAnalysisThreads=48, nnMaxBatchSize=384)", "configs/rtx_desktop_tuned.cfg"),
    ]:
        cfg = dict(model_cfg, config_path=config_path)
        print(f"\n=== [thread-tuning] {label} ===")
        worker = start_worker(cfg)
        try:
            await warmup(worker, game)
            analyze_turns = full_analyze_turns(game["num_moves"])
            payloads = [
                build_payload(game["moves"], max_visits, game["board_size"], game["komi"],
                               game["rules"], analyze_turns=analyze_turns, query_id=f"tt_{k}")
                for k in range(scenes_multiplier)
            ]
            start = time.perf_counter()
            await asyncio.gather(*[worker.analyze(p, timeout=timeout) for p in payloads])
            elapsed = time.perf_counter() - start
            n_scenes = len(analyze_turns) * scenes_multiplier
            row = {
                "variant": label,
                "config_path": config_path,
                "max_visits": max_visits,
                "n_scenes": n_scenes,
                "batch_latency_sec": round(elapsed, 3),
                "batch_sec_per_scene": round(elapsed / n_scenes, 5),
            }
            rows.append(row)
            print(f"  {label}: {elapsed:.2f}s for {n_scenes} scenes ({elapsed/n_scenes:.5f}s/scene)")
        finally:
            stop_worker(worker)
    return rows


async def measure_batch_efficiency(model_cfg, game, max_visits, timeout):
    """같은 게임의 turn 목록을 (a) 1개씩 순차 vs (b) 한 번에 배치로 분석."""
    print("\n=== [batch-efficiency] worker 시작 (콜드) ===")
    worker = start_worker(model_cfg)
    rows = []
    try:
        await warmup(worker, game)
        analyze_turns = full_analyze_turns(game["num_moves"])

        # (a) 1개씩 순차 (analyzeTurns를 turn 1개짜리로 매번 새 요청)
        print(f"  (a) 장면 {len(analyze_turns)}개 1개씩 순차 전송...")
        start = time.perf_counter()
        for i, turn in enumerate(analyze_turns):
            payload = build_payload(
                game["moves"], max_visits, game["board_size"], game["komi"], game["rules"],
                analyze_turns=[turn], query_id=f"seq_{i}",
            )
            await time_query(worker, payload, timeout=timeout)
        sequential_elapsed = time.perf_counter() - start
        print(f"      {sequential_elapsed:.2f}s ({sequential_elapsed/len(analyze_turns):.5f}s/scene)")

        # 워커 재시작 (a)의 nnCache가 (b) 결과를 오염시키지 않도록
        stop_worker(worker)
        print("\n=== [batch-efficiency] worker 재시작 (콜드) ===")
        worker = start_worker(model_cfg)
        await warmup(worker, game)

        # (b) 한 번에 배치
        print(f"  (b) 장면 {len(analyze_turns)}개 한 번에 배치 전송...")
        payload = build_payload(
            game["moves"], max_visits, game["board_size"], game["komi"], game["rules"],
            analyze_turns=analyze_turns, query_id="batch_all",
        )
        start = time.perf_counter()
        await time_query(worker, payload, timeout=timeout)
        batch_elapsed = time.perf_counter() - start
        print(f"      {batch_elapsed:.2f}s ({batch_elapsed/len(analyze_turns):.5f}s/scene)")

        rows.append({
            "mode": "individual (1 scene per request, sequential)",
            "n_scenes": len(analyze_turns),
            "total_sec": round(sequential_elapsed, 3),
            "sec_per_scene": round(sequential_elapsed / len(analyze_turns), 5),
        })
        rows.append({
            "mode": "batched (all scenes in 1 analyzeTurns request)",
            "n_scenes": len(analyze_turns),
            "total_sec": round(batch_elapsed, 3),
            "sec_per_scene": round(batch_elapsed / len(analyze_turns), 5),
        })
        speedup = sequential_elapsed / batch_elapsed if batch_elapsed > 0 else None
        print(f"\n  배치 처리량 개선: {speedup:.1f}x" if speedup else "")
    finally:
        stop_worker(worker)
    return rows


async def main_async(args):
    manifest = load_manifest(args.manifest, [args.model])
    if not manifest:
        raise SystemExit(f"manifest에서 모델 '{args.model}'을 찾을 수 없습니다")
    model_cfg = manifest[0]

    games = load_positions(args.games)
    game = max(games, key=lambda g: g["num_moves"])
    print(f"대표 게임: game_id={game['game_id']} board={game['board_size']} moves={game['num_moves']}")
    print(f"대표 모델: {args.model}, maxVisits={args.max_visits}")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    tuning_rows = await measure_thread_tuning(model_cfg, game, args.max_visits, args.timeout)
    efficiency_rows = await measure_batch_efficiency(model_cfg, game, args.max_visits, args.timeout)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "model": args.model,
            "max_visits": args.max_visits,
            "game_id": game["game_id"],
            "game_num_moves": game["num_moves"],
            "thread_tuning": tuning_rows,
            "batch_efficiency": efficiency_rows,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n결과 저장: {out_path}")


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--manifest", default=str(REPO_ROOT / "scripts" / "bench_models.json"))
    p.add_argument("--model", default="b18c384nbt")
    p.add_argument("--max-visits", type=int, default=200)
    p.add_argument("--games", default=str(REPO_ROOT / "scripts" / "bench_data" / "test_a_games.jsonl"))
    p.add_argument("--timeout", type=float, default=180.0)
    p.add_argument("--output", default=str(REPO_ROOT / "scripts" / "bench_results" / "test_a_extra.json"))
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main_async(parse_args()))
