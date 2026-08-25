import random
from locust import HttpUser, task, between
from utils import generate_random_moves

NUM_DIMENSION = 19
NUM_MOVES = 20
MAX_VISITS = 50


class KataGoUser(HttpUser):
    wait_time = between(1, 5)

    @task
    def analyze(self):
        payload = {
            "id": "test1",
            "moves": generate_random_moves(NUM_DIMENSION, NUM_MOVES),
            "rules": "korean",
            "komi": 6.5,
            "boardXSize": NUM_DIMENSION,
            "boardYSize": NUM_DIMENSION,
            "overrideSettings": {"maxVisits": MAX_VISITS},
        }
        model_id = "level3"
        self.client.post(f"/analyze/{model_id}", json=payload)
