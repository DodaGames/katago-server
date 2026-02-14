import threading
from katago_worker import KataGoWorker
from config import (
    SERVING_MODELS,
    base_model_path,
    human_model_path,
    config_path,
    NUM_WORKERS_PER_MODEL,
    NUM_HUMAN_WORKERS,
)

# 모델별 워커 풀 초기화
# 구조: { "level2": [worker1, worker2], "level3": [worker1, worker2] }
analysis_worker_map = {}
analysis_rr_indices = {}  # 라운드 로빈 인덱스 { "level2": 0, "level3": 0 }

print("Initializing Analysis Workers...")
for model_id, model_file in SERVING_MODELS.items():
    print(f"  - Loading model: {model_id} ({model_file})")
    model_full_path = base_model_path + model_file + ".txt.gz"
    
    workers = [
        KataGoWorker(model_full_path, config_path) 
        for _ in range(NUM_WORKERS_PER_MODEL)
    ]
    analysis_worker_map[model_id] = workers
    analysis_rr_indices[model_id] = 0

print("Initializing Human Workers...")
katago_human_pool = [
    KataGoWorker(base_model_path + "b18c384nbt-humanv0.bin.gz", config_path, human_model_path)
    # Human 모델의 경우 base model을 무엇으로 할지 애매하지만, 
    # 기존 코드에서는 analysis_model_path를 썼음. 
    # 여기서는 안전하게 SERVING_MODELS의 첫번째나 고정된 모델을 쓰거나, 
    # human_model_path가 지정되면 -model 인자는 덜 중요할 수 있음(KataGo 버전에 따라 다름).
    # 기존 로직 유지: analysis_worker와 동일한 base model을 쓰고 -human-model을 추가했었음.
    # 변경: 명시적으로 첫번째 서빙 모델을 base로 사용 (Human play는 보통 base model + human aux model)
    # 혹은 임의의 모델 사용. 여기서는 level2를 base로 가정.
] if NUM_HUMAN_WORKERS > 0 else []

# Human 워커는 analysis_models["level2"]를 base로 쓰도록 하드코딩하거나 
# SERVING_MODELS의 첫번째를 가져오도록 수정
if NUM_HUMAN_WORKERS > 0:
    # 재초기화 (위 리스트 컴프리헨션은 가독성을 위해 풀어서 다시 작성)
    katago_human_pool = []
    # Base model for human (use level2 as default base)
    base_model_file = SERVING_MODELS.get("level2", list(SERVING_MODELS.values())[0])
    base_model_full_path = base_model_path + base_model_file + ".txt.gz"
    
    for _ in range(NUM_HUMAN_WORKERS):
        katago_human_pool.append(
            KataGoWorker(base_model_full_path, config_path, human_model_path)
        )

_human_idx = 0
_pool_lock = threading.Lock()


def get_analysis_worker(model_id: str):
    """
    해당 model_id를 담당하는 워커를 반환합니다.
    """
    with _pool_lock:
        workers = analysis_worker_map.get(model_id)
        if not workers:
            return None
        
        idx = analysis_rr_indices[model_id]
        worker = workers[idx]
        analysis_rr_indices[model_id] = (idx + 1) % len(workers)
        return worker


def get_human_worker():
    global _human_idx
    if not katago_human_pool:
        return None
        
    with _pool_lock:
        worker = katago_human_pool[_human_idx]
        _human_idx = (_human_idx + 1) % len(katago_human_pool)
        return worker
