"""'/check-end-game' 요청 바디 DTO.

katago_worker.KataGoWorker.analyze()가 반환하는 KataGo analysis 엔진 응답
한 턴 분량과 동일한 구조. 예측에 쓰이지 않는 필드(moveInfos, id, isDuringSearch 등)는
Pydantic 기본 동작에 따라 그냥 무시된다.
"""

from pydantic import BaseModel, Field


class RootInfo(BaseModel):
    scoreStdev: float
    winrate: float
    scoreLead: float


class KatagoAnalysisResult(BaseModel):
    rootInfo: RootInfo
    ownership: list[float] = Field(min_length=1)
    ownershipStdev: list[float] = Field(min_length=1)
    policy: list[float] = Field(min_length=1)
    turnNumber: int
