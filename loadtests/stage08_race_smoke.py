import json
import os
import uuid

import httpx


base_url = os.getenv("STAGE08_BASE_URL", "http://127.0.0.1:18000")
auth_token = os.getenv("STAGE08_AUTH_TOKEN", "stage08-load-token-000000000000000000000")
knowledge_id = int(os.getenv("STAGE08_RACE_KNOWLEDGE_ID", "2"))
suffix = uuid.uuid4().hex[:10]

with httpx.Client(
    base_url=base_url,
    headers={"Authorization": f"Token {auth_token}"},
    timeout=30,
) as client:
    def upload(prefix: str):
        name = f"stage08-race-{prefix}-{suffix}.txt"
        response = client.post(
            f"/api/knowledge-bases/{knowledge_id}/documents/",
            files={"file": (name, b"queued race verification", "text/plain")},
            headers={"Idempotency-Key": f"stage08-race-{prefix}-{suffix}"},
        )
        if response.status_code != 202:
            raise RuntimeError(f"upload {prefix} returned {response.status_code}")
        return name, response.json()["data"]

    cancel_name, cancel_payload = upload("cancel")
    cancel_response = client.post(
        f"/api/knowledge-bases/{knowledge_id}/processing-tasks/{cancel_payload['task']['id']}/cancel/"
    )
    cancel_response.raise_for_status()

    delete_name, delete_payload = upload("delete")
    delete_response = client.delete(
        f"/api/knowledge-bases/{knowledge_id}/documents/{delete_payload['document']['id']}/"
    )
    delete_response.raise_for_status()

    print(
        json.dumps(
            {
                "cancel_file": cancel_name,
                "cancel_document_id": cancel_payload["document"]["id"],
                "cancel_task_id": cancel_payload["task"]["id"],
                "cancel_status": cancel_response.json()["data"]["status"],
                "delete_file": delete_name,
                "delete_document_id": delete_payload["document"]["id"],
                "delete_task_id": delete_payload["task"]["id"],
                "delete_response": delete_response.status_code,
            },
            ensure_ascii=False,
        )
    )
