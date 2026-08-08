"""GameRecord CSV의 SGF를 이 서버의 best_judge KataGo 프로세스로 직접 분석해
endgame 모델 학습용 analysis-result JSON(턴별 rootInfo/ownership/policy)을 생성한다.

기존에는 별도 NestJS 백엔드(localhost:3000)를 거쳐 분석 결과를 받아왔지만,
SGF 파싱(utils/sgf_parser)과 KataGo 분석(katago_worker)이 전부 이 repo 안에
있으므로 외부 서비스 없이 닫힌 파이프라인으로 데이터를 생성한다.

사용 예:
  python -m endgame.train.prepare_data
  python -m endgame.train.prepare_data --overwrite
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from katago_worker import KataGoWorker  # noqa: E402
from config import SERVING_MODELS, base_model_path, config_path as default_config_path  # noqa: E402
from utils.sgf_parser import parse_sgf_game  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent / "data"


def safe_title(title: str) -> str:
    cleaned = "".join(c for c in title if c.isalnum() or c in (" ", ".", "_", "-")).strip()
    return cleaned or "unnamed_game"


async def analyze_game(worker: KataGoWorker, moves, board_size, komi, rules, timeout: float):
    analyze_turns = list(range(len(moves) + 1))
    payload = {
        "id": "prepare_data",
        "moves": moves,
        "rules": rules,
        "komi": komi,
        "boardXSize": board_size,
        "boardYSize": board_size,
        "analyzeTurns": analyze_turns,
        "includeOwnership": True,
        "includeOwnershipStdev": True,
        "includePolicy": True,
    }
    return await worker.analyze(payload, timeout=timeout)


async def main_async(args):
    df = pd.read_csv(args.csv)
    model_file = SERVING_MODELS["best_judge"]["main_model"]
    worker = KataGoWorker(
        main_model_path=str(Path(base_model_path) / model_file),
        config_path=args.config_path,
    )

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        for _, row in df.iterrows():
            title = str(row.get("title", "unnamed"))
            sgf = str(row.get("sgfContent", ""))
            if not sgf:
                print(f"SGF 내용이 비어있음, 스킵: {title}")
                continue

            out_path = out_dir / f"{safe_title(title)}.json"
            if out_path.exists() and not args.overwrite:
                print(f"이미 존재, 스킵: {out_path.name}")
                continue

            parsed = parse_sgf_game(sgf)
            if not parsed or not parsed["moves"]:
                print(f"SGF 파싱 실패, 스킵: {title}")
                continue

            print(f"분석 중: {title} ({len(parsed['moves'])}수)")
            try:
                result = await analyze_game(
                    worker, parsed["moves"], parsed["board_size"], parsed["komi"], args.rules, args.timeout
                )
            except Exception as e:
                print(f"분석 실패 ({title}): {e}")
                continue

            if not isinstance(result, list):
                print(f"분석 실패 ({title}): {result}")
                continue

            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            print(f"저장 완료: {out_path.name}")
    finally:
        worker.process.terminate()
        try:
            worker.process.wait(timeout=15)
        except Exception:
            worker.process.kill()


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--csv", default=str(DATA_DIR / "GameRecord_rows.csv"))
    p.add_argument("--output-dir", default=str(DATA_DIR / "analysis-result"))
    p.add_argument("--config-path", default=default_config_path)
    p.add_argument("--rules", default="korean")
    p.add_argument("--timeout", type=float, default=600.0)
    p.add_argument("--overwrite", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main_async(parse_args()))
