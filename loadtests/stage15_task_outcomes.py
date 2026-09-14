import json
import os
from collections import Counter

import httpx


base_url = os.getenv("STAGE15_BASE_URL", "http://127.0.0.1:18080")
token = os.environ["STAGE15_AUTH_TOKEN"]
workspace_id = os.environ["STAGE15_WORKSPACE_ID"]
headers = {"Authorization": f"Token {token}", "X-Workspace-ID": workspace_id}
counts = Counter()
task_count = 0
with httpx.Client(base_url=base_url, headers=headers, timeout=10) as client:
    page = 1
    while True:
        response = client.get("/api/processing-tasks/", params={"page": page, "page_size": 100})
        response.raise_for_status()
        data = response.json()["data"]
        items = [item for item in data["items"] if str(item.get("document_name", "")).startswith("stage15-load-")]
        for item in items:
            counts[item["status"]] += 1
            task_count += 1
        if page >= data["total_pages"]:
            break
        page += 1
print(json.dumps({"task_count": task_count, "statuses": counts}, ensure_ascii=False, default=dict))
