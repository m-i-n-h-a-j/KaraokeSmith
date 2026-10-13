# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "requests<3",
# ]
# ///
"""Make a karaoke track from an audio file with the MVSEP API (MVSep Karaoke, sep_type 49).

Writes one file next to the input, <name>_karaoke.flac: the mix with only the lead vocals
removed (back vocals and instruments stay). --all-stems also saves every stem to --out.
Every saved file is loudness-normalized with ffmpeg to -14 LUFS / -1.0 dBTP and encoded as
48 kHz 16-bit FLAC. The job hash is kept in <out>/job.json, so a rerun resumes or
re-downloads that job instead of uploading again. Delete job.json (or pick another --out)
for a new job.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests

API = "https://mvsep.com/api"
SEP_TYPE_KARAOKE = 49
HERE = Path(__file__).resolve().parent
PENDING = {"waiting", "processing", "distributing", "merging"}
KARAOKE_STEM = "back instrum"  # MVSEP file "type" for everything except the lead vocals
TARGET_LUFS = -14.0
TARGET_TRUE_PEAK = -1.0  # dBTP


def load_token() -> str:
    token = os.environ.get("MVSEP_API_TOKEN", "").strip()
    env_file = HERE / ".env"
    if not token and env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "MVSEP_API_TOKEN":
                token = value.strip().strip("\"'")
    if not token:
        sys.exit("No API token. Set MVSEP_API_TOKEN or put MVSEP_API_TOKEN=... in .env next to this script.")
    return token


def create_job(audio: Path, token: str, model: int, extract_first: bool, output_format: int) -> dict:
    with audio.open("rb") as handle:
        response = requests.post(
            f"{API}/separation/create",
            files={"audiofile": (audio.name, handle)},
            data={
                "api_token": token,
                "sep_type": SEP_TYPE_KARAOKE,
                "add_opt1": model,
                "add_opt2": int(extract_first),
                "output_format": output_format,
                "is_demo": 0,
            },
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


def wait_for_files(job_hash: str, interval: int) -> list[dict]:
    last = None
    while True:
        try:
            payload = requests.get(f"{API}/separation/get", params={"hash": job_hash}, timeout=60).json()
        except (requests.RequestException, ValueError) as error:
            print(f"poll failed, retrying: {error}")
            time.sleep(interval)
            continue
        status = payload.get("status")
        data = payload.get("data") or {}
        if status == "done":
            return data["files"]
        if status not in PENDING:
            sys.exit(f"Job {job_hash} ended with status {status!r}: {data.get('message', payload)}")
        line = status
        if "current_order" in data:
            line += f" (queue position {data['current_order']} of {data.get('queue_count')})"
        if line != last:
            print(line, flush=True)
            last = line
        time.sleep(interval)


def loudnorm(source: Path, measured: str, output: list[str]) -> dict:
    """Run ffmpeg's loudnorm filter over source and return the stats it reports."""
    filters = f"loudnorm=I={TARGET_LUFS}:TP={TARGET_TRUE_PEAK}:{measured}print_format=json"
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-y", "-i", str(source), "-af", filters, *output],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        sys.exit(f"ffmpeg failed on {source}:\n{result.stderr[-600:]}")
    return json.loads(result.stderr[result.stderr.rindex("{"):result.stderr.rindex("}") + 1])


def normalize(path: Path) -> Path:
    """Replace path with a -14 LUFS / -1.0 dBTP, 48 kHz 16-bit FLAC (two-pass loudnorm)."""
    measured = loudnorm(path, "", ["-f", "null", "-"])
    # A target LRA below the measured one drops loudnorm out of linear mode, which changes dynamics.
    lra = min(50.0, max(7.0, float(measured["input_lra"])))
    second_pass = (
        f"LRA={lra}:measured_I={measured['input_i']}:measured_LRA={measured['input_lra']}:"
        f"measured_TP={measured['input_tp']}:measured_thresh={measured['input_thresh']}:"
        f"offset={measured['target_offset']}:linear=true:"
    )
    temporary = path.with_name(f"{path.stem}.tmp.flac")
    stats = loudnorm(path, second_pass, ["-ar", "48000", "-sample_fmt", "s16", "-c:a", "flac", str(temporary)])
    final = path.with_suffix(".flac")
    temporary.replace(final)
    if final != path:
        path.unlink()
    print(f"normalized {final}: {stats['input_i']} -> {stats['output_i']} LUFS, "
          f"true peak {stats['output_tp']} dBTP, {stats['normalization_type']} mode")
    return final


def save(url: str, target: Path) -> None:
    """Download url to target, then normalize it."""
    with requests.get(url, stream=True, timeout=600) as response:
        response.raise_for_status()
        with target.open("wb") as handle:
            for chunk in response.iter_content(1 << 20):
                handle.write(chunk)
    print(f"saved {target} ({target.stat().st_size / 1e6:.1f} MB)")
    normalize(target)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("audio", nargs="?", type=Path, default=Path("og.flac"))
    parser.add_argument("--out", type=Path, default=Path("karaoke"), help="directory for job.json and --all-stems")
    parser.add_argument("--all-stems", action="store_true", help="also save every stem MVSEP returns to --out")
    parser.add_argument("--model", type=int, default=6,
                        help="add_opt1, 0-8. 6 = BS Roformer by MVSep Team (default), "
                             "8 = BS Roformer 124 bands (premium accounts only)")
    parser.add_argument("--extract-vocals-first", action="store_true",
                        help="add_opt2=1: separate full vocals first, then split lead/back")
    parser.add_argument("--format", type=int, default=2,
                        help="output_format: 0 mp3 320, 1 wav 16, 2 flac 16 (default), 3 m4a, 4 wav 32, 5 flac 24")
    parser.add_argument("--interval", type=int, default=10, help="seconds between status polls")
    args = parser.parse_args()

    if shutil.which("ffmpeg") is None:
        sys.exit("ffmpeg not found on PATH; it is needed to normalize the output.")
    args.out.mkdir(parents=True, exist_ok=True)
    job_file = args.out / "job.json"
    if job_file.exists():
        job = json.loads(job_file.read_text(encoding="utf-8"))
        print(f"resuming job {job['hash']}")
    else:
        if not args.audio.is_file():
            sys.exit(f"Audio file not found: {args.audio}")
        token = load_token()
        print(f"uploading {args.audio} ({args.audio.stat().st_size / 1e6:.1f} MB)", flush=True)
        job = create_job(args.audio, token, args.model, args.extract_vocals_first, args.format)
        job_file.write_text(json.dumps(job), encoding="utf-8")
        print(f"job {job['hash']}: {job.get('link')}")

    files = wait_for_files(job["hash"], args.interval)
    if args.all_stems:
        for entry in files:
            # "download" is a server-supplied name: keep only its last path component.
            save(entry["url"], args.out / Path(entry["download"]).name)
    karaoke = next((entry for entry in files if entry.get("type", "").lower() == KARAOKE_STEM), None)
    if karaoke is None:
        sys.exit(f"No '{KARAOKE_STEM}' stem in job {job['hash']}; got {[entry.get('type') for entry in files]}")
    save(karaoke["url"], args.audio.with_name(f"{args.audio.stem}_karaoke{Path(karaoke['download']).suffix}"))


if __name__ == "__main__":
    main()
