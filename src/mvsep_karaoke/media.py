"""ffmpeg/ffprobe helpers: probe a file and loudness-normalize the karaoke track."""
import json
import subprocess
import sys
from pathlib import Path

TARGET_LUFS = -10.0
TARGET_TRUE_PEAK = -1.0  # dBTP


def run(command: list[str], cwd: Path | None = None) -> str:
    """Run an ffmpeg/ffprobe command and return its stderr, exiting with its tail on failure."""
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        sys.exit(f"{command[0]} failed:\n{result.stderr[-800:]}")
    return result.stderr


def probe(path: Path) -> tuple[float, dict]:
    """Return (duration in seconds, lower-cased tags) of an audio file."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:format_tags:stream_tags", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        sys.exit(f"ffprobe failed on {path}:\n{result.stderr[-800:]}")
    info = json.loads(result.stdout)
    # Ogg/Opus keeps tags on the stream, most other containers on the format.
    tags = {**(info.get("streams") or [{}])[0].get("tags", {}), **info["format"].get("tags", {})}
    return float(info["format"]["duration"]), {key.lower(): value for key, value in tags.items()}


def loudnorm(source: Path, measured: str, output: list[str]) -> dict:
    """Run ffmpeg's loudnorm filter over source and return the stats it reports."""
    filters = f"loudnorm=I={TARGET_LUFS}:TP={TARGET_TRUE_PEAK}:{measured}print_format=json"
    stderr = run(["ffmpeg", "-hide_banner", "-nostats", "-y", "-i", str(source), "-af", filters, *output])
    return json.loads(stderr[stderr.rindex("{"):stderr.rindex("}") + 1])


def normalize(path: Path) -> None:
    """Rewrite the FLAC at path at TARGET_LUFS / TARGET_TRUE_PEAK, 48 kHz 16-bit (two-pass loudnorm)."""
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
    temporary.replace(path)
    print(f"normalized {path}: {stats['input_i']} -> {stats['output_i']} LUFS, "
          f"true peak {stats['output_tp']} dBTP, {stats['normalization_type']} mode")
