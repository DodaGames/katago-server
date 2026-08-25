from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

import uvicorn
import time
import logging

from analysis.pool import (
    get_analysis_worker,
    get_pool_stats,
    acquire_concurrency_slot,
    record_request_latency,
)
from endgame.schemas import KatagoAnalysisResult
from endgame.service import get_endgame_predictor
from schemas import ApiResponse, ApiError, ApiErrorResponse, error_code_for_status

app = FastAPI()

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("uvicorn.error")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()

    # 요청 시점 로그 추가
    logger.info(f'Request: {request.method} "{request.url.path}"')

    response = await call_next(request)

    process_time = time.time() - start_time
    formatted_process_time = "{:.4f}".format(process_time)

    # 클라이언트 정보 가져오기
    client_host = request.client.host if request.client else "unknown"

    # 기본 uvicorn 포맷과 유사하게 통합 로그 출력 (시간 포함)
    logger.info(
        f'Response: {client_host} - "{request.method} {request.url.path}" {response.status_code} - {formatted_process_time}s'
    )

    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content=ApiErrorResponse(
            error=ApiError(code=error_code_for_status(exc.status_code), message=str(exc.detail))
        ).model_dump(),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    error_msg = ", ".join([f"{err['loc'][-1]}: {err['msg']}" for err in errors])
    return JSONResponse(
        status_code=422,
        content=ApiErrorResponse(
            error=ApiError(code=error_code_for_status(422), message=error_msg)
        ).model_dump(),
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content=ApiErrorResponse(
            error=ApiError(code=error_code_for_status(500), message=str(exc))
        ).model_dump(),
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 모든 origin 허용
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/analyze/{model_id}", response_model=ApiResponse[Any])
async def analyze(model_id: str, payload: dict):
    worker = get_analysis_worker(model_id)
    if not worker:
        raise HTTPException(status_code=400, detail=f"Model '{model_id}' not found.")

    # 동시성 상한이 설정된 모델(예: 복기)은 FIFO로 대기 후 슬롯을 얻는다.
    request_start = time.time()
    async with acquire_concurrency_slot(model_id):
        result = await worker.analyze(payload)
    record_request_latency(model_id, time.time() - request_start)

    def handle_error(err_msg: str):
        err_lower = err_msg.lower()
        if "timeout" in err_lower:
            raise HTTPException(status_code=504, detail=err_msg)
        elif "internal process error" in err_lower:
            raise HTTPException(status_code=500, detail=err_msg)
        else:
            raise HTTPException(status_code=400, detail=err_msg)

    # 에러 여부 확인 및 상태 코드 응답 처리
    if isinstance(result, dict) and "error" in result:
        handle_error(result["error"])

    if isinstance(result, list):
        for item in result:
            if isinstance(item, dict) and "error" in item:
                handle_error(item["error"])

    return ApiResponse(result=result)


@app.post("/check-end-game", response_model=ApiResponse[bool])
async def check_end_game(analysis_result: KatagoAnalysisResult):
    """이미 확보된 KataGo 분석 결과(best_judge 등으로 직접 분석한 한 턴 분량) 하나를 받아
    종국 판정(XGBoost) 모델을 돌려 '종료 추천' 여부를 {"success": True, "result": boolean} 형식으로 반환한다.

    이 서버가 KataGo 분석을 직접 수행하지 않는다 - 호출자가 이미 /analyze로 얻은
    rootInfo/ownership/ownershipStdev/policy/turnNumber를 그대로 전달한다.
    요청 스키마는 KatagoAnalysisResult DTO(endgame/schemas.py)로 검증되며,
    필드 누락/타입 불일치는 FastAPI가 422로 응답한다.
    """
    predictor = get_endgame_predictor()
    if not predictor:
        raise HTTPException(status_code=500, detail="종국 판정 모델이 로드되지 않았습니다.")

    try:
        is_endgame = (
            predictor.predict_proba(
                analysis_result.model_dump(), analysis_result.turnNumber
            )
            >= predictor.threshold
        )
    except Exception as e:
        # DTO 검증은 통과했지만 피처 계산 단계에서 실패한 경우(예: 극단적인 입력값) -
        # 종료를 잘못 추천하는 것보다 안전한 기본값(False)으로 응답한다.
        logger.error(f"Endgame prediction failed: {e}")
        is_endgame = False

    return ApiResponse(result=is_endgame)


@app.get("/health", response_model=ApiResponse[str])
def health_check():
    return ApiResponse(result="ok")


@app.get("/status", response_model=ApiResponse[dict[str, Any]])
def status_check():
    """운영 지표 대시보드용 스냅샷: 모델별 큐 depth/대기 요청 수/요청 지연시간
    (p50/p95)/동시성 게이트 상태(대기 중 건수·대기시간)와 GPU 사용률을 반환합니다."""
    return ApiResponse(result=get_pool_stats())


if __name__ == "__main__":
    # reload=False: 프로덕션에서 실수로 `python main.py`를 직접 실행해도
    # 자동 재시작이 켜지지 않도록 한다 (지난 OOM 사고 원인 재발 방지).
    # 개발 중 자동 재시작이 필요하면 scripts/run_dev.sh를 사용한다.
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False, access_log=False)
