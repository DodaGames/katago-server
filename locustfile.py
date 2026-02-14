from locust import HttpUser, task, between
from utils import generate_random_moves

NUM_DIMENSION = 19
NUM_MOVES = 20
MAX_VISITS = 10


class KataGoUser(HttpUser):
    wait_time = between(1, 5)

    @task
    def analyze(self):
        payload = {
            "id": "test1",
            "moves": generate_random_moves(NUM_DIMENSION, NUM_MOVES),
            "rules": "korean",
            "komi": 6.5,
            "boardXSize": 19,
            "boardYSize": 19,
            "overrideSettings": {"maxVisits": MAX_VISITS},
        }
        self.client.post("/analyze", json=payload)

    @task
    def human_play(self):
        payload = {
            "id": "test2",
            "moves": generate_random_moves(NUM_DIMENSION, NUM_MOVES),
            "rules": "korean",
            "komi": 6.5,
            "boardXSize": 19,
            "boardYSize": 19,
            "overrideSettings": {
                "humanSLProfile": "rank_5d",
                "ignorePreRootHistory": False,
                "maxVisits": MAX_VISITS,
            },
            "includePolicy": True,
        }
        self.client.post("/humanplay", json=payload)
