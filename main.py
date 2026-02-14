from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import uvicorn

from pool import get_analysis_worker, get_human_worker

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 모든 origin 허용
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/analyze")
def analyze(payload: dict):
    worker = get_analysis_worker()
    result = worker.analyze(payload)
    return {"success": True, "result": result}


@app.post("/humanplay")
def human_play(payload: dict):
    worker = get_human_worker()
    result = worker.analyze(payload)
    return {"success": True, "result": result}


@app.get("/health")
def health_check():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)