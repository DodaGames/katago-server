"""
Doda_복기_Delta분포_데이터추출_요청.md 요구사항에 따른 일회성 배치 추출 스크립트.

GameRecord.csv의 각 판을 KataGo(best 모델, 고정 maxVisits)로 재분석해서,
수(move) 단위 승률/집 Delta 및 등급 판정 파이프라인(①유효성 게이트 → ③승률 기본
등급 → ④집 Delta 강등) 중간 산출물을 CSV로 남긴다. 앱 코드는 건드리지 않는다.

komi는 각 판의 SGF에 기록된 원래 값을 그대로 사용한다(요청서는 komi=0 일괄 적용을
언급했으나, 실제 47/448개 판이 komi=6.5로 기록돼 있어 원래 조건을 보존하는 쪽으로
사용자와 합의함). visits/모델/rules는 모든 판에 동일하게 적용한다.

사용 예:
  python scripts/extract_review_delta.py \\
      --input GameRecord.csv \\
      --output scripts/output/review_delta.csv \\
      --model-id best --max-visits 200 --rules korean
"""

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_ROOT = REPO_ROOT / "src"
THIS_DIR = Path(__file__).resolve().parent
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from utils.sgf_parser import parse_sgf_game  # noqa: E402
from utils.go_board import compute_empty_counts_before_each_move  # noqa: E402
from analysis.config import SERVING_MODELS  # noqa: E402

csv.field_size_limit(10**7)

GRADE_ORDER = ["훌륭한 수", "호수", "정수", "아쉬운 수", "악수", "대악수"]
GRADE_THRESHOLDS = [3, 6, 10, 20, 30]  # 마지막 구간(대악수)은 상한 없음
DOWNGRADE_ELIGIBLE = {"정수", "아쉬운 수", "악수", "대악수"}

CSV_COLUMNS = [
    "game_id", "user_id", "player", "board_size", "move_number",
    "winrate_before", "winrate_top1", "winrate_actual",
    "delta_raw", "weight", "delta_weighted",
    "score_before", "score_top1", "score_actual", "score_delta",
    "empty_count", "gate_passed",
    "grade_winrate", "downgrade_steps", "grade_final",
    "is_top1_move", "time_spent_ms",
]


def grade_from_delta_weighted(delta_weighted):
    for threshold, name in zip(GRADE_THRESHOLDS, GRADE_ORDER):
        if delta_weighted < threshold:
            return name
    return GRADE_ORDER[-1]


def downgrade_steps_from_score_delta(score_delta):
    if score_delta < 8:
        return 0
    if score_delta < 12:
        return 1
    return 2


def apply_downgrade(grade_winrate, steps):
    if steps <= 0:
        return grade_winrate
    idx = GRADE_ORDER.index(grade_winrate)
    idx = min(idx + steps, len(GRADE_ORDER) - 1)
    return GRADE_ORDER[idx]


def compute_weight(winrate_before):
    d = abs(winrate_before - 50)
    weight = 1.5 - 0.7 * (d / 50)
    if winrate_before < 50:
        weight *= 0.9
    return weight


def r2(x):
    return round(x, 2)


def is_ai_name(name):
    return (name or "").startswith("AI")


def load_time_records(raw):
    if not raw or not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def build_payload(model_query_id, moves, board_size, komi, rules, max_visits, analyze_turns):
    return {
        "id": model_query_id,
        "moves": moves,
        "rules": rules,
        "komi": komi,
        "boardXSize": board_size,
        "boardYSize": board_size,
        "maxVisits": max_visits,
        "includeOwnership": False,
        "includePolicy": False,
        "analyzeTurns": analyze_turns,
    }


def query_katago(base_url, model_id, payload, timeout, retries):
    url = f"{base_url}/analyze/{model_id}"
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(url, json=payload, timeout=timeout)
        except requests.exceptions.RequestException as e:
            last_err = str(e)
            time.sleep(2 * attempt)
            continue
        if resp.status_code != 200:
            last_err = f"HTTP {resp.status_code}: {resp.text[:300]}"
            time.sleep(2 * attempt)
            continue
        data = resp.json()
        if not data.get("success"):
            last_err = str(data)
            time.sleep(2 * attempt)
            continue
        return data["result"], None
    return None, last_err


def process_game(row, args):
    game_id = row["id"]
    parsed = parse_sgf_game(row["sgfContent"])
    if parsed is None:
        return [], f"SGF 파싱 실패 (game_id={game_id})"

    board_size = parsed["board_size"]
    komi = parsed["komi"]
    all_moves = parsed["moves"]

    try:
        metadata = json.loads(row["metadata"]) if row["metadata"] else {}
    except json.JSONDecodeError:
        metadata = {}

    auto_finish_turn = metadata.get("autoFinishTurn")
    if isinstance(auto_finish_turn, int):
        effective_num_moves = min(len(all_moves), auto_finish_turn - 1)
    else:
        effective_num_moves = len(all_moves)

    if effective_num_moves <= 0:
        return [], None

    moves = all_moves[:effective_num_moves]
    time_records = load_time_records(row["timeRecords"])
    user_id = metadata.get("userId") or row["blackPlayerName"]
    white_is_ai = is_ai_name(row["whitePlayerName"])

    empty_counts_before = compute_empty_counts_before_each_move(moves, board_size)

    analyze_turns = list(range(effective_num_moves + 1))
    payload = build_payload(
        f"extract_{game_id}", moves, board_size, komi, args.rules,
        args.max_visits, analyze_turns,
    )
    results, err = query_katago(args.base_url, args.model_id, payload, args.timeout, args.retries)
    if err is not None:
        return [], f"KataGo 쿼리 실패 (game_id={game_id}): {err}"

    by_turn = {r["turnNumber"]: r for r in results}
    missing = [t for t in analyze_turns if t not in by_turn]
    if missing:
        return [], f"응답 누락 (game_id={game_id}, missing turns={missing[:5]}...)"

    rows_out = []
    for i in range(effective_num_moves):
        move_number = i + 1
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
        top1 = min(move_infos, key=lambda m: m.get("order", 10**9)) if move_infos else None

        winrate_before = wr(root_before["winrate"])
        score_before = sc(root_before["scoreLead"])

        if top1 is not None:
            winrate_top1 = wr(top1["winrate"])
            score_top1 = sc(top1["scoreLead"])
            top1_move_coord = top1["move"]
        else:
            winrate_top1 = winrate_before
            score_top1 = score_before
            top1_move_coord = None

        winrate_actual = wr(root_after["winrate"])
        score_actual = sc(root_after["scoreLead"])

        delta_raw = winrate_top1 - winrate_actual
        weight = compute_weight(winrate_before)
        delta_weighted = delta_raw * weight
        score_delta = score_top1 - score_actual

        empty_count = empty_counts_before[i]
        gate_passed = not (abs(score_before) > empty_count * args.gate_k)

        grade_winrate = grade_from_delta_weighted(delta_weighted)
        if grade_winrate in DOWNGRADE_ELIGIBLE:
            downgrade_steps = downgrade_steps_from_score_delta(score_delta)
        else:
            downgrade_steps = 0
        grade_final = apply_downgrade(grade_winrate, downgrade_steps)

        is_top1_move = (top1_move_coord is not None and actual_coord == top1_move_coord)

        player = "user" if color == "B" else ("ai" if white_is_ai else "user")
        time_spent = time_records.get(str(move_number), "")

        rows_out.append({
            "game_id": game_id,
            "user_id": user_id,
            "player": player,
            "board_size": board_size,
            "move_number": move_number,
            "winrate_before": r2(winrate_before),
            "winrate_top1": r2(winrate_top1),
            "winrate_actual": r2(winrate_actual),
            "delta_raw": r2(delta_raw),
            "weight": r2(weight),
            "delta_weighted": r2(delta_weighted),
            "score_before": r2(score_before),
            "score_top1": r2(score_top1),
            "score_actual": r2(score_actual),
            "score_delta": r2(score_delta),
            "empty_count": empty_count,
            "gate_passed": gate_passed,
            "grade_winrate": grade_winrate,
            "downgrade_steps": downgrade_steps,
            "grade_final": grade_final,
            "is_top1_move": is_top1_move,
            "time_spent_ms": time_spent,
        })

    return rows_out, None


def load_done_game_ids(output_path):
    if not output_path.exists():
        return set()
    done = set()
    with open(output_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            done.add(row["game_id"])
    return done


def write_settings_file(args, settings_path):
    model_file = SERVING_MODELS.get(args.model_id, {})
    if isinstance(model_file, dict):
        model_file = model_file.get("main_model", args.model_id)
    lines = [
        "Doda 복기 수 평가 Delta 분포 분석용 데이터 추출 설정",
        f"생성 시각: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"모델 ID: {args.model_id}",
        f"모델 파일: {model_file}",
        f"maxVisits: {args.max_visits} (모든 판 동일 적용)",
        f"rules: {args.rules}",
        "komi: 판별 SGF에 기록된 원래 값 그대로 사용 (요청서의 'komi 0 고정'과 달리,"
        " 실제 47/448개 판이 komi=6.5로 기록돼 있어 사용자 확인 후 원래 값 사용으로 결정)",
        f"유효성 게이트 k: {args.gate_k} (|score_before| > empty_count * k → gate_passed=false)",
        "보드 크기별 설정 차이: 없음 (rules/komi 산출 방식은 모든 보드 크기에 동일 적용,"
        " komi 자체는 위와 같이 판별로 다름)",
        "",
        "가중치 공식: weight = 1.5 - 0.7*(|winrate_before-50|/50);"
        " winrate_before<50이면 추가로 *0.9",
        "등급 경계(가중 Delta, %): <3 훌륭한 수 / <6 호수 / <10 정수 / <20 아쉬운 수"
        " / <30 악수 / >=30 대악수 (구간은 왼쪽 포함/오른쪽 미포함)",
        "강등 경계(집 Delta): <8 강등없음 / <12 1단계 / >=12 2단계"
        " (grade_winrate가 '정수' 이상일 때만 발동)",
        "",
        "autoFinishTurn: metadata.autoFinishTurn이 설정된 경우, 그 수(move_number) 이상은"
        " 분석에서 제외 (move_number < autoFinishTurn까지만 포함)",
        "대상 데이터: GameRecord.csv 전체 448개 판 (사람 대 사람 2개 판 id=89,90과"
        " 13x13 보드 1개 판 id=110 포함, 사용자 확인 완료)",
        "winrate/scoreLead 부호 처리: 서버 설정(rtx_desktop.cfg) 'reportAnalysisWinratesAs = BLACK'"
        " 확인됨 → 원본 값은 항상 흑 기준이며, 착수자가 백이면 winrate는 100-x, scoreLead는 -x로 변환",
    ]
    settings_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", default=str(REPO_ROOT / "tests" / "data" / "GameRecord.csv"))
    p.add_argument("--output", default=str(THIS_DIR / "output" / "review_delta.csv"))
    p.add_argument("--settings-output", default=str(THIS_DIR / "output" / "review_delta_settings.txt"))
    p.add_argument("--failures-output", default=str(THIS_DIR / "output" / "review_delta_failures.log"))
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument("--model-id", default="best")
    p.add_argument("--max-visits", type=int, default=200)
    p.add_argument("--rules", default="korean")
    p.add_argument("--gate-k", type=float, default=2.5)
    p.add_argument("--timeout", type=float, default=120.0)
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--limit", type=int, default=None, help="테스트용: 앞에서부터 N개 판만 처리")
    return p.parse_args()


def main():
    args = parse_args()
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    settings_path = Path(args.settings_output)
    write_settings_file(args, settings_path)

    failures_path = Path(args.failures_output)

    done_ids = load_done_game_ids(output_path)
    if done_ids:
        print(f"[재개] 이미 처리된 {len(done_ids)}개 판은 건너뜁니다.")

    with open(args.input, newline="", encoding="utf-8") as f:
        all_rows = list(csv.DictReader(f))

    if args.limit:
        all_rows = all_rows[:args.limit]

    write_header = not output_path.exists() or output_path.stat().st_size == 0
    out_f = open(output_path, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(out_f, fieldnames=CSV_COLUMNS)
    if write_header:
        writer.writeheader()

    fail_f = open(failures_path, "a", encoding="utf-8")

    total = len(all_rows)
    ok_count = 0
    fail_count = 0
    skip_count = 0
    t_start = time.time()

    try:
        for i, row in enumerate(all_rows, 1):
            game_id = row["id"]
            if game_id in done_ids:
                skip_count += 1
                continue

            rows_out, err = process_game(row, args)
            if err is not None:
                fail_count += 1
                fail_f.write(f"{game_id}\t{err}\n")
                fail_f.flush()
                print(f"  [{i}/{total}] game={game_id} FAILED: {err}")
                continue

            for r in rows_out:
                writer.writerow(r)
            out_f.flush()
            ok_count += 1

            if i % 20 == 0 or i == total:
                elapsed = time.time() - t_start
                print(f"  [{i}/{total}] 처리 완료 (성공 {ok_count}, 실패 {fail_count}, 건너뜀 {skip_count}) "
                      f"경과 {elapsed:.1f}s")
    finally:
        out_f.close()
        fail_f.close()

    print(f"완료: 총 {total}개 판 중 성공 {ok_count}, 실패 {fail_count}, 건너뜀(재개) {skip_count}")
    print(f"CSV: {output_path}")
    print(f"설정 기록: {settings_path}")
    if fail_count:
        print(f"실패 로그: {failures_path}")


if __name__ == "__main__":
    main()
