"""Word timings heard in the isolated vocals (or with --skip-separation, the whole song), via faster-whisper."""
import subprocess
import sys
from pathlib import Path

from .lyrics import Line, Word
from .progress import Progress

# "small" skipped the first half-minute of a rap verse that "turbo" caught; turbo takes about
# 0.4x real time on a laptop CPU and downloads about 1.6 GB on first use.
MODEL = "turbo"


def transcribe(vocals: Path) -> list[Line]:
    """Transcribe vocals into phrases of timed words."""
    try:
        import numpy
        from faster_whisper import WhisperModel  # slow to import, so only when a transcription is needed
    except ImportError as e:
        sys.exit(
            f"Speech transcription (faster-whisper) is not available on this platform: {e}\n"
            f"Please time the lyrics manually with '--sync' (opens the in-browser editor)\n"
            f"or provide a timed .lrc file next to the song."
        )

    # Decoded here rather than by faster-whisper, whose own decoder breaks with current PyAV.
    decoded = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(vocals), "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
        capture_output=True,
    )
    if decoded.returncode != 0:
        sys.exit(f"ffmpeg failed on {vocals}:\n{decoded.stderr.decode(errors='replace')[-800:]}")
    print(f"transcribing {vocals.name} with faster-whisper {MODEL} (CPU)", flush=True)
    model = WhisperModel(MODEL, device="cpu", compute_type="int8")
    samples = numpy.frombuffer(decoded.stdout, dtype=numpy.float32)
    segments, _ = model.transcribe(samples, word_timestamps=True, vad_filter=True, condition_on_previous_text=False)
    meter, phrases = Progress("transcribing", len(samples) / 16000), []
    for segment in segments:  # worked out one by one as the loop asks for them
        phrases.append([Word(word.word.strip(), word.start, word.end) for word in segment.words])
        meter.update(segment.end)
    phrases = [phrase for phrase in phrases if phrase]
    meter.close(f"transcribing: heard {sum(map(len, phrases))} words in {meter.elapsed()}")
    return phrases
