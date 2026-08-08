"""종국 판정(XGBoost) 모델을 학습하고 버전 아티팩트로 저장한다.

기보 단위(GroupShuffleSplit)로 train/test를 나눠 평가하고, 여러 threshold에서의
precision/recall을 표로 보여준다. --promote를 주면 endgame/models/current.json을
갱신해 새 버전을 서빙 대상으로 승격한다(그렇지 않으면 아티팩트만 저장되고
기존 current.json은 그대로 유지 - 검증 전 모델이 실수로 서빙되는 걸 방지).

threshold에는 기본값을 두지 않는다 - sweep 표를 사람이 보고 정밀도/재현율
트레이드오프를 직접 판단해서 넣도록 강제한다("일단 0.85"가 검증 없이 굳어지는 걸
방지). --promote 시에는 --threshold를 반드시 같이 줘야 한다.

사용 예:
  # 1) 먼저 지표만 확인 (threshold 생략 가능, current.json 변경 없음)
  python -m endgame.train.train

  # 2) sweep 표를 보고 threshold를 정해 승격
  python -m endgame.train.train --threshold 0.85 --promote
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score
from sklearn.model_selection import GroupShuffleSplit
from xgboost import XGBClassifier

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from endgame.features import FEATURE_COLUMNS  # noqa: E402
from endgame.train.dataset import build_dataset  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent / "data"
KST = timezone(timedelta(hours=9))


def git_short_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT,
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or "nogit"
    except Exception:
        return "nogit"


def threshold_sweep(y_test, y_proba, thresholds):
    rows = []
    for t in thresholds:
        y_pred = (y_proba >= t).astype(int)
        rows.append({
            "threshold": t,
            "precision": round(precision_score(y_test, y_pred, zero_division=0), 4),
            "recall": round(recall_score(y_test, y_pred, zero_division=0), 4),
            "f1": round(f1_score(y_test, y_pred, zero_division=0), 4),
            "accuracy": round(accuracy_score(y_test, y_pred), 4),
        })
    return rows


def main(args):
    print(f"데이터셋 구성 중... (csv={args.csv}, json_dir={args.json_dir})")
    df = build_dataset(args.csv, args.json_dir)
    if df.empty:
        raise SystemExit("데이터셋이 비어 있습니다. --csv/--json-dir 경로를 확인하세요.")
    print(f"총 {len(df)}개 (turn, label) 샘플, {df['game_title'].nunique()}개 기보, "
          f"label 분포={df['label'].value_counts().to_dict()}")

    X = df[FEATURE_COLUMNS]
    y = df["label"]
    groups = df["game_title"]

    gss = GroupShuffleSplit(n_splits=1, test_size=args.test_size, random_state=args.seed)
    train_idx, test_idx = next(gss.split(X, y, groups))
    X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
    y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]

    pos = y_train.value_counts()
    scale_pos_weight = pos.get(0, 1) / max(pos.get(1, 1), 1)

    model = XGBClassifier(
        n_estimators=100,
        max_depth=3,
        learning_rate=0.1,
        scale_pos_weight=scale_pos_weight,
        eval_metric="logloss",
    )
    model.fit(X_train, y_train)

    y_proba = model.predict_proba(X_test)[:, 1]
    sweep = threshold_sweep(y_test, y_proba, [0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95])
    print("\n=== Threshold별 성능 (held-out) ===")
    header = f"{'threshold':>9} {'precision':>9} {'recall':>9} {'f1':>7} {'accuracy':>9}"
    print(header)
    for row in sweep:
        print(f"{row['threshold']:>9} {row['precision']:>9} {row['recall']:>9} "
              f"{row['f1']:>7} {row['accuracy']:>9}")

    if args.promote and args.threshold is None:
        raise SystemExit(
            "--promote를 쓰려면 --threshold를 반드시 지정해야 합니다. "
            "위 sweep 표를 보고 precision/recall 트레이드오프를 판단해서 값을 고르세요."
        )

    if args.threshold is None:
        print("\n--threshold를 지정하지 않아 아티팩트만 저장하고 종료합니다 "
              "(sweep 표를 보고 값을 정한 뒤 --threshold와 함께 재실행하세요).")
        chosen = None
    else:
        chosen = next((r for r in sweep if r["threshold"] == args.threshold), None)
        if chosen is None:
            y_pred = (y_proba >= args.threshold).astype(int)
            chosen = {
                "threshold": args.threshold,
                "precision": round(precision_score(y_test, y_pred, zero_division=0), 4),
                "recall": round(recall_score(y_test, y_pred, zero_division=0), 4),
                "f1": round(f1_score(y_test, y_pred, zero_division=0), 4),
                "accuracy": round(accuracy_score(y_test, y_pred), 4),
            }
        print(f"\n선택된 threshold={args.threshold}: precision={chosen['precision']} "
              f"recall={chosen['recall']} f1={chosen['f1']}")

    model_dir = Path(args.model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)

    version = f"{datetime.now(KST).strftime('%Y-%m-%d')}_{git_short_sha()}"
    artifact_name = f"endgame_model_{version}.json"
    artifact_path = model_dir / artifact_name
    model.save_model(str(artifact_path))
    print(f"\n아티팩트 저장: {artifact_path}")

    manifest_entry = {
        "version": version,
        "file": artifact_name,
        "recommended_threshold": chosen["threshold"] if chosen else None,
        "precision": chosen["precision"] if chosen else None,
        "recall": chosen["recall"] if chosen else None,
        "f1": chosen["f1"] if chosen else None,
        "n_train_samples": int(len(X_train)),
        "n_test_samples": int(len(X_test)),
        "trained_at": datetime.now(KST).isoformat(),
        "promoted": bool(args.promote),
    }
    with open(model_dir / "manifest.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(manifest_entry, ensure_ascii=False) + "\n")

    if args.promote:
        current_path = model_dir / "current.json"
        current_path.write_text(json.dumps(manifest_entry, ensure_ascii=False, indent=2) + "\n")
        print(f"승격 완료: {current_path} -> {artifact_name} "
              f"(서빙에 반영하려면 프로세스를 재시작하세요)")
    else:
        print("\n--promote를 주지 않아 current.json은 변경되지 않았습니다 "
              "(기존 버전이 계속 서빙됩니다). 지표 확인 후 재실행 시 --promote를 추가하세요.")


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--csv", default=str(DATA_DIR / "GameRecord_rows.csv"))
    p.add_argument("--json-dir", default=str(DATA_DIR / "analysis-result"))
    p.add_argument("--test-size", type=float, default=0.2)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument(
        "--threshold", type=float, default=None,
        help="promote 시 필수. 생략하면 sweep 표만 보고 종료(아티팩트는 저장됨).",
    )
    p.add_argument("--model-dir", default=str(REPO_ROOT / "endgame" / "models"))
    p.add_argument("--promote", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    main(parse_args())
