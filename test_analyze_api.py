import requests
import json
import sys


def test_analyze_api(model_id):
    """
    /analyze/{model_id} API를 테스트하는 함수입니다.
    """
    url = f"http://127.0.0.1:8000/analyze/{model_id}"

    # KataGo가 분석할 때 사용하는 기본 페이로드 예시입니다.
    # 필요에 따라 이 값을 수정하여 테스트할 수 있습니다.
    payload = {
        "id": "test_query_1",
        "maxVisits": 100,
        "boardXSize": 19,
        "boardYSize": 19,
        "rules": "tromp-taylor",
        "komi": 6.5,
        "moves": [["B", "Q16"], ["W", "D4"], ["B", "Q4"], ["W", "D16"]],
    }

    try:
        print(f"요청 URL: {url}")
        print("전송할 페이로드(Payload):")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        print("-" * 50)

        response = requests.post(url, json=payload)

        print(f"상태 코드(Status Code): {response.status_code}")

        # JSON 응답 파싱 시도
        try:
            response_data = response.json()
            print("서버 응답(Response):")
            print(json.dumps(response_data, indent=2, ensure_ascii=False))
        except json.JSONDecodeError:
            print("서버 응답(Response - JSON 형태가 아님):")
            print(response.text)

    except requests.exceptions.ConnectionError:
        print(
            "\n[오류] 서버에 연결할 수 없습니다. 터미널에서 서버가 8000 포트에서 실행 중인지 확인해주세요."
        )
    except Exception as e:
        print(f"\n[오류] 알 수 없는 오류가 발생했습니다: {e}")


if __name__ == "__main__":
    # 실행 시 뒤에 모델 ID를 인자로 넘기면 해당 모델로 테스트합니다.
    # 예: python test_analyze_api.py specific_model_name
    input_model_id = "level2"  # 기본 테스트 모델 ID
    if len(sys.argv) > 1:
        input_model_id = sys.argv[1]

    print("API 테스트를 시작합니다...")
    test_analyze_api(input_model_id)
