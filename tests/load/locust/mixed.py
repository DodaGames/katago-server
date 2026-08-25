import random
from locust import HttpUser, task, between
from utils import generate_random_moves


class MixedKataGoUser(HttpUser):
    wait_time = between(1, 5)

    # 20의 가중치 부여 (21번 중 20번 비율로 실행)
    @task(20)
    def next_move(self):
        NUM_DIMENSION = 7
        NUM_MOVES = 20
        MAX_VISITS = 100

        payload = {
            "id": "test_next",
            "moves": generate_random_moves(NUM_DIMENSION, NUM_MOVES),
            "rules": "korean",
            "komi": 6.5,
            "boardXSize": NUM_DIMENSION,
            "boardYSize": NUM_DIMENSION,
            "maxVisits": MAX_VISITS,
            "includePolicy": True,
            "overrideSettings": {},
        }
        model_id = "level3"
        self.client.post(
            f"/analyze/{model_id}", json=payload, name="/analyze/{next_move}"
        )

    # 1의 가중치 부여 (21번 중 1번 비율로 실행)
    @task(1)
    def review(self):
        NUM_DIMENSION = 7
        NUM_MOVES = 30
        MAX_VISITS = 200

        payload = {
            "id": "test_review",
            "moves": generate_random_moves(NUM_DIMENSION, NUM_MOVES),
            "analyzeTurns": [x for x in range(0, NUM_MOVES + 1)],
            "rules": "korean",
            "komi": 6.5,
            "boardXSize": NUM_DIMENSION,
            "boardYSize": NUM_DIMENSION,
            "maxVisits": MAX_VISITS,
            "overrideSettings": {},
            "includeOwnership": True,
            "includeOwnershipStdev": True,
            "includePolicy": False,
        }
        model_id = "best"
        self.client.post(f"/analyze/{model_id}", json=payload, name="/analyze/{review}")
