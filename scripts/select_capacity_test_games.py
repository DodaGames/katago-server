"""
temp/review-ai-capacity-test-request.md 테스트 A/C용 기보 셋을 기존
scripts/bench_data/positions.jsonl(50판, 보드크기 층화추출) 에서 뽑아낸다.

주의(사용자 확인 완료): 요청서는 "150수 내외 실제 스테이지 기보"를 전제하지만,
실제 GameRecord.csv(448판)는 대부분 5~9줄 소형 보드의 부스/테스트 세션이고
최장 수순도 126수(중앙값 33수)라 150수 게임이나 "스테이지"(고정 오프닝) 개념
자체가 없다. 그래서 실제 최장 기보로 대체한다.

- Test A(10판): "짧은/긴 판 섞어서"라는 요청 취지를 살리기 위해, 수순 수 기준
  최상위 6판(무거운 판 위주로 처리시간 상한을 재기 위함) + 나머지를 수순 분포
  4분위에서 고르게 1판씩 뽑아 총 10판을 구성.
- Test C(20판): 정확도 비교가 목적이라 보드크기 분포를 보존해야 하므로,
  prepare_positions.py와 동일한 층화추출 방식으로 positions.jsonl(50판)에서
  20판을 재추출한다.

둘 다 시드 고정으로 재현 가능.
"""

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def load_positions(path):
    games = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                games.append(json.loads(line))
    return games


def select_test_a(games, n, seed):
    by_moves = sorted(games, key=lambda g: g["num_moves"], reverse=True)
    longest = by_moves[:6]
    chosen_ids = {g["game_id"] for g in longest}

    remaining = [g for g in by_moves if g["game_id"] not in chosen_ids]
    remaining_sorted = sorted(remaining, key=lambda g: g["num_moves"])
    rng = random.Random(seed)
    need = n - len(longest)
    extra = []
    if need > 0 and remaining_sorted:
        # 수순 분포를 4분위(또는 need분위)로 나눠 한 판씩 뽑아 "짧은/긴 혼합" 취지를 살림
        bucket_count = need
        size = max(1, len(remaining_sorted) // bucket_count)
        for i in range(bucket_count):
            start = i * size
            end = start + size if i < bucket_count - 1 else len(remaining_sorted)
            bucket = remaining_sorted[start:end]
            if not bucket:
                continue
            extra.append(rng.choice(bucket))

    selected = longest + extra
    # 중복 방지
    seen = set()
    result = []
    for g in selected:
        if g["game_id"] not in seen:
            seen.add(g["game_id"])
            result.append(g)
    return sorted(result, key=lambda g: g["num_moves"], reverse=True)[:n]


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


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--positions", default=str(REPO_ROOT / "scripts" / "bench_data" / "positions.jsonl"))
    p.add_argument("--test-a-size", type=int, default=10)
    p.add_argument("--test-c-size", type=int, default=20)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--test-a-output", default=str(REPO_ROOT / "scripts" / "bench_data" / "test_a_games.jsonl"))
    p.add_argument("--test-c-output", default=str(REPO_ROOT / "scripts" / "bench_data" / "test_c_games.jsonl"))
    args = p.parse_args()

    games = load_positions(args.positions)
    print(f"positions.jsonl: {len(games)}판 로드")

    test_a = select_test_a(games, args.test_a_size, args.seed)
    with open(args.test_a_output, "w", encoding="utf-8") as f:
        for g in test_a:
            f.write(json.dumps(g, ensure_ascii=False) + "\n")
    print(f"Test A: {len(test_a)}판 -> {args.test_a_output}")
    for g in test_a:
        print(f"  game_id={g['game_id']} board={g['board_size']} moves={g['num_moves']}")

    test_c = stratified_sample(games, args.test_c_size, args.seed)
    with open(args.test_c_output, "w", encoding="utf-8") as f:
        for g in test_c:
            f.write(json.dumps(g, ensure_ascii=False) + "\n")
    print(f"\nTest C: {len(test_c)}판 -> {args.test_c_output}")
    size_dist = defaultdict(int)
    for g in test_c:
        size_dist[g["board_size"]] += 1
    print(f"  보드크기 분포: {dict(sorted(size_dist.items()))}")


if __name__ == "__main__":
    main()
