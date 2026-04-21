import random
from locust import HttpUser, task, between
from utils import generate_random_moves

NUM_DIMENSION = 7
NUM_MOVES = 30
MAX_VISITS = 100


class KataGoUser(HttpUser):
    wait_time = between(1, 5)

    @task
    def analyze(self):
        payload = {
            "id": "test1",
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
        self.client.post(f"/analyze/{model_id}", json=payload)
