from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

import uvicorn

from pool import get_analysis_worker

app = FastAPI()


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
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
