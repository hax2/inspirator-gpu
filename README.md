# Inspirator GPU server

An authenticated, queued image-generation API for the Inspirator site. It runs on a Linux GPU server without Docker or systemd.

Defaults match the supplied H100 NVL MIG allocation: one visible CUDA device with approximately 47 GB VRAM, one image worker, BF16 inference, and a 65% PyTorch allocator limit to leave room for the existing TTS process. The installer does not change GPU drivers, CUDA/MIG configuration, system Python packages, or other virtual environments.

## Install on the server

Clone this repository, then run from its root:

```bash
cd inspirator-gpu
bash scripts/install.sh
bash scripts/start.sh
tail -f logs/server.log
```

If `git` or Python's venv support is missing on Ubuntu, install those OS packages first:

```bash
apt-get update
apt-get install -y git python3-venv
```

Python 3.11+ is required. Set `PYTHON_BIN=python3.12` before running the installer if your machine has several Python versions.

The installer creates `.venv`, installs a pinned CUDA-enabled PyTorch wheel plus Diffusers, creates an API key in `.env`, checks CUDA/BF16 operation, and downloads `black-forest-labs/FLUX.2-klein-4B`. It records the downloaded model revision in `.env` for repeatable loading and installed package versions in `data/installed-versions.txt`. The first download is substantial; wait for it to finish.

`nvidia-smi`'s CUDA version describes the driver capability. The PyTorch installation includes its own CUDA 12.6 runtime; this does not replace the server's CUDA installation. The existing notebook's `CUDA_VISIBLE_DEVICES` setting is respected. Do not point it at another MIG slice without the server owner's allocation.

The model stays in GPU memory once loaded. `/livez` returns HTTP 200 while the process is alive; `/health` returns 503 while the model loads and 200 when ready:

```bash
curl http://127.0.0.1:8010/health
.venv/bin/python scripts/smoke.py
```

The smoke test submits one image, polls its existing job ID, and writes `data/smoke.jpg`. It reads the API key from `.env` without printing it.

## Start, stop, update

```bash
bash scripts/start.sh
bash scripts/stop.sh
# With the service stopped:
git pull --ff-only
bash scripts/install.sh
bash scripts/start.sh
```

`start.sh` uses nohup, so no systemd or Docker daemon is needed. `foreground.sh` runs the same service directly for a notebook terminal or process supervisor. One process owns an OS file lock; do not start several Uvicorn workers for the same GPU/data directory.

Logs are appended to `logs/server.log`. Job metadata, input references, completed images, model cache, and runtime version records are stored under `data/` by default and excluded from Git. Set `DATA_DIR` and `HF_HOME` to persistent storage if the notebook's local filesystem is ephemeral. Monitor disk usage; this first version retains generated images rather than deleting them automatically.

## Expose the API to Vercel

The default bind address is **127.0.0.1:8010**. Put an HTTPS reverse proxy or the GPU provider's authenticated/managed HTTPS port forwarding in front of it. A sample Caddy configuration is in `scripts/Caddyfile.example`.

If your provider requires binding a forwarded port on all interfaces, change `HOST=0.0.0.0` in `.env` and restart. Keep HTTPS at the ingress and the bearer token enabled. No browser CORS access is needed: Vercel's backend calls this service.

`IMAGE_API_KEY` is created locally in `.env` with file mode 0600. Copy it privately into the Vercel project's server-side environment settings when the website adapter is connected. Do not paste it into chat, frontend code, shell history, or Git.

## API contract

All `/v1/*` endpoints require `Authorization: Bearer <IMAGE_API_KEY>`. Health endpoints are public and expose only readiness status.

### Submit: `POST /v1/jobs`

```json
{
  "request_id": "880f6a33-661f-4456-8254-5e7c89408a38",
  "prompts": [
    "A sculptural terracotta lamp with an organic silhouette",
    "A sculptural terracotta lamp with expressive negative space"
  ],
  "references": [],
  "width": 768,
  "height": 768,
  "seed": 42,
  "max_run_seconds": 300
}
```

HTTP 202 returns the job ID and current progress. Supply 1–50 prompts and up to four JPG/PNG/WebP reference images as base64 data URIs. Reference URLs are rejected; the server does not fetch arbitrary network addresses. Width and height must be 768×768, 1024×768, 768×1024, or 1024×1024. The worker uses four inference steps and guidance 1.0. For edits, include the original design among the references and describe which image to preserve in the prompt.

The same `request_id` and normalized payload always return the same job. Reusing an ID with different inputs returns 409. New requests return 429 when the active queue is full. There are no automatic retries of failed inference.

### Poll: `GET /v1/jobs/{job_id}`

```json
{
  "id": "JOB_UUID",
  "state": "succeeded",
  "total": 2,
  "completed": 2,
  "server_runtime_seconds": 3.7,
  "usage_complete": true,
  "estimated_cost_eur_micros": null,
  "images": [
    {"id": "IMAGE_UUID", "position": 0, "path": "/v1/jobs/JOB_UUID/images/IMAGE_UUID"}
  ]
}
```

This is a response-shape example, **not a performance benchmark**. Job states are `queued`, `running`, `succeeded`, `failed`, `timed_out`, and `interrupted`. Images become available individually as they finish.

### Download: `GET /v1/jobs/{job_id}/images/{image_id}`

Returns an authenticated JPEG with private/no-store cache headers. The Vercel adapter should copy completed images to the site's private storage. The API key is a backend service credential, not an end-user credential; website routes must continue enforcing account ownership and subscriptions before submitting or retrieving jobs.

## Runtime and cost controls

`MAX_ACTIVE_JOBS` defaults to 16. `MAX_JOB_SECONDS` defaults to 300. Deadline checks occur between images and at diffusion-step boundaries. They are **not a hard watchdog** against a hung GPU kernel. Model loading and queue wait are excluded from reported job runtime. Progress and completed files survive normal restarts; an in-flight job becomes `interrupted` and is never silently regenerated. A crash can leave its final cost unknown.

If `GPU_HOURLY_EUR` is set, completed jobs report an estimate derived from elapsed server processing time. Unknown/interrupted jobs return `usage_complete: false` and no cost estimate. This is not an invoice or a guarantee of total rental expense. Idle GPU rental, storage, bandwidth and the other TTS workload remain separate costs.

The existing website currently calls Runware. **It will need a self-hosted provider adapter** to submit/poll these jobs and translate its EUR 8 allowance into a conservative GPU-runtime reservation. Merely changing Runware's URL will not work. No website deployment is changed by installing this repository, and the service does not pretend that self-hosted GPU usage has zero cost. Once the HTTPS endpoint is reachable, the adapter can use `IMAGE_API_URL` and `IMAGE_API_KEY` in Vercel.

## Validation

API, queue and worker persistence tests run without downloading the image model or requiring a GPU:

```bash
python -m pip install fastapi Pillow httpx
python -m unittest discover -s tests -v
```

The local suite covers authentication, request validation, body limits, atomic job claims, idempotency under concurrent submission, queue limits, protected image reads, partial failures, restart recovery, accounting uncertainty and runtime limits. GPU inference is replaced with a fake engine in these tests. Actual CUDA/model installation and generation must be validated with the provided smoke test on your server.

## Upstream references

- [FLUX.2 Klein 4B model card and Apache 2.0 model license](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B)
- [Diffusers FLUX.2 pipeline documentation](https://huggingface.co/docs/diffusers/api/pipelines/flux2)
- [Official PyTorch CUDA wheel installation commands](https://pytorch.org/get-started/previous-versions/)
