from fastapi import FastAPI, HTTPException
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


@app.post("/analyze/{model_id}")
def analyze(model_id: str, payload: dict):
    worker = get_analysis_worker(model_id)
    if not worker:
        raise HTTPException(status_code=400, detail=f"Model '{model_id}' not found.")
    
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