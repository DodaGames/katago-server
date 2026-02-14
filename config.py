analysis_models = {
    "level1": "kata1-b6c96-s1248000-d550347", # rating 483, (준호)
    "level2": "kata1-b6c96-s938496-d1208807", # rating 800, 막 같다 붙이는 애 not bad (준호2)
    "level3": "kata1-b6c96-s1995008-d1329786", # rating 1067, 막 같다 붙이는 애 (2보단 맥락 있게 둠) not bad (초보 표본) (용호)
    "level4": "kata1-b6c96-s4136960-d1510003", # rating 1524, 대기 (3이랑 비슷) (용호2)
    "level5": "kata1-b6c96-s5214720-d1690538", # rating 1599,  (재호)
    "level6": "kata1-b6c96-s6127360-d1754797", # rating 1697,  가닥 없음 (건호)
    # "level7": "kata1-b6c96-s8080640-d1961030", # rating 1851,  가닥 약간 (무야호)
    # "level8": "kata1-b6c96-s8982784-d2082583", # rating 1929,  통과 (중수 표본) (재호)
    # # "level9": "kata1-b6c96-s10014464-d2201128", # rating 2108,  
    # # "level10": "kata1-b6c96-s10825472-d2300510", # rating 2283,
    # "level11": "kata1-b6c96-s12849664-d2510774", # rating 2540, 통과 (고수 표본) (건호)
    # # "level12": "kata1-b6c96-s19408128-d3280178", # rating 4146, 기본기는 잡혀있음
    "level13": "kata1-b6c96-s48921344-d7092247", # rating 8087, 최고수 표본 (무야호)
}

# 모델 경로 및 설정 파일
katago_executable_path = "/opt/homebrew/bin/katago"

base_model_path = "/Users/my_home/Documents/project/baduk-master/AI-test-server/models/"
analysis_model_path = base_model_path + analysis_models["level2"] + ".txt.gz"
human_model_path = base_model_path + "b18c384nbt-humanv0.bin.gz"
config_path = "/Users/my_home/Documents/project/baduk-master/AI-test-server/configs/analysis_example.cfg"

# 워커 개수 설정
NUM_ANALYSIS_WORKERS = 2  # katago_analysis 프로세스 개수
NUM_HUMAN_WORKERS = 2  # katago_human 프로세스 개수


