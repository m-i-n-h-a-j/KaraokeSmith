"""MVSEP API client: run one job for an audio file and download its results."""
import json
import os
import sys
import time
from pathlib import Path

import requests

API = "https://mvsep.com/api"
POLL_SECONDS = 10
PENDING = {"waiting", "processing", "distributing", "merging"}


def load_token() -> str:
    token = os.environ.get("MVSEP_API_TOKEN", "").strip()
    env_file = Path(".env")
    if not token and env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "MVSEP_API_TOKEN":
                token = value.strip().strip("\"'")
    if not token:
        sys.exit("No API token. Set MVSEP_API_TOKEN or put MVSEP_API_TOKEN=... in .env in the current directory.")
    return token


def job_file(audio: Path, kind: str) -> Path:
    return audio.with_name(f"{audio.name}.{kind}.mvsep-job.json")


def create_job(audio: Path, options: dict) -> dict:
    with audio.open("rb") as handle:
        response = requests.post(
            f"{API}/separation/create",
            files={"audiofile": (audio.name, handle)},
            data={"api_token": load_token(), "is_demo": 0, **options},
            timeout=600,
        )
    try:
        payload = response.json()
    except ValueError:
        sys.exit(f"MVSEP returned HTTP {response.status_code}: {response.text[:300]}")
    if not payload.get("success"):
        message = payload.get("errors") or (payload.get("data") or {}).get("message") or payload
        sys.exit(f"MVSEP rejected the job (HTTP {response.status_code}): {message}")
    return payload["data"]


def wait_for_job(job_hash: str) -> dict:
    """Poll until the job is done and return its data. Raises RuntimeError if it failed or is lost."""
    last = None
    while True:
        try:
            payload = requests.get(f"{API}/separation/get", params={"hash": job_hash}, timeout=60).json()
        except (requests.RequestException, ValueError) as error:
            print(f"poll failed, retrying: {error}")
            time.sleep(POLL_SECONDS)
            continue
        status = payload.get("status")
        data = payload.get("data") or {}
        if status == "done":
            return data
        if status not in PENDING:
            raise RuntimeError(f"Job {job_hash} ended with status {status!r}: {data.get('message', payload)}")
        line = status
        if "current_order" in data:
            line += f" (queue position {data['current_order']} of {data.get('queue_count')})"
        if line != last:
            print(line, flush=True)
            last = line
        time.sleep(POLL_SECONDS)


def run_job(audio: Path, kind: str, options: dict) -> dict:
    """Return the data of a finished job for audio, uploading it unless a job is already pending.

    The job file exists only while a job is unfinished, so an interrupted run resumes instead
    of uploading again. The caller deletes it once the results are safely on disk.
    """
    state = job_file(audio, kind)
    if state.exists():
        job = json.loads(state.read_text(encoding="utf-8"))
        print(f"resuming {kind} job {job['hash']}")
    else:
        print(f"uploading {audio} for {kind} ({audio.stat().st_size / 1e6:.1f} MB)", flush=True)
        job = create_job(audio, options)
        state.write_text(json.dumps(job), encoding="utf-8")
        print(f"{kind} job {job['hash']}")
    try:
        return wait_for_job(job["hash"])
    except RuntimeError as error:
        state.unlink()
        sys.exit(str(error))


def save(url: str, target: Path) -> None:
    with requests.get(url, stream=True, timeout=600) as response:
        response.raise_for_status()
        with target.open("wb") as handle:
            for chunk in response.iter_content(1 << 20):
                handle.write(chunk)
    print(f"saved {target} ({target.stat().st_size / 1e6:.1f} MB)")
