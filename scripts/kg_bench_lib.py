"""bench_networks.py / generate_ground_truth.py / run_sweep.py가 공유하는 헬퍼.

서버(main.py/pool.py)를 거치지 않고 katago_worker.KataGoWorker를 직접 구동해서
모델을 하나씩 순차 점유하며 측정하기 위한 공통 코드.
"""

import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from katago_worker import KataGoWorker  # noqa: E402
from config import base_model_path  # noqa: E402


def load_manifest(manifest_path, only_names=None):
    manifest = json.loads(Path(manifest_path).read_text())
    if only_names:
        wanted = set(only_names)
        manifest = [m for m in manifest if m["name"] in wanted]
    return manifest


def load_positions(positions_path):
    games = []
    with open(positions_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                games.append(json.loads(line))
    return games


def start_worker(model_cfg: dict) -> KataGoWorker:
    model_path = os.path.join(base_model_path, model_cfg["model_path"])
    config_path = str(REPO_ROOT / model_cfg["config_path"])
    return KataGoWorker(main_model_path=model_path, config_path=config_path)


def stop_worker(worker: KataGoWorker):
    worker.process.terminate()
    try:
        worker.process.wait(timeout=15)
    except Exception:
        worker.process.kill()


def build_payload(moves, max_visits, board_size, komi, rules, analyze_turns=None, query_id="bench"):
    payload = {
        "id": query_id,
        "moves": moves,
        "rules": rules,
        "komi": komi,
        "boardXSize": board_size,
        "boardYSize": board_size,
        "maxVisits": max_visits,
        "includeOwnership": False,
        "includePolicy": False,
    }
    if analyze_turns is not None:
        payload["analyzeTurns"] = analyze_turns
    return payload


async def time_query(worker: KataGoWorker, payload: dict, timeout: float):
    start = time.perf_counter()
    result = await worker.analyze(payload, timeout=timeout)
    elapsed = time.perf_counter() - start
    return elapsed, result


def extract_root_info(result):
    if isinstance(result, list) and result:
        return result[-1].get("rootInfo", {})
    return {}


def full_analyze_turns(num_moves: int) -> list:
    """한 판의 모든 turn(0..num_moves)을 analyzeTurns로 요청.
    주의: analyzeTurns에 중복된 turn 번호를 넣으면 KataGo가 내부적으로 dedup해서
    응답 라인 수가 요청 길이보다 적게 와, expected_lines와 어긋나 타임아웃까지
    block된다 (겪었던 버그). 항상 고유한 turn 목록만 사용할 것."""
    return list(range(num_moves + 1))
