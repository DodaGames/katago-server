"""endgame 피처의 런타임 싱글턴 배선. main.py는 이 모듈의 get_endgame_predictor()만 호출한다.

analysis 피처가 KataGo 워커 풀을 자기 pool.py에서 소유하는 것과 대칭적으로,
endgame 피처도 자신의 예측기 싱글턴을 스스로 소유한다.
"""

from .config import ENDGAME_MODEL_DIR
from .predictor import EndgamePredictor

# 종국 판정(XGBoost) 모델. KataGo 워커와 달리 가볍고 서브프로세스가 아니라
# 프로세스 시작 시 1회 로드해두는 in-process 싱글턴이다. 아티팩트가 아직
# 준비되지 않은 환경(예: 로컬 개발 초기)에서도 서버 자체는 뜨도록 실패를
# 흡수하고 None으로 둔다 - /check-end-game 호출 시점에 에러를 낸다.
try:
    _endgame_predictor = EndgamePredictor(ENDGAME_MODEL_DIR)
except FileNotFoundError as e:
    print(f"[WARN] Endgame predictor를 로드하지 못했습니다: {e}")
    _endgame_predictor = None


def get_endgame_predictor():
    return _endgame_predictor
