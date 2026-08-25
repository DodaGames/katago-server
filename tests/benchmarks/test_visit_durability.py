"""
테스트 C(요청서 §2-C, ⭐): 모델별 "바닥 visits" 내구성.

같은 20게임(scripts/bench_data/test_c_games.jsonl)을 모델별로
visits = 800(기준) / 400 / 200 / 100 로 각각 콜드 상태에서 전체 analyzeTurns 분석하고,
800visits 결과를 그 모델 자신의 정답(reference)으로 삼아 400/200/100 결과와 비교한다.
(모델 간 비교가 아니라, "같은 모델이 visits를 낮췄을 때 판정이 안 뒤집히는가"를 보는
자기 자신 대비 내구성 테스트라는 점이 run_sweep.py의 ground-truth 정확도 측정과 다르다.)

비교 지표 (요청서 표 그대로):
  - 실수 감지 위치 일치율: 각 수를 "악수/대악수"로 볼지 여부(그레이드 파이프라인 최종
    등급 grade_final 기준)가 기준(800v) 대비 얼마나 일치하는지 (일치 move 수 / 전체 move 수)
  - 승률 그래프 방향 - 부호가 뒤집힌 구간 수: turn별 root winrate(원본, Black 기준, 착수자
    관계없이 그래프에 표시되는 값 그대로) 추이에서 인접 turn 간 증감 부호가 기준과
    달라지는 구간 수 (epsilon=0.5%p 이내 변화는 "평탄"으로 보고 카운트에서 제외)
  - 추천 수(최선수) top-3 포함률: 기준(800v)이 뽑은 최선수(top-1, order=0)가 비교
    visits의 top-3 추천 후보(order 0~2) 안에 들어있는 비율
  - 집 차이 추정 MAE: turn별 root scoreLead(원본, 착수자 무관 보드 상태값) 절대오차 평균

등급 파이프라인(가중치/등급경계/강등)은 scripts/extract_review_delta.py와 완전히
동일한 함수를 재사용해 일관성을 유지한다.

사용 예:
  python scripts/test_visit_durability.py --models b18c384nbt,b28c512nbt
"""

import argparse
import asyncio
import csv
import json
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_ROOT = REPO_ROOT / "src"
THIS_DIR = Path(__file__).resolve().parent
for p in (SRC_ROOT, THIS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from kg_bench_lib import (  # noqa: E402
    load_manifest, load_positions, start_worker, stop_worker,
    build_payload, time_query, full_analyze_turns,
)
from utils.go_board import compute_empty_counts_before_each_move  # noqa: E402
import extract_review_delta as erd  # noqa: E402

WINRATE_FLAT_EPS = 0.005  # 0.5%p 이내 변화는 "평탄"으로 취급 (부호반전 카운트에서 제외)


async def analyze_game(worker, game, max_visits, timeout, gate_k):
    """extract_review_delta.py와 동일한 등급 파이프라인으로 game 전체를 분석해서
    move별 레코드 + turn별 winrate/scoreLead 궤적을 반환한다."""
    moves = game["moves"]
    num_moves = game["num_moves"]
    analyze_turns = full_analyze_turns(num_moves)
    payload = build_payload(
        moves, max_visits, game["board_size"], game["komi"], game["rules"],
        analyze_turns=analyze_turns, query_id=f"c_{game['game_id']}",
    )
    _, result = await time_query(worker, payload, timeout=timeout)
    if not isinstance(result, list):
        return None, f"쿼리 실패: {result}"

    by_turn = {r["turnNumber"]: r for r in result}
    missing = [t for t in analyze_turns if t not in by_turn]
    if missing:
        return None, f"응답 누락 turns={missing[:5]}"

    empty_counts_before = compute_empty_counts_before_each_move(moves, game["board_size"])

    winrate_traj = [by_turn[t]["rootInfo"].get("winrate") for t in analyze_turns]
    score_traj = [by_turn[t]["rootInfo"].get("scoreLead") for t in analyze_turns]

    move_records = []
    for i in range(num_moves):
        color, actual_coord = moves[i]
        flip = (color == "W")

        def wr(raw_winrate):
            pct = raw_winrate * 100
            return 100 - pct if flip else pct

        def sc(raw_score_lead):
            return -raw_score_lead if flip else raw_score_lead

        root_before = by_turn[i]["rootInfo"]
        root_after = by_turn[i + 1]["rootInfo"]
        move_infos = by_turn[i].get("moveInfos", [])
        sorted_infos = sorted(move_infos, key=lambda m: m.get("order", 10**9))
        top3_coords = [m["move"] for m in sorted_infos[:3]]
        top1 = sorted_infos[0] if sorted_infos else None

        winrate_before = wr(root_before["winrate"])
        score_before = sc(root_before["scoreLead"])

        if top1 is not None:
            winrate_top1 = wr(top1["winrate"])
            score_top1 = sc(top1["scoreLead"])
        else:
            winrate_top1 = winrate_before
            score_top1 = score_before

        winrate_actual = wr(root_after["winrate"])
        score_actual = sc(root_after["scoreLead"])

        delta_raw = winrate_top1 - winrate_actual
        weight = erd.compute_weight(winrate_before)
        delta_weighted = delta_raw * weight
        score_delta = score_top1 - score_actual

        empty_count = empty_counts_before[i]
        gate_passed = not (abs(score_before) > empty_count * gate_k)

        grade_winrate = erd.grade_from_delta_weighted(delta_weighted)
        if grade_winrate in erd.DOWNGRADE_ELIGIBLE:
            downgrade_steps = erd.downgrade_steps_from_score_delta(score_delta)
        else:
            downgrade_steps = 0
        grade_final = erd.apply_downgrade(grade_winrate, downgrade_steps)

        move_records.append({
            "turn": i,
            "gate_passed": gate_passed,
            "grade_final": grade_final,
            "is_mistake": grade_final in ("악수", "대악수"),
            "top1_coord": top1["move"] if top1 else None,
            "top3_coords": top3_coords,
        })

    return {"move_records": move_records, "winrate_traj": winrate_traj, "score_traj": score_traj}, None


async def run_all_games(worker, games, max_visits, timeout, gate_k):
    out = {}
    for i, game in enumerate(games):
        data, err = await analyze_game(worker, game, max_visits, timeout, gate_k)
        if err is not None:
            print(f"    [{i+1}/{len(games)}] game={game['game_id']} FAILED: {err}")
            continue
        out[game["game_id"]] = data
    return out


def compare_to_reference(ref_by_game, cmp_by_game, gate_k, only_gate_passed):
    """ref(800visits)와 cmp(낮은 visits)를 game_id 기준으로 비교해 4개 지표를 낸다."""
    mistake_match_count = 0
    mistake_total = 0
    top3_included_count = 0
    top1_exact_count = 0
    top_total = 0
    score_abs_errs = []
    sign_flips_per_game = []

    for game_id, ref in ref_by_game.items():
        cmp = cmp_by_game.get(game_id)
        if cmp is None:
            continue

        ref_moves = ref["move_records"]
        cmp_moves = cmp["move_records"]
        for rm, cm in zip(ref_moves, cmp_moves):
            if only_gate_passed and not (rm["gate_passed"] and cm["gate_passed"]):
                continue
            mistake_total += 1
            if rm["is_mistake"] == cm["is_mistake"]:
                mistake_match_count += 1

            top_total += 1
            if rm["top1_coord"] is not None and rm["top1_coord"] in cm["top3_coords"]:
                top3_included_count += 1
            if rm["top1_coord"] is not None and rm["top1_coord"] == cm["top1_coord"]:
                top1_exact_count += 1

        ref_score = ref["score_traj"]
        cmp_score = cmp["score_traj"]
        for rs, cs in zip(ref_score, cmp_score):
            if rs is not None and cs is not None:
                score_abs_errs.append(abs(rs - cs))

        ref_wr = ref["winrate_traj"]
        cmp_wr = cmp["winrate_traj"]
        flips = 0
        for t in range(len(ref_wr) - 1):
            if ref_wr[t] is None or ref_wr[t + 1] is None or cmp_wr[t] is None or cmp_wr[t + 1] is None:
                continue
            d_ref = ref_wr[t + 1] - ref_wr[t]
            d_cmp = cmp_wr[t + 1] - cmp_wr[t]
            if abs(d_ref) < WINRATE_FLAT_EPS or abs(d_cmp) < WINRATE_FLAT_EPS:
                continue
            if (d_ref > 0) != (d_cmp > 0):
                flips += 1
        sign_flips_per_game.append(flips)

    return {
        "mistake_match_pct": round(100 * mistake_match_count / mistake_total, 2) if mistake_total else None,
        "n_moves_compared": mistake_total,
        "winrate_sign_flips_total": sum(sign_flips_per_game),
        "winrate_sign_flips_avg_per_game": round(statistics.mean(sign_flips_per_game), 3) if sign_flips_per_game else None,
        "top3_inclusion_pct": round(100 * top3_included_count / top_total, 2) if top_total else None,
        "top1_exact_pct": round(100 * top1_exact_count / top_total, 2) if top_total else None,
        "score_mae": round(statistics.mean(score_abs_errs), 3) if score_abs_errs else None,
    }


async def run_one_model(model_cfg, games, args, csv_writer, raw_out_dir: Path):
    name = model_cfg["name"]
    visits_levels = [args.reference_visits] + args.compare_visits
    by_visits = {}

    for visits in visits_levels:
        print(f"\n=== [{name}] visits={visits} (콜드 재시작, {len(games)}게임) ===")
        worker = start_worker(model_cfg)
        warmup = build_payload(games[0]["moves"][:2], 50, games[0]["board_size"], games[0]["komi"],
                                games[0]["rules"], query_id="warmup")
        await time_query(worker, warmup, timeout=120)
        t0 = time.perf_counter()
        try:
            by_visits[visits] = await run_all_games(worker, games, visits, args.timeout, args.gate_k)
        finally:
            stop_worker(worker)
        print(f"    완료 ({time.perf_counter() - t0:.1f}s, {len(by_visits[visits])}/{len(games)}게임 성공)")

    raw_path = raw_out_dir / f"{name}_raw.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(by_visits, f, ensure_ascii=False)

    ref = by_visits[args.reference_visits]
    for visits in args.compare_visits:
        cmp = by_visits.get(visits, {})
        metrics = compare_to_reference(ref, cmp, args.gate_k, args.only_gate_passed)
        row = {"model": name, "reference_visits": args.reference_visits, "compare_visits": visits, **metrics}
        csv_writer.writerow(row)
        print(
            f"  [{name}] v{visits} vs v{args.reference_visits}: "
            f"실수일치율={metrics['mistake_match_pct']}%  승률부호반전={metrics['winrate_sign_flips_total']}건"
            f"(avg {metrics['winrate_sign_flips_avg_per_game']}/game)  "
            f"top3포함률={metrics['top3_inclusion_pct']}%  집MAE={metrics['score_mae']}"
        )


async def main_async(args):
    manifest = load_manifest(args.manifest, args.models.split(",") if args.models else None)
    games = load_positions(args.games)
    print(f"Test C 대상: 모델 {len(manifest)}개 x visits {[args.reference_visits] + args.compare_visits} x 대국 {len(games)}판")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    raw_out_dir = out_path.parent / "test_c_raw"

    fieldnames = [
        "model", "reference_visits", "compare_visits",
        "mistake_match_pct", "n_moves_compared",
        "winrate_sign_flips_total", "winrate_sign_flips_avg_per_game",
        "top3_inclusion_pct", "top1_exact_pct", "score_mae",
    ]
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for model_cfg in manifest:
            await run_one_model(model_cfg, games, args, writer, raw_out_dir)
            f.flush()

    print(f"\n결과 저장: {out_path}")


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--manifest", default=str(THIS_DIR / "bench_models.json"))
    p.add_argument("--models", default=None)
    p.add_argument("--games", default=str(THIS_DIR / "bench_data" / "test_c_games.jsonl"))
    p.add_argument("--reference-visits", type=int, default=800)
    p.add_argument("--compare-visits", default="400,200,100")
    p.add_argument("--gate-k", type=float, default=2.5)
    p.add_argument("--only-gate-passed", action="store_true",
                    help="유효성 게이트를 통과한 수만 비교 대상에 포함 (기본: 전체 포함)")
    p.add_argument("--timeout", type=float, default=240.0)
    p.add_argument("--output", default=str(THIS_DIR / "bench_results" / "test_c_durability.csv"))
    args = p.parse_args()
    args.compare_visits = [int(v) for v in args.compare_visits.split(",")]
    return args


if __name__ == "__main__":
    asyncio.run(main_async(parse_args()))
