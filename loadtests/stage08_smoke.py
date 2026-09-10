import json
import os
import time
import uuid

import httpx


base_url = os.getenv("STAGE08_BASE_URL", "http://127.0.0.1:18000")
username = os.getenv("STAGE08_USERNAME", "stage08-load-user")
password = os.getenv("STAGE08_PASSWORD", "stage08-load-password")
knowledge_name = os.getenv("STAGE08_KNOWLEDGE_NAME", "stage08-loadtest-kb")

with httpx.Client(base_url=base_url, timeout=30) as client:
    login = client.post("/api/login/", json={"username": username, "password": password})
    login.raise_for_status()
    client.headers["Authorization"] = f"Token {login.json()['data']['token']}"
    listing = client.get("/api/knowledge-bases/", params={"keyword": knowledge_name, "page_size": 100})
    listing.raise_for_status()
    knowledge = next(
        item for item in listing.json()["data"]["items"] if item["name"] == knowledge_name
    )
    suffix = uuid.uuid4().hex[:10]
    started = time.perf_counter()
    upload = client.post(
        f"/api/knowledge-bases/{knowledge['id']}/documents/",
        files={
            "file": (
                f"stage08-smoke-{suffix}.txt",
                "真实Redis和Celery异步处理冒烟测试。切片与文件会在验收后清理。".encode("utf-8"),
                "text/plain",
            )
        },
        headers={"Idempotency-Key": f"stage08-smoke-{suffix}"},
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    if upload.status_code != 202:
        raise RuntimeError(f"upload returned {upload.status_code}: {upload.text}")
    payload = upload.json()["data"]
    document_id = payload["document"]["id"]
    task_id = payload["task"]["id"]
    observed = [payload["task"]["status"]]
    deadline = time.monotonic() + 30
    task = payload["task"]
    while task["status"] in {"PENDING", "PROCESSING", "RETRYING", "CANCEL_REQUESTED"}:
        if time.monotonic() >= deadline:
            raise TimeoutError("processing task did not finish within 30 seconds")
        time.sleep(0.2)
        response = client.get(
            f"/api/knowledge-bases/{knowledge['id']}/processing-tasks/{task_id}/"
        )
        response.raise_for_status()
        task = response.json()["data"]
        if task["status"] != observed[-1]:
            observed.append(task["status"])
    if task["status"] != "SUCCESS":
        raise RuntimeError(f"processing task failed safely: {task['error_message']}")
    paragraphs = client.get(
        f"/api/knowledge-bases/{knowledge['id']}/documents/{document_id}/paragraphs/"
    )
    paragraphs.raise_for_status()
    paragraph_count = paragraphs.json()["data"]["total"]
    print(
        json.dumps(
            {
                "http_status": upload.status_code,
                "submit_ms": elapsed_ms,
                "document_id": document_id,
                "task_id": task_id,
                "observed_statuses": observed,
                "final_progress": task["progress"],
                "paragraph_count": paragraph_count,
            },
            ensure_ascii=False,
        )
    )
