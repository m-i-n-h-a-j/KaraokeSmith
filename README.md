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

If lyrics without timings cannot be matched to the vocals (a language the speech recognition does not handle, or incomplete lyrics), no video is made and you are told to time them by hand.

### Timing lyrics by hand

```
uv run karaoke path/to/song.flac --sync --video
```

`--sync` opens an editor in your browser before anything else. It runs on your own machine; nothing is uploaded. When you save and close it, the command carries on and renders the video.

- **Edit lyrics**: paste or type the lyrics, one line per row.
- **Tap lines** (default): play the song and press `Space` at the start of each line. The words of the line are spread over it by their length.
- **Tap words**: press `Space` at the start of each word instead.
- `Enter` ends the last tapped line or word at that moment, for a pause before the next one.
- `Backspace` undoes the last tap and rewinds a little. `P` plays and pauses, the arrow keys jump 3 seconds, `1` `2` `3` set the speed to 0.5x, 0.75x and 1x.
- Click a line to rewind to it and tap again from there.
- **-50 ms / +50 ms** move every timing, for when your taps are consistently late or early.

Timed words turn gold as the song plays, the way they will in the video. **Save** writes `song.lrc`; lines you have not timed yet are kept in it without a time and left out of the video.

### Fixing lyrics or timing in the file

You can also edit `song.lrc` in a text editor and run the command again. Only the video is rendered again.

Each line starts with its time, and each word is preceded by the time it starts. A time with nothing after it marks where the word before it ends:

```
[00:12.00] <00:12.00> first <00:12.40> second <00:12.90>
```

A line with only a line time and no word times is also accepted; its words are then timed automatically and the file is rewritten with word times:

```
[00:12.00] first second
```
