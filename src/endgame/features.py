"""KataGo analysis 결과(rootInfo/ownership/policy)에서 종국 판정 모델의 입력 피처를 추출.

학습(별도 프로젝트 doda/endgame-judge의 features.py - 이 파일과 동기화 유지)과
서빙(endgame/predictor)이 반드시 동일한 피처 계산 로직을 써야 한다 - 두 곳이
어긋나면 train/serve skew가 생긴다. 이 파일을 수정하면 doda/endgame-judge/features.py에도
동일하게 반영할 것.
"""

import numpy as np

# XGBoost 모델(models/endgame/*.json)이 학습된 피처 순서. 순서를 바꾸면 기존
# 모델 아티팩트와 호환이 깨지므로 임의로 재정렬하지 말 것.
FEATURE_COLUMNS = [
    "scoreStdev",
    "ownership_mean_abs",
    "ownership_stdev_mean",
    "ownership_stdev_max",
    "passPolicy",
    "winrate_margin",
    "turn",
    "score_lead_abs",
]


def extract_features(analysis: dict, turn: int) -> list:
    """KataGo analysis 응답 한 턴 분량(dict)에서 FEATURE_COLUMNS 순서의 피처 벡터를 만든다.

    analysis는 includeOwnership/includeOwnershipStdev/includePolicy를 켠 상태의
    KataGo analysis 엔진 출력 한 줄(rootInfo/ownership/ownershipStdev/policy 포함)이어야 한다.
    """
    root_info = analysis.get("rootInfo") or {}
    ownership = analysis.get("ownership")
    ownership_stdev = analysis.get("ownershipStdev")
    policy = analysis.get("policy")

    if ownership is None or ownership_stdev is None or not policy:
        raise ValueError(
            "analysis 응답에 ownership/ownershipStdev/policy가 없습니다. "
            "includeOwnership/includeOwnershipStdev/includePolicy 옵션을 켰는지 확인하세요."
        )

    winrate = root_info.get("winrate")
    score_lead = root_info.get("scoreLead")

    return [
        root_info.get("scoreStdev"),
        float(np.mean(np.abs(ownership))),
        float(np.mean(ownership_stdev)),
        float(np.max(ownership_stdev)),
        policy[-1],  # 마지막 인덱스 = pass의 정책 확률
        abs(winrate - 0.5) * 2 if winrate is not None else None,
        turn,
        abs(score_lead) if score_lead is not None else None,
    ]
