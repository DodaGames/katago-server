"""
scripts/prepare_positions.py로 뽑은 실제 대국 표본에 대해, 가장 강한 모델을
매우 높은 maxVisits로 돌려 각 턴의 winrate/scoreLead를 "정답(ground truth)"으로
저장한다. scripts/run_sweep.py가 이 값을 기준으로 각 (모델, maxVisits) 조합의
정확도 오차를 계산하는 데 사용한다.

사용 예:
  python scripts/generate_ground_truth.py \
      --positions scripts/bench_data/positions.jsonl \
      --model b28c512nbt --max-visits 1000 \
      --output scripts/bench_data/ground_truth.jsonl
"""

import argparse
import asyncio
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


async def main_async(args):
    manifest = load_manifest(args.manifest, [args.model])
    if not manifest:
        raise SystemExit(f"manifest에서 모델 '{args.model}'을 찾을 수 없습니다")
    model_cfg = manifest[0]

    games = load_positions(args.positions)
    print(f"{len(games)}개 대국에 대해 ground truth 생성 (model={args.model}, maxVisits={args.max_visits})")

    worker = start_worker(model_cfg)
    warmup = build_payload(games[0]["moves"][:2], 50, games[0]["board_size"], games[0]["komi"],
                            games[0]["rules"], query_id="warmup")
    await time_query(worker, warmup, timeout=120)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(out_path, "w", encoding="utf-8") as f:
            for i, game in enumerate(games):
                analyze_turns = full_analyze_turns(game["num_moves"])
                payload = build_payload(
                    game["moves"], args.max_visits, game["board_size"], game["komi"], game["rules"],
                    analyze_turns=analyze_turns, query_id=f"gt_{game['game_id']}",
                )
                start = time.perf_counter()
                elapsed, result = await time_query(worker, payload, timeout=args.timeout)
                if not isinstance(result, list):
                    print(f"  [{i+1}/{len(games)}] game={game['game_id']} FAILED: {result}")
                    continue

                turns = []
                for r in result:
                    root = r.get("rootInfo", {})
                    turns.append({
                        "turn": r.get("turnNumber"),
                        "winrate": root.get("winrate"),
                        "scoreLead": root.get("scoreLead"),
                    })
                record = {
                    "game_id": game["game_id"],
                    "board_size": game["board_size"],
                    "num_moves": game["num_moves"],
                    "turns": turns,
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                f.flush()
                print(f"  [{i+1}/{len(games)}] game={game['game_id']} "
                      f"({game['board_size']}x{game['board_size']}, {game['num_moves']}수) {elapsed:.2f}s")
    finally:
        stop_worker(worker)

    print(f"저장 완료: {out_path}")


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--manifest", default=str(REPO_ROOT / "scripts" / "bench_models.json"))
    p.add_argument("--model", default="b28c512nbt")
    p.add_argument("--max-visits", type=int, default=1000)
    p.add_argument("--positions", default=str(REPO_ROOT / "scripts" / "bench_data" / "positions.jsonl"))
    p.add_argument("--timeout", type=float, default=300.0)
    p.add_argument("--output", default=str(REPO_ROOT / "scripts" / "bench_data" / "ground_truth.jsonl"))
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main_async(parse_args()))
