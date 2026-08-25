def get_expected_response_count(payload: dict) -> int:
    """
    KataGo 분석 요청 페이로드(payload)를 받아
    해당 요청에 대해 기대되는 응답(JSON 줄)의 개수를 계산하여 반환합니다.
    """
    if "analyzeTurns" in payload and isinstance(payload["analyzeTurns"], list):
        # 배열이 비어있는 경우(0)가 올 수도 있으므로 최소 1개인지, 아니면 0으로 처리할지 고려
        return max(1, len(payload["analyzeTurns"]))
    return 1
