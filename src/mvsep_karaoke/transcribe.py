"""Word timings heard in isolated vocals, via faster-whisper."""
import subprocess
import sys
from pathlib import Path

from .lyrics import Line, Word

# "small" skipped the first half-minute of a rap verse that "turbo" caught; turbo takes about
# 0.4x real time on a laptop CPU and downloads about 1.6 GB on first use.
MODEL = "turbo"


def transcribe(vocals: Path) -> list[Line]:
    """Transcribe vocals into phrases of timed words."""
    import numpy
    from faster_whisper import WhisperModel  # slow to import, so only when a transcription is needed

    # Decoded here rather than by faster-whisper, whose own decoder breaks with current PyAV.
    decoded = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(vocals), "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
        capture_output=True,
    )
    if decoded.returncode != 0:
        sys.exit(f"ffmpeg failed on {vocals}:\n{decoded.stderr.decode(errors='replace')[-800:]}")
    print(f"transcribing vocals with faster-whisper {MODEL} (CPU)", flush=True)
    model = WhisperModel(MODEL, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(
        numpy.frombuffer(decoded.stdout, dtype=numpy.float32),
        word_timestamps=True, vad_filter=True, condition_on_previous_text=False,
    )
    phrases = [[Word(word.word.strip(), word.start, word.end) for word in segment.words] for segment in segments]
    return [phrase for phrase in phrases if phrase]
