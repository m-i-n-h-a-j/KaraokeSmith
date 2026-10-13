"""Make a karaoke track, and optionally a karaoke lyrics video, from a song with the MVSEP API.

Writes <name>_karaoke.flac next to the input: the mix with only the lead vocals removed
(back vocals and instruments stay), loudness-normalized with ffmpeg to -10 LUFS / -1.0 dBTP
and encoded as 48 kHz 16-bit FLAC.

With --video it also writes <name>_karaoke.mkv, scrolling lyrics highlighted word by word
over a plain colour with two FLAC audio tracks (karaoke and original), and <name>.lrc, the
lyrics and timings it used. "--video vertical" writes <name>_karaoke_vertical.mkv instead,
"--video both" writes both. Put the song before --video on the command line.

Lyrics come from <name>.lrc (timed) or <name>.txt (text only) next to the song if you put
one there, otherwise from LRCLIB, looked up by the song's tags. --sync opens an editor in
the browser to type in lyrics and tap their timing along with the song; it saves <name>.lrc.
Run again after changing the .lrc and only the video is rendered again.

The API token is read from MVSEP_API_TOKEN, or from a .env file in the current directory.
"""
import argparse
import shutil
import sys
import tempfile
from pathlib import Path

from . import lyrics, media, mvsep, sync, video
from .transcribe import transcribe

# sep_type 49 is MVSep Karaoke; add_opt1 6 is BS Roformer by MVSep Team, the best model open
# to non-premium accounts; output_format 2 is FLAC 16-bit.
KARAOKE_JOB = {"sep_type": 49, "add_opt1": 6, "add_opt2": 0, "output_format": 2}
KARAOKE_STEM = "back instrum"  # MVSEP file "type" for everything except the lead vocals
VOCALS_STEM = "vocals lead"


def stem_url(files: list[dict], kind: str) -> str:
    for entry in files:
        if entry.get("type", "").lower() == kind:
            return entry["url"]
    sys.exit(f"No '{kind}' stem in the MVSEP job; got {[entry.get('type') for entry in files]}")


def separate(song: Path, karaoke: Path, vocals: Path | None) -> None:
    """Run the MVSEP karaoke job; save the karaoke track if it is missing and the lead vocals if wanted."""
    files = mvsep.run_job(song, "karaoke", KARAOKE_JOB)["files"]
    if not karaoke.exists():
        # Finished under another name first, so an interrupted run cannot leave a broken track behind.
        download = karaoke.with_name(f"{karaoke.stem}.download.flac")
        mvsep.save(stem_url(files, KARAOKE_STEM), download)
        media.normalize(download)
        download.replace(karaoke)
    if vocals is not None:
        mvsep.save(stem_url(files, VOCALS_STEM), vocals)
    mvsep.job_file(song, "karaoke").unlink()


def find_lyrics(song: Path, lrc: Path, duration: float, tags: dict) -> list[lyrics.Draft]:
    """Lyrics for the song from the most trusted source there is, or nothing."""
    if lrc.exists():
        drafts = lyrics.parse_lrc(lrc.read_text(encoding="utf-8-sig"))
        if drafts:
            print(f"lyrics: {lrc}")
            return drafts
    text = song.with_suffix(".txt")
    if text.exists():
        print(f"lyrics: {text} (text only)")
        return lyrics.parse_plain(text.read_text(encoding="utf-8-sig"))
    if tags.get("title"):
        return lyrics.fetch_lrclib(tags["title"], tags.get("artist", ""), duration)
    # No title tag: the file name is the only clue, so only an entry named exactly that is trusted.
    return lyrics.fetch_lrclib(song.stem, "", duration, exact_title=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="karaoke", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("input", type=Path, help="song to turn into a karaoke track")
    parser.add_argument("--video", nargs="?", const="horizontal", choices=["horizontal", "vertical", "both"],
                        help="also make a karaoke lyrics video: horizontal 1920x1080 (the default), "
                             "vertical 1080x1920, or both")
    parser.add_argument("--sync", action="store_true",
                        help="first open the lyrics editor in the browser, to type in lyrics and tap their timing by hand")
    args = parser.parse_args()
    song = args.input

    if not song.is_file():
        sys.exit(f"Audio file not found: {song}")
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            sys.exit(f"{tool} not found on PATH; it is needed to process the audio.")

    karaoke = song.with_name(f"{song.stem}_karaoke.flac")
    lrc = song.with_suffix(".lrc")
    duration, tags = media.probe(song)
    title = tags.get("title") or song.stem
    drafts = find_lyrics(song, lrc, duration, tags) if args.video or args.sync else []
    if args.sync:
        sync.edit(song, lrc, drafts, duration, title)
        drafts = find_lyrics(song, lrc, duration, tags) if args.video else []
    timed = [draft for draft in drafts if draft.start is not None]
    if timed and len(timed) < len(drafts):
        print(f"lyrics: {len(drafts) - len(timed)} lines have no timing yet and are left out; finish them with --sync")
        drafts = timed
    # Word timings come from transcribing the lead vocals, unless the .lrc already has them all.
    untimed = args.video and (not drafts or any(draft.words is None for draft in drafts))

    with tempfile.TemporaryDirectory() as folder:
        vocals = Path(folder) / "vocals.flac" if untimed else None
        if vocals is not None or not karaoke.exists():
            separate(song, karaoke, vocals)
        else:
            print(f"using existing {karaoke}")
        if not args.video:
            return
        if vocals is not None:
            phrases = transcribe(vocals)
            if drafts:
                try:
                    lines = lyrics.align(drafts, [word for phrase in phrases for word in phrase], duration)
                except lyrics.NoTiming as problem:
                    if not lrc.exists():  # keep the lyrics text where the editor will find it
                        lrc.write_text("\n".join(draft.text for draft in drafts) + "\n", encoding="utf-8")
                    sys.exit(f"The lyrics could not be timed automatically: {problem}.\n"
                             f"Run again with --sync to time them by hand.")
            else:
                print("lyrics: none found, using the transcription")
                lines = phrases
            lrc.write_text(lyrics.format_lrc(lines), encoding="utf-8")
            print(f"saved {lrc}; edit it and run again to fix lyrics or timing")
        else:
            lines = lyrics.align(drafts, [], duration)
    for shape in ("horizontal", "vertical") if args.video == "both" else (args.video,):
        suffix = "" if shape == "horizontal" else f"_{shape}"
        video.render(lines, karaoke, song, duration, song.with_name(f"{song.stem}_karaoke{suffix}.mkv"),
                     video.LAYOUTS[shape], title, tags.get("artist", ""))


if __name__ == "__main__":
    main()
