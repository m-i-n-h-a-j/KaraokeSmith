# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A uv project that exposes one console command, `karaoke`. It uploads a song to the [MVSEP API](https://mvsep.com/en/full_api), runs the MVSep Karaoke separation (`sep_type=49`), saves the stem with only the lead vocals removed, and normalizes it with ffmpeg. With `--video` it also renders a lyrics video with word-by-word highlighting. There is no linter or test suite.

## Commands

```
uv run karaoke audio/song.opus                    # writes audio/song_karaoke.flac
uv run karaoke audio/song.opus --video            # also audio/song.lrc and audio/song_karaoke.mkv
uv run karaoke audio/song.opus --video vertical   # audio/song_karaoke_vertical.mkv instead
uv run karaoke audio/song.opus --video both
uv run karaoke audio/song.opus --sync --video     # time the lyrics by hand in the browser first
uv add <package>                                  # add a dependency (updates pyproject.toml and uv.lock)
```

The CLI is deliberately minimal: one positional input, `--video [horizontal|vertical|both]` and `--sync`. Because `--video` takes an optional value, the song must come before it. Model, formats, loudness targets, colours and encoder settings are module constants, not flags. The user's way to customise lyrics and timing is the `.lrc`/`.txt` file next to the song, not options.

The API token is read from `MVSEP_API_TOKEN`, falling back to a `.env` file in the current working directory, so run from the repo root or export the variable. `ffmpeg` and `ffprobe` must be on PATH.

## Architecture

`cli.py` orchestrates; the other modules in `src/mvsep_karaoke/` each own one concern:

- `mvsep.py`: API client. `run_job(audio, kind, options)` creates or resumes a job and returns its data.
- `media.py`: ffprobe (`probe`) and the loudness normalization (`normalize`).
- `lyrics.py`: LRC parsing and writing, LRCLIB lookup, and `align`, which turns lyric lines into word timings.
- `transcribe.py`: faster-whisper on the isolated lead vocals, returning timed words.
- `video.py`: builds an ASS subtitle script with karaoke tags and renders it with ffmpeg.
- `sync.py` and `sync.html`: the hand-timing editor, a one-page app served on localhost.

### Outputs are the cache

Everything is written next to the input, and existing files short-circuit work: `<name>_karaoke.flac` is reused if present, and a `<name>.lrc` whose lines all carry word tags means no separation, no transcription, only a re-render. That is what makes "edit the .lrc, run again" take seconds. The MVSEP job is only run when the karaoke track is missing or the lead vocals are needed for timing; the vocals go to a temp folder and are never kept.

### Job resume

Right after a job is created, the create response is written as `<input name>.<kind>.mvsep-job.json`. If that file exists, the upload is skipped and the saved job is polled. It is deleted once the results are on disk, and also when MVSEP reports the job failed or lost, so the next run starts fresh. Ctrl+C leaves it in place on purpose.

### Lyrics pipeline

`find_lyrics` picks the first source that exists: `<name>.lrc`, `<name>.txt`, LRCLIB (by title and artist tags, entries within 2 s of the song's duration), else nothing. Lines become `Draft` objects that may have text only, text and a start time, or finished word timings.

Databases only have line timings, so word timings come from transcribing the lead vocals and matching the heard words to the lyric words (`align`). With line timestamps, each line is matched only against words heard inside its own time window, which stops a repeated chorus from latching onto the wrong repetition; without them the whole song is matched at once. Words that were not heard are spread between their matched neighbours in proportion to their length. If there are no lyrics at all, the transcription itself becomes the lyrics.

The result is written to `<name>.lrc` in enhanced LRC (`[mm:ss.xx] <mm:ss.xx> word ...`; an empty word tag marks where the previous word ends). A user-supplied line-level `.lrc` is rewritten in place with word tags added.

Matching text-only lyrics needs the recognizer to hear the same words. When fewer than 30% match and there are no line timestamps to fall back on, `align` raises `NoTiming` instead of smearing the lyrics over the song; the CLI then saves the text as an untimed `.lrc` and tells the user to run `--sync`. This happens with languages Whisper handles badly (the Malayalam/English track this project started with matched 0 of 77 words).

### Hand-timing editor

`--sync` runs before the rest of the pipeline: `sync.edit` serves `sync.html` on a random localhost port, behind a random token in the path so other sites open in the browser cannot post to it, and blocks until the page's Close button (or Ctrl+C). The song is transcoded to WAV for the page, which loads it as a blob so seeking works without HTTP range support.

All editing logic is in the page's script: taps read `audio.currentTime`, a line tap spreads the line's words by length with the same `SECONDS_PER_CHAR` estimate as `lyrics.place`, a word tap times one word and gives the rest of the line provisional times, and every change pushes an undo snapshot. Save posts the rows to the server, which writes the `.lrc`. A row that is not fully timed is written without a timestamp: `parse_lrc` reads such a line back as untimed, the editor shows it as still to do, and the CLI leaves it out of the video.

There is no browser test in the repo. The page was checked by driving it with Playwright against installed Chrome (`uv run --no-project --with playwright ...`, `chromium.launch(channel="chrome")`); its functions and state (`tap()`, `lines`, `cursor`) are globals, so they can be inspected with `page.evaluate`. Setting `BROWSER` to something like `cmd /c echo %s` stops `--sync` from opening a real browser window during such a test.

## MVSEP API facts not obvious from the docs

- `sep_type` is the `render_id` field in `GET https://mvsep.com/api/app/algorithms`, not `id` (MVSep Karaoke has `id` 48 and `render_id` 49). That endpoint is the source of truth for the `add_opt1`/`add_opt2` values of every algorithm and needs no token.
- For `sep_type=49`, `add_opt1` is the model (0-8) and `add_opt2=1` means "extract vocals first".
- Model 8 (BS Roformer 124 bands) scores higher but is rejected on non-premium accounts with HTTP 400 and `{'success': False, 'errors': ['This option is available only for premium users.']}`. That is why model 6 is used. A rejected create makes no job.
- Create-time failures arrive in a top-level `errors` list, not in `data.message` as the docs describe.
- A finished job's `data.files` is a list of objects with `type`, `url` and `download`. This shape is not in the docs. Stems are selected by `type`, not by filename.
- Model 6 returns four stems: `Vocals lead`, `Vocals back`, `Instrum only`, `Back instrum`.
- Stems come back at 44.1 kHz even when the input is 48 kHz, which is why `normalize()` resamples to 48 kHz.
- MVSEP's own transcription (Whisper `sep_type=39`, Parakeet `sep_type=64`) returns only `data.transcription.txt` and `.srt` with phrase-level cues, no word timings, and Whisper skipped 25 s of a rap verse in testing. That is why transcription is done locally.
- Non-premium accounts get one concurrent job and wait in a queue.
- The OpenAPI spec is at `https://mvsep.com/misc/mvsep-openapi-spec.json`, with paths prefixed by `/api`.

## Other things learned the hard way

- LRCLIB (`https://lrclib.net/api/search`, no key) often answers 503 or drops the connection; `search_lrclib` retries. Its entries have `syncedLyrics` (line-level LRC) and `plainLyrics`; `hasWordSync` was false for every entry checked.
- faster-whisper 1.2.1 cannot decode files with the PyAV it installs (`open() got an unexpected keyword argument 'metadata_errors'`), so `transcribe.py` decodes with ffmpeg and passes samples.
- faster-whisper `small` missed the first 28 s of vocals that `turbo` caught; `turbo` on CPU takes about 0.4x real time. GPU needs cuBLAS/cuDNN DLLs that are not installed (`Library cublas64_12.dll is not found`), so it runs on CPU.
- ffmpeg's `ass` filter needs heavy escaping for Windows paths, so `render` writes `lyrics.ass` to a temp folder and runs ffmpeg with that folder as cwd.

## Post-processing

`normalize()` replaces the downloaded karaoke track with a 48 kHz 16-bit FLAC at -10 LUFS integrated, -1.0 dBTP ceiling (`TARGET_LUFS`, `TARGET_TRUE_PEAK` in `media.py`; both values were chosen by the user). It is a two-pass `loudnorm`: pass one measures, pass two feeds the `measured_*` values back with `linear=true`. The target `LRA` is raised to at least the measured LRA, because loudnorm silently falls back to dynamic mode (which reshapes dynamics) when the target LRA is below the source's. It also falls back when the required gain would push the true peak over the ceiling. The mode used is printed.

At -10 LUFS that second fallback is the normal case: MVSEP's stems arrive around -11 to -13 LUFS with peaks near 0 dBFS, so they need gain and cannot stay under the ceiling linearly. On the test stem dynamic mode gave -10.2 LUFS (it undershoots), cut the loudness range from 9.6 to 5.1 LU, and measured -0.9 dBTP after the resample to 48 kHz. With the earlier -14 LUFS target every track was a plain gain cut in linear mode. If the squashed dynamics become a problem, the alternative is linear gain followed by a true-peak limiter instead of loudnorm.

## Video

The output is an MKV: H.264 (`libx264 -preset slow -crf 26`, 60 fps) with two FLAC audio tracks, both 48 kHz 16-bit: the karaoke track (stream-copied, default) and the original song (re-encoded, not normalized). The encoder settings, container and two-track layout were specified by the user.

`video.subtitles` builds the whole look as an ASS script. The lyrics are a column that scrolls up one row each time a new row starts; the row being sung sits in the centre and fills gold with `\kf` karaoke tags, and other rows fade, shrink and blur with distance (`look`). ASS allows one `\move` per event, so each row is emitted as one event per position it passes through, with `\t` animating the look during the scroll step. `turns[m]` is the moment row `m` reaches the centre. After a pause longer than `GAP` the next row scrolls in early and gets count-in dots. A title card shows during an intro of more than two seconds if the song has a title tag.

`LAYOUTS` holds the two shapes (horizontal 1920x1080, vertical 1080x1920). `max_chars` is how long a row may be before `split_line` breaks it, and has to be lowered if `font_size` is raised.

## Files

Songs and everything generated from them live in `audio/`, which is gitignored along with `*.flac`, `.env`, and `*.mvsep-job.json`.
