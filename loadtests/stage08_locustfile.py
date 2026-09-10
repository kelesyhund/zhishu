import os
import uuid

from locust import HttpUser, between, task
from locust.exception import StopUser


class StageEightUploadUser(HttpUser):
    wait_time = between(0.01, 0.05)
    network_timeout = 30.0
    connection_timeout = 30.0

    def on_start(self):
        auth_token = os.getenv("STAGE08_AUTH_TOKEN", "")
        knowledge_id = os.getenv("STAGE08_KNOWLEDGE_ID", "")
        if auth_token and knowledge_id:
            self.client.headers.update({"Authorization": f"Token {auth_token}"})
            self.knowledge_id = int(knowledge_id)
            self.submitted = False
            return
        username = os.getenv("STAGE08_USERNAME", "stage08-load-user")
        password = os.getenv("STAGE08_PASSWORD", "stage08-load-password")
        knowledge_name = os.getenv("STAGE08_KNOWLEDGE_NAME", "stage08-loadtest-kb")
        with self.client.post(
            "/api/login/",
            json={"username": username, "password": password},
            name="setup: login",
            timeout=30,
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"login returned {response.status_code}")
                raise StopUser()
            token = response.json()["data"]["token"]
        self.client.headers.update({"Authorization": f"Token {token}"})
        response = self.client.get(
            "/api/knowledge-bases/",
            params={"keyword": knowledge_name, "page_size": 100},
            name="setup: locate knowledge base",
            timeout=30,
        )
        items = response.json().get("data", {}).get("items", []) if response.ok else []
        match = next((item for item in items if item.get("name") == knowledge_name), None)
        if not match:
            raise StopUser()
        self.knowledge_id = match["id"]
        self.submitted = False

    @task
    def submit_one_document(self):
        if self.submitted:
            return
        self.submitted = True
        unique_id = uuid.uuid4().hex
        filename = f"stage08-load-{unique_id}.txt"
        body = (
            f"Stage 8 load test document {unique_id}. "
            "This content uses deterministic local embeddings and contains no sensitive data."
        ).encode("utf-8")
        with self.client.post(
            f"/api/knowledge-bases/{self.knowledge_id}/documents/",
            files={"file": (filename, body, "text/plain")},
            headers={"Idempotency-Key": unique_id},
            name="POST async document upload",
            timeout=30,
            catch_response=True,
        ) as response:
            if response.status_code != 202:
                response.failure(f"expected 202, received {response.status_code}")
            else:
                payload = response.json().get("data", {})
                if not payload.get("document") or not payload.get("task"):
                    response.failure("202 response is missing document/task")
