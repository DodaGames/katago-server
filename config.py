import os
import sys
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()


def get_env_required(key):
    value = os.getenv(key)
    if not value:
        print(f"[Error] Environment variable '{key}' is required but not set.")
        sys.exit(1)
    return value


# 전체 모델 목록 (참고용)
analysis_models = {
    "level1": "kata1-b6c96-s1248000-d550347.txt.gz",  # rating 483, (준호)
    "level2": "kata1-b6c96-s938496-d1208807.txt.gz",  # rating 800, 막 같다 붙이는 애 not bad (준호2)
    "level3": "kata1-b6c96-s1995008-d1329786.txt.gz",  # rating 1067, 막 같다 붙이는 애 (2보단 맥락 있게 둠) not bad (초보 표본) (용호)
    "level4": "kata1-b6c96-s4136960-d1510003.txt.gz",  # rating 1524, 대기 (3이랑 비슷) (용호2)
    "level5": "kata1-b6c96-s5214720-d1690538.txt.gz",  # rating 1599,  (재호)
    "level6": "kata1-b6c96-s6127360-d1754797.txt.gz",  # rating 1697,  가닥 없음 (건호)
    "level7": "kata1-b6c96-s8080640-d1961030.txt.gz",  # rating 1851,  가닥 약간 (무야호)
    "level8": "kata1-b6c96-s8982784-d2082583.txt.gz",  # rating 1929,  통과 (중수 표본) (재호)
    # # "level9": "kata1-b6c96-s10014464-d2201128.txt.gz", # rating 2108,
    # # "level10": "kata1-b6c96-s10825472-d2300510.txt.gz", # rating 2283,
    "level11": "kata1-b6c96-s12849664-d2510774.txt.gz",  # rating 2540, 통과 (고수 표본) (건호)
    # # "level12": "kata1-b6c96-s19408128-d3280178.txt.gz", # rating 4146, 기본기는 잡혀있음
    "level13": "kata1-b6c96-s48921344-d7092247.txt.gz",  # rating 8087, 최고수 표본 (무야호)
}

# 모델 경로 및 설정 파일
katago_executable_path = get_env_required("KATAGO_EXECUTABLE_PATH")

# 서빙할 모델 목록
SERVING_MODELS = {
    "level2": "kata1-b6c96-s938496-d1208807.txt.gz",
    "level3": "kata1-b6c96-s1995008-d1329786.txt.gz",
    # "level6": "kata1-b6c96-s6127360-d1754797.txt.gz",  # rating 1697,  가닥 없음 (건호)
    "best": {
        "is_human": False,
        "main_model": "kata1-b10c128-s1141046784-d204142634.txt.gz",
    },
    # "human": {
    #     "is_human": True,
    #     "main_model": "kata1-b10c128-s1141046784-d204142634.txt.gz",
    #     "human_model": "b18c384nbt-humanv0.bin.gz",
    # },
}

base_model_path = get_env_required("BASE_MODEL_PATH")

# 설정 파일 경로 (환경변수 'KATAGO_CONFIG_PATH' - 필수)
# .env 파일에서 로드되거나 시스템 환경변수로 설정되어 있어야 함
env_config_path = get_env_required("KATAGO_CONFIG_PATH")

if not env_config_path:
    print("[Error] KATAGO_CONFIG_PATH is not set in .env or environment variables.")
    sys.exit(1)

# 상대 경로인 경우 절대 경로로 변환
if not os.path.isabs(env_config_path):
    env_config_path = os.path.abspath(env_config_path)

config_path = env_config_path
print(f"[config.py] Using config: {config_path}")

NUM_WORKERS_PER_MODEL = int(
    os.getenv("NUM_WORKERS_PER_MODEL", "1")
)  # 모델별 프로세스 개수 (메모리 절약을 위해 1로 설정, 환경에 따라 변경 가능)
