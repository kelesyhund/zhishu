import json
import os
import time
import uuid

from locust import HttpUser, between, events, task
from locust.exception import StopUser


def _auth(user):
    token = os.getenv("STAGE15_AUTH_TOKEN", "")
    if not token:
        username = os.getenv("STAGE15_USERNAME", "stage15-load-user")
        password = os.getenv("STAGE15_PASSWORD", "stage15-load-password")
        response = user.client.post(
            "/api/login/", json={"username": username, "password": password},
            name="setup: token login", timeout=5,
        )
        if response.status_code != 200:
            raise StopUser()
        token = response.json()["data"]["token"]
    user.client.headers.update({"Authorization": f"Token {token}"})
    workspace_id = os.getenv("STAGE15_WORKSPACE_ID", "")
    if workspace_id:
        user.client.headers.update({"X-Workspace-ID": workspace_id})
    user.knowledge_id = int(os.getenv("STAGE15_KNOWLEDGE_ID", "1"))


class Stage15ApiUser(HttpUser):
    weight = 4
    wait_time = between(0.1, 0.5)
    network_timeout = 5
    connection_timeout = 5

    def on_start(self):
        _auth(self)

    @task(3)
    def knowledge_list(self):
        self.client.get("/api/knowledge-bases/", params={"page": 1, "page_size": 20}, name="A: knowledge list", timeout=5)

    @task(2)
    def knowledge_detail(self):
        self.client.get(f"/api/knowledge-bases/{self.knowledge_id}/", name="A: knowledge detail", timeout=5)

    @task(1)
    def task_list(self):
        self.client.get("/api/processing-tasks/", params={"page": 1, "page_size": 20}, name="A: task list", timeout=5)


class Stage15SseUser(HttpUser):
    weight = 1
    wait_time = between(0.2, 0.8)
    network_timeout = 120
    connection_timeout = 10

    def on_start(self):
        _auth(self)

    @task
    def stream_once(self):
        started = time.perf_counter()
        first_token_ms = None
        with self.client.post(
            f"/api/knowledge-bases/{self.knowledge_id}/chat/stream/",
            json={"message": "stage15 无敏感内容的固定压测问题"},
            name="B: SSE answer",
            stream=True,
            timeout=120,
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"expected 200, received {response.status_code}")
                return
            saw_done = False
            for line in response.iter_lines(decode_unicode=True):
                if line == "event: content" and first_token_ms is None:
                    first_token_ms = (time.perf_counter() - started) * 1000
                    events.request.fire(
                        request_type="SSE", name="B: first token", response_time=first_token_ms,
                        response_length=0, exception=None, context={},
                    )
                if line == "event: done":
                    saw_done = True
            if first_token_ms is None or first_token_ms > 10000:
                response.failure("first content event missing or exceeded 10 seconds")
            elif not saw_done:
                response.failure("done event missing")


class Stage15UploadUser(HttpUser):
    weight = 1
    wait_time = between(0.01, 0.05)
    network_timeout = 10
    connection_timeout = 10

    def on_start(self):
        _auth(self)
        self.submitted = False

    @task
    def submit_one(self):
        if self.submitted:
            raise StopUser()
        self.submitted = True
        unique = uuid.uuid4().hex
        body = json.dumps({"marker": unique, "purpose": "stage15 temporary load test"}).encode()
        with self.client.post(
            f"/api/knowledge-bases/{self.knowledge_id}/documents/",
            files={"file": (f"stage15-load-{unique}.txt", body, "text/plain")},
            headers={"Idempotency-Key": f"stage15-load-{unique}"},
            name="C: async upload submit",
            timeout=10,
            catch_response=True,
        ) as response:
            if response.status_code != 202:
                response.failure(f"expected 202, received {response.status_code}")
            else:
                data = response.json().get("data", {})
                if not data.get("task") or not data.get("document"):
                    response.failure("202 response omitted document/task")
        raise StopUser()
