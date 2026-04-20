from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

import uvicorn
import time
import logging

from pool import get_analysis_worker

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
        content={"success": False, "error": exc.detail},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    error_msg = ", ".join([f"{err['loc'][-1]}: {err['msg']}" for err in errors])
    return JSONResponse(
        status_code=422,
        content={"success": False, "error": error_msg},
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"success": False, "error": str(exc)},
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 모든 origin 허용
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/analyze/{model_id}")
def analyze(model_id: str, payload: dict):
    worker = get_analysis_worker(model_id)
    if not worker:
        raise HTTPException(status_code=400, detail=f"Model '{model_id}' not found.")

    result = worker.analyze(payload)

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

    return {"success": True, "result": result}


@app.get("/health")
def health_check():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, access_log=False)
