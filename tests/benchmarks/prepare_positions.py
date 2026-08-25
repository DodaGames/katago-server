"""
GameRecord.csv(실제 대국 로그)에서 벤치마크/정확도 검증용 포지션 셋을 추출한다.

- 보드 크기(SZ)별 분포를 보존하는 층화추출(stratified sampling)로 표본을 뽑는다
  (전체 448판 그대로 sweep 돌리면 무거운 모델 기준 수 시간이 걸려 샘플링).
- 각 판의 canRecommendEnd(자동종료 추천 라벨)도 함께 보존해서, 추후 자동종료
  판단 정확도 검증에 참고 신호로 쓸 수 있게 한다.

사용 예:
  python scripts/prepare_positions.py --sample-size 50 --output scripts/bench_data/positions.jsonl
"""

import argparse
import csv
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_ROOT = REPO_ROOT / "src"
THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC_ROOT))

from utils.sgf_parser import parse_sgf_game  # noqa: E402

csv.field_size_limit(10_000_000)


def load_games(csv_path: Path):
    games = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sgf = row.get("sgfContent") or ""
            parsed = parse_sgf_game(sgf)
            if not parsed or len(parsed["moves"]) < 2:
                continue
            can_recommend_end = {}
            raw = row.get("canRecommendEnd")
            if raw:
                try:
                    can_recommend_end = json.loads(raw)
                except json.JSONDecodeError:
                    can_recommend_end = {}
            games.append({
                "game_id": row.get("id"),
                "title": row.get("title"),
                "board_size": parsed["board_size"],
                "komi": parsed["komi"],
                "moves": parsed["moves"],
                "can_recommend_end": can_recommend_end,
            })
    return games


def stratified_sample(games, sample_size, seed):
    rng = random.Random(seed)
    by_size = defaultdict(list)
    for g in games:
        by_size[g["board_size"]].append(g)

    total = len(games)
    sampled = []
    for size, bucket in sorted(by_size.items()):
        rng.shuffle(bucket)
        # 원본 비율을 보존하되, 표본이 있는 크기는 최소 1개는 포함
        quota = max(1, round(sample_size * len(bucket) / total))
        sampled.extend(bucket[:quota])

    rng.shuffle(sampled)
    return sampled[:sample_size] if len(sampled) > sample_size else sampled


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--csv", default=str(REPO_ROOT / "tests" / "data" / "GameRecord.csv"))
    p.add_argument("--sample-size", type=int, default=50)
    p.add_argument("--rules", default="korean")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--output", default=str(THIS_DIR / "bench_data" / "positions.jsonl"))
    args = p.parse_args()

    games = load_games(Path(args.csv))
    print(f"파싱된 유효 대국: {len(games)}판")

    size_dist = defaultdict(int)
    for g in games:
        size_dist[g["board_size"]] += 1
    print("보드 크기 분포:", dict(sorted(size_dist.items())))

    sampled = stratified_sample(games, args.sample_size, args.seed)
    print(f"샘플링된 대국: {len(sampled)}판")
    sampled_dist = defaultdict(int)
    for g in sampled:
        sampled_dist[g["board_size"]] += 1
    print("샘플 보드 크기 분포:", dict(sorted(sampled_dist.items())))

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for g in sampled:
            record = {
                "game_id": g["game_id"],
                "title": g["title"],
                "board_size": g["board_size"],
                "komi": g["komi"],
                "rules": args.rules,
                "moves": g["moves"],
                "num_moves": len(g["moves"]),
                "can_recommend_end": g["can_recommend_end"],
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"저장 완료: {out_path}")


if __name__ == "__main__":
    main()
