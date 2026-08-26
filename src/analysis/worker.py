import subprocess
import threading
import queue
import json
import logging
import signal
import uuid
import asyncio

from .config import katago_executable_path
from .payload_analyzer import get_expected_response_count

logger = logging.getLogger("uvicorn.error")


class KataGoWorker:
    def __init__(
        self,
        main_model_path,
        config_path,
        is_human=False,
        human_model_path=None,
        model_id=None,
    ):
        # 로그에서 어느 모델의 워커인지 식별하기 위한 라벨(로깅 전용).
        self.model_id = model_id or main_model_path

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

        # 쓰기 전용 큐 (병렬 처리를 위해 즉각적으로 전달)
        self.write_queue = queue.Queue()

        # 내부 ID 매핑용 딕셔너리 및 락
        # 내부 ID -> (Asyncio Future, expected_lines, accumulated_results, caller_loop)
        self.futures = {}
        self.futures_lock = threading.Lock()

        # 프로세스 종료를 stdout/stderr 리더 중 한쪽만 기록하도록 하는 가드
        self._exit_logged = threading.Event()

        # writer, reader thread 분리
        threading.Thread(target=self._writer_loop, daemon=True).start()
        threading.Thread(target=self._reader_loop, daemon=True).start()
        threading.Thread(target=self._log_stderr, daemon=True).start()

    def _handle_process_exit(self, source: str):
        """KataGo 프로세스가 죽으면 stdout/stderr 리더가 EOF로 조용히 끝난다.
        아무것도 남기지 않으면 이후 모든 요청이 타임아웃으로만 드러나므로
        (원인은 알 수 없는 채로), 먼저 EOF에 도달한 리더가 한 번만 기록한다.
        """
        if self._exit_logged.is_set():
            return
        self._exit_logged.set()

        try:
            returncode = self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            returncode = None

        # SIGTERM(-15)은 `systemctl stop`이 cgroup 전체에 보내는 정상 종료 경로다.
        # 이걸 에러로 찍으면 정상 재시작마다 거짓 경보가 남는다.
        if returncode == -signal.SIGTERM:
            logger.info(
                f"[KataGo Worker] model={self.model_id} 프로세스 정상 종료 (source={source})"
            )
            return

        logger.error(
            f"[KataGo Worker] model={self.model_id} 프로세스가 예기치 않게 종료됨 "
            f"(source={source}, returncode={returncode}). "
            f"이 워커로 가는 요청은 이후 전부 타임아웃된다 — 서비스 재시작 필요."
        )

    def _log_stderr(self):
        for line in iter(self.process.stderr.readline, ""):
            print("[KataGo Log]", line.strip())
        self._handle_process_exit("stderr")

    def _writer_loop(self):
        while True:
            # 큐에서 들어오는 즉시 KataGo로 밀어넣어 병렬 처리(Batch) 유도
            query_str = self.write_queue.get()
            try:
                self.process.stdin.write(query_str)
                self.process.stdin.flush()
            except Exception as e:
                print(f"[KataGo Worker Error] Failed to write to stdin: {e}")
            finally:
                self.write_queue.task_done()

    def _reader_loop(self):
        for result_line in iter(self.process.stdout.readline, ""):
            if not result_line.strip():
                continue

            try:
                result = json.loads(result_line)
                query_id = result.get("id")

                with self.futures_lock:
                    if query_id in self.futures:
                        future, expected_lines, results_list, loop = self.futures[query_id]
                        results_list.append(result)

                        # 에러 발생 또는 예상 라인 수를 모두 읽었을 경우 완료 처리
                        if "error" in result or len(results_list) >= expected_lines:
                            del self.futures[query_id]
                            # FastAPI 스레드의 asyncio loop에 스레드 안전하게 완료 이벤트 전달
                            loop.call_soon_threadsafe(future.set_result, results_list)

            except Exception as e:
                print(f"[KataGo Worker Error] Failed to parse stdout: {e}, line: {result_line}")

        self._handle_process_exit("stdout")

    async def analyze(self, payload: dict, timeout: float = 100.0):
        """payload를 분석하고 결과를 반환 (timeout 초과 시 반환)"""
        expected_lines = get_expected_response_count(payload)

        internal_id = str(uuid.uuid4())
        external_id = payload.get("id")

        # KataGo 병렬 엔진 내부에서 여러 요청이 섞이지 않도록 내부 UUID로 덮어씌움
        payload["id"] = internal_id

        loop = asyncio.get_running_loop()
        future = loop.create_future()

        with self.futures_lock:
            self.futures[internal_id] = (future, expected_lines, [], loop)

        query_str = json.dumps(payload) + "\n"
        self.write_queue.put(query_str)

        try:
            results = await asyncio.wait_for(future, timeout=timeout)
            # 클라이언트에게 돌려줄 때는 원래의 요청 ID로 복원
            for r in results:
                r["id"] = external_id
            return results
        except asyncio.TimeoutError:
            with self.futures_lock:
                if internal_id in self.futures:
                    del self.futures[internal_id]
            return {"error": "KataGo response timeout"}

    def is_alive(self) -> bool:
        """KataGo 프로세스 생존 여부.

        주의: "프로세스가 살아있다"이지 "응답할 수 있다"가 아니다. GPU가 행에 걸려
        프로세스는 떠 있는데 응답만 못 하는 상태는 이 값으로 잡히지 않는다
        (그 경우는 모델별 타임아웃 비율로 판단한다 — monitoring/rules.py).
        """
        return self.process.poll() is None

    def get_stats(self) -> dict:
        """모니터링용 큐 depth 및 대기 중인 요청 수 스냅샷"""
        with self.futures_lock:
            pending_requests = len(self.futures)

        return {
            "queue_size": self.write_queue.qsize(),
            "pending_requests": pending_requests,
            "process_alive": self.is_alive(),
        }
