# mvsep-karaoke

Turn a song into a karaoke track, and optionally a karaoke lyrics video, with the [MVSEP API](https://mvsep.com/en/full_api). Only the lead vocals are removed; back vocals and instruments stay.

## Requirements

- [uv](https://docs.astral.sh/uv/)
- `ffmpeg` and `ffprobe` on PATH
- An MVSEP API token

## Setup

Copy `.env.example` to `.env` and put your token in it, or set the `MVSEP_API_TOKEN` environment variable.

## Karaoke track

```
uv run karaoke path/to/song.flac
```

This writes `song_karaoke.flac` next to the input: a 48 kHz 16-bit FLAC, normalized to -14 LUFS with a -1.0 dBTP ceiling. If that file already exists it is reused; delete it to make a new one.

If a run is interrupted, run the same command again: it picks the unfinished job back up instead of uploading the song a second time.

## Lyrics video

```
uv run karaoke path/to/song.flac --video             # horizontal, 1920x1080
uv run karaoke path/to/song.flac --video vertical    # vertical, 1080x1920
uv run karaoke path/to/song.flac --video both
```

Put the song before `--video`. This also writes:

- `song_karaoke.mkv` (and/or `song_karaoke_vertical.mkv`): the lyrics as a scrolling column over a plain background, the row being sung held in the centre and filled word by word. H.264 video with two FLAC audio tracks: the karaoke track (default) and the original song, so a player can switch between them.
- `song.lrc`: the lyrics and timings that were used.

The first run downloads a speech recognition model (about 1.6 GB) that is used to time the words.

### Where the lyrics come from

The first of these that exists is used:

1. `song.lrc` next to the song: your own lyrics with timings.
2. `song.txt` next to the song: your own lyrics, one line per row, no timings.
3. [LRCLIB](https://lrclib.net), looked up by the song's title and artist tags.
4. If nothing is found, whatever the speech recognition heard.

### Fixing lyrics or timing

Edit `song.lrc` and run the command again. Only the video is rendered again, which takes seconds.

Each line starts with its time, and each word is preceded by the time it starts. A time with nothing after it marks where the word before it ends:

```
[00:12.00] <00:12.00> first <00:12.40> second <00:12.90>
```

A line with only a line time and no word times is also accepted; its words are then timed automatically and the file is rewritten with word times:

```
[00:12.00] first second
```
