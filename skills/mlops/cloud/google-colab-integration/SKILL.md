---
name: google-colab-integration
title: Google Colab for On-Demand GPU
description: Google Colab integration patterns -- notebook automation, worker-based GPU offloading, and Colab Enterprise via Vertex AI. Includes realistic constraints and alternatives.
categories: [mlops-cloud, mlops]
---

## Overview

Google Colab provides free T4 GPUs with ~12h sessions. No official execution API. For headless GPU compute, prefer Kaggle or Colab Enterprise (Vertex AI).

## Prerequisites

- Python 3.9+ with `httpx` (`pip install httpx`) for agent-side task polling
- Method 1: A render.com-hosted task queue (or any HTTP endpoint) with a shared secret
- Method 2: Google Drive API enabled + OAuth credentials (`pip install google-api-python-client`)
- Method 3: GCP project with Vertex AI billing enabled (`gcloud auth login`)

## When to Use Each Method

| Scenario | Method |
|----------|--------|
| You can leave a browser tab open with a notebook | Method 1 (Worker Pattern) |
| You need to create/share notebooks for manual review | Method 2 (Drive Upload) |
| Production workloads needing guaranteed GPU + API | Method 3 (Vertex AI, paid) |
| Headless GPU without browser | Use **kaggle-compute** skill instead |

## Method 1: Colab Worker Pattern (Most Practical)

A Colab notebook runs a worker loop polling for tasks. The agent pushes tasks, worker executes them on the GPU.

**Colab notebook (run manually in browser):**
```python
# Cell 1: Setup
import requests, time, torch, traceback
API_URL = "https://hermes-agent-api.onrender.com"
WORKER_SECRET = "your-shared-secret"
print(f"GPU: {torch.cuda.is_available()}")

# Cell 2: Worker loop
def poll():
    try:
        r = requests.get(f"{API_URL}/tasks/next",
            params={"worker_id": "colab-gpu-1", "capability": "gpu"},
            headers={"Authorization": f"Bearer {WORKER_SECRET}"}, timeout=30)
        return r.json().get("task") if r.status_code == 200 else None
    except: return None

def report(task_id, result):
    requests.post(f"{API_URL}/tasks/{task_id}/complete",
        json={"worker_id": "colab-gpu-1", "result": result},
        headers={"Authorization": f"Bearer {WORKER_SECRET}"}, timeout=30)

empty = 0
while True:
    task = poll()
    if task:
        empty = 0
        try:
            exec_globals = {}
            exec(task.get("code", ""), exec_globals)
            report(task["id"], {"status": "success", "data": exec_globals.get("result")})
        except Exception as e:
            report(task["id"], {"status": "error", "error": str(e)})
    else:
        empty += 1
        time.sleep(min(5 * empty, 60))
```

**Agent side:**
```python
import httpx, uuid, time

class ColabWorkerClient:
    def __init__(self, api_url: str, secret: str):
        self.url, self.secret = api_url, secret

    def submit(self, code: str, timeout: int = 300):
        task_id = str(uuid.uuid4())
        httpx.post(f"{self.url}/tasks/submit",
            json={"id": task_id, "code": code, "require_gpu": True},
            headers={"Authorization": f"Bearer {self.secret}"}, timeout=30).raise_for_status()
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = httpx.get(f"{self.url}/tasks/{task_id}/result",
                headers={"Authorization": f"Bearer {self.secret}"}, timeout=30)
            if r.status_code == 200 and r.json().get("status") in ("success","error"):
                return r.json()
            time.sleep(5)
        return {"status": "timeout"}
```

## Method 2: Create Notebooks in Drive (Manual Execution)

```python
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import json

def create_colab_notebook(drive_service, title: str, code: str) -> str:
    nb = {"nbformat": 4, "nbformat_minor": 0,
          "metadata": {"colab": {"name": title, "accelerator": "GPU"},
                       "kernelspec": {"name": "python3"}},
          "cells": [{"cell_type": "code", "source": [code], "outputs": []}]}
    with open("/tmp/nb.ipynb", "w") as f:
        json.dump(nb, f)
    media = MediaFileUpload("/tmp/nb.ipynb", mimetype="application/json")
    file = drive_service.files().create(
        body={"name": f"{title}.ipynb", "mimeType": "application/vnd.google.colaboratory"},
        media_body=media, fields="id").execute()
    return f"https://colab.research.google.com/drive/{file['id']}"
```

## Method 3: Colab Enterprise via Vertex AI (Production, Paid)

```python
from google.cloud import aiplatform
aiplatform.init(project="my-project", location="us-central1")
job = aiplatform.CustomJob.from_local_script(
    display_name="agent-task", script_path="./task.py",
    container_uri="us-docker.pkg.dev/vertex-ai/training/tf-gpu.2-14.py310:latest",
    machine_type="n1-standard-4",
    accelerator_type="NVIDIA_TESLA_T4", accelerator_count=1)
job.run(sync=False)
```

## Colab Limits (2024-2025)

| Free | Pro ($10/mo) | Pro+ ($50/mo) |
|------|---------------|---------------|
| T4/K80, ~12h max | T4/V100 priority | A100, background exec |
| ~12.7 GB RAM | High-RAM option | High-RAM available |
| ~90 min idle timeout | Longer idle | Background mode |
| NO API | NO API | Vertex AI API |

## Quick Start (Agent-Side Setup)

Before using any method, verify your agent can reach external services:

```bash
# 1. Verify httpx is available (needed for all methods)
python3 -c "import httpx; print('httpx OK')"

# 2. Test network connectivity to Colab
python3 -c "import httpx; r = httpx.get('https://colab.research.google.com', timeout=10); print(f'Colab reachable: {r.status_code}')"

# 3. For Method 2: verify Google Drive API auth
python3 -c "from googleapiclient.discovery import build; print('google-api-python-client OK')"

# 4. For Method 3: verify gcloud auth for Vertex AI
gcloud auth list --filter=status:ACTIVE  # Should show active account
gcloud projects list --filter=my-project  # Should show your project
```

## Verification

After setting up a method, verify it works end-to-end:

**Method 1 (Worker):**
1. Open the Colab notebook in browser, run Cell 1 — confirm `GPU: True` output
2. Run Cell 2 (worker loop) — confirm no immediate errors, worker is polling
3. From agent side, create a `ColabWorkerClient` and submit a trivial task:
   ```python
   client = ColabWorkerClient("https://your-api-url.com", "your-secret")
   result = client.submit("import torch; result = torch.cuda.get_device_name(0)", timeout=60)
   # Expected: {"status": "success", "data": "Tesla T4"} or similar GPU name
   ```
4. Check task completes within 60s. If it times out, verify: worker is running, API_URL/secret match, Render.com service is not sleeping.

**Method 2 (Drive):**
1. Authenticate: `gcloud auth application-default login`
2. Run `create_colab_notebook(drive_svc, "test", "print('hello')")` — returns a Colab URL
3. Open the URL in browser — confirm notebook renders with GPU accelerator badge

**Method 3 (Vertex AI):**
1. Set quota: `gcloud services enable aiplatform.googleapis.com --project=my-project`
2. Run the example script — confirm CustomJob appears in Vertex AI console
3. `gcloud ai custom-jobs list --region=us-central1 --project=my-project` — shows your job

## Pitfalls
- No official REST API for consumer Colab execution
- Sessions disconnect after ~90 min idle or ~12h max
- GPU not guaranteed -- may get CPU only
- **Render.com free tier sleeps after 15 min of inactivity** — worker pattern will fail until service wakes (~30s cold start). Use `curl` heartbeat from a cron job to keep it alive.
- **`exec()` in worker has no sandbox** — untrusted code can access Colab's environment, files, and network keys. Only submit code you trust.
- **Colab silently downgrades GPU** during peak hours (T4 → K80 → CPU). Always check `torch.cuda.is_available()` in Cell 1 before running worker loop.
- **OAuth tokens for Method 2 expire after 1 hour** — use refresh tokens or service accounts for long-running automation.
- For production GPU use: Kaggle (free), Modal.com, or Vertex AI (paid)