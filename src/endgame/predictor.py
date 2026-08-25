"""학습된 종국 판정(XGBoost) 모델을 로드해 확률/판정을 반환하는 서빙 래퍼.

endgame/models/current.json이 가리키는 버전을 프로세스 시작 시 1회 로드한다.
모델이 작아(수백 KB) 로드가 밀리초 단위이므로, 새 버전 반영은 KataGo 가중치
교체와 동일하게 "current.json 갱신 + 프로세스 재시작"으로 충분하다.
"""

import json
import os
from pathlib import Path

import xgboost as xgb

from .features import FEATURE_COLUMNS, extract_features


# current.json에 반드시 있어야 하는 필드. 학습 파이프라인(별도 프로젝트
# doda/endgame-judge의 train.py)이 --promote 시 항상 다 채워서 쓰므로, 값이
# 비어있다면 수동으로 잘못 만든 파일이거나 손상된 것 - 조용히 기본값으로 넘어가지
# 말고 즉시 실패시킨다.
_REQUIRED_CURRENT_FIELDS = ("version", "file", "recommended_threshold")


class EndgamePredictor:
    def __init__(self, model_dir: str):
        self.model_dir = Path(model_dir)
        current_path = self.model_dir / "current.json"
        if not current_path.exists():
            raise FileNotFoundError(f"모델 포인터 파일이 없습니다: {current_path}")

        current = json.loads(current_path.read_text())
        missing = [k for k in _REQUIRED_CURRENT_FIELDS if current.get(k) is None]
        if missing:
            raise ValueError(
                f"{current_path}에 필수 필드가 없습니다: {missing}. "
                f"doda/endgame-judge의 train.py --promote로 생성된 파일인지 확인하세요."
            )

        model_path = self.model_dir / current["file"]
        if not model_path.exists():
            raise FileNotFoundError(f"모델 아티팩트가 없습니다: {model_path}")

        self.version = current["version"]
        self.recommended_threshold = current["recommended_threshold"]

        # 재학습 없이 즉시 운영 튜닝이 필요할 때를 위한 환경변수 override.
        # 지정하지 않으면 학습 파이프라인이 산출한(precision/recall로 검증된)
        # recommended_threshold를 사용한다 - 여기에도 하드코딩된 기본값을 두지 않는다.
        env_threshold = os.getenv("ENDGAME_MODEL_THRESHOLD")
        self.threshold = float(env_threshold) if env_threshold else self.recommended_threshold

        self.booster = xgb.Booster()
        self.booster.load_model(str(model_path))

        print(
            f"[EndgamePredictor] Loaded model version={self.version} "
            f"threshold={self.threshold} (path={model_path})"
        )

    def predict_proba(self, analysis: dict, turn: int) -> float:
        """analysis(ownership/ownershipStdev/policy/rootInfo 포함 KataGo 응답 한 턴)에서
        '종료 추천' 확률(class 1)을 반환."""
        features = extract_features(analysis, turn)
        dmatrix = xgb.DMatrix([features], feature_names=FEATURE_COLUMNS)
        return float(self.booster.predict(dmatrix)[0])

    def predict(self, analysis: dict, turn: int) -> dict:
        probability = self.predict_proba(analysis, turn)
        return {
            "turn": turn,
            "probability": probability,
            "recommendEnd": probability >= self.threshold,
        }
