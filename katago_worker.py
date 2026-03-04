import subprocess
import threading
import queue
import json

from config import katago_executable_path
from utils.payload_analyzer import get_expected_response_count


class KataGoWorker:
    def __init__(
        self, main_model_path, config_path, is_human=False, human_model_path=None
    ):
        cmd = [
            katago_executable_path,
            "analysis",
            "-model",
            main_model_path,
            "-config",
            config_path,
        ]

        if is_human and human_model_path:
            cmd.extend(["-human-model", human_model_path])

        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # line-buffered
        )

        # 요청 큐 (이 프로세스는 직렬 처리만 가능)
        self.task_queue = queue.Queue()

        # worker thread
        threading.Thread(target=self._worker_loop, daemon=True).start()

        # stderr 로깅 thread
        threading.Thread(target=self._log_stderr, daemon=True).start()

    def _log_stderr(self):
        for line in iter(self.process.stderr.readline, ""):
            print("[KataGo Log]", line.strip())

    def _worker_loop(self):
        while True:
            query_str, expected_lines, future = self.task_queue.get()
            try:
                self.process.stdin.write(query_str)
                self.process.stdin.flush()

                results = []
                for _ in range(expected_lines):
                    result_line = self.process.stdout.readline()
                    # 빈 줄이 들어오는 경우를 방어
                    while result_line.strip() == "":
                        result_line = self.process.stdout.readline()

                    result = json.loads(result_line)
                    results.append(result)

                    # 에러가 발생한 경우 즉시 읽기를 종료하여 무한 대기를 방지
                    if "error" in result:
                        break

                # 결과는 항상 배열(리스트) 형태로 반환합니다.
                future.put(results)

            except Exception as e:
                future.put({"error": f"Internal process error: {str(e)}"})
            finally:
                self.task_queue.task_done()

    def analyze(self, payload: dict, timeout: float = 100.0):
        """payload를 분석하고 결과를 반환 (timeout 초과 시 예외 발생)"""
        expected_lines = get_expected_response_count(payload)

        query_str = json.dumps(payload) + "\n"
        future = queue.Queue(maxsize=1)
        self.task_queue.put((query_str, expected_lines, future))

        try:
            return future.get(timeout=timeout)
        except queue.Empty:
            return {"error": "KataGo response timeout"}
