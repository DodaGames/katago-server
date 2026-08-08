"""GameRecord CSV(라벨) + KataGo 분석 JSON(피처)을 합쳐 학습용 DataFrame을 만든다.

피처 계산은 endgame.features.extract_features를 그대로 재사용해 서빙 코드와
skew가 생기지 않게 한다.
"""

import json
import os

import pandas as pd

from endgame.features import FEATURE_COLUMNS, extract_features


def build_dataset(csv_path: str, json_dir: str) -> pd.DataFrame:
    df_meta = pd.read_csv(csv_path)
    rows = []

    for _, row in df_meta.iterrows():
        title = row["title"]
        # title과 실제 json 파일명 매칭 (OS 환경에 따라 슬래시 처리 주의)
        filename = title.replace("/", " ").replace("  ", " ") + ".json"
        json_path = os.path.join(json_dir, filename)
        if not os.path.exists(json_path):
            continue

        try:
            labels_dict = (
                json.loads(row["canRecommendEnd"]) if pd.notna(row["canRecommendEnd"]) else {}
            )
        except (json.JSONDecodeError, TypeError):
            labels_dict = {}

        with open(json_path, "r", encoding="utf-8") as f:
            game_analysis = json.load(f)
        game_analysis.sort(key=lambda x: x["turnNumber"])
        max_turn = game_analysis[-1]["turnNumber"]
        if len(game_analysis) != max_turn + 1:
            raise ValueError(
                f"Turn number mismatch in {json_path}: expected {max_turn + 1} turns, got {len(game_analysis)}"
            )

        for turn_number, analysis in enumerate(game_analysis):
            label = 1 if labels_dict.get(str(turn_number)) else 0
            features = extract_features(analysis, turn_number)
            rows.append(
                {
                    "game_title": title,
                    **dict(zip(FEATURE_COLUMNS, features)),
                    "label": label,
                }
            )

    return pd.DataFrame(rows)
