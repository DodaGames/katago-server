import os
from pathlib import Path

# 종국 판정(XGBoost) 모델 아티팩트 디렉터리. endgame/models/current.json이
# 현재 서빙 버전을 가리킨다 (별도 프로젝트 doda/endgame-judge의 train.py
# --promote로 만든 아티팩트를 이 디렉터리로 수동 복사해서 갱신).
# 기본값은 이 패키지(endgame/) 바로 아래 models/ - analysis 피처의 모델 저장
# 관례(analysis/models/)와 동일하게 피처 폴더 안에 자기 아티팩트를 둔다.
ENDGAME_MODEL_DIR = os.getenv(
    "ENDGAME_MODEL_DIR", str(Path(__file__).resolve().parent / "models")
)
