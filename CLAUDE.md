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
uv run karaoke audio/song.opus --video --codec ffv1 --fps 60   # lossless 10-bit FFV1 at 60 fps
uv run karaoke audio/song.opus --sync --video     # time the lyrics by hand in the browser first
uv add <package>                                  # add a dependency (updates pyproject.toml and uv.lock)
```

The CLI is deliberately minimal: one positional input, `--video [horizontal|vertical|both]`, `--sync`, and for the video `--codec avc|ffv1` (default `avc`) and `--fps` (default 24), the last two added at the user's request. Because `--video` takes an optional value, the song must come before it. Model, formats, loudness targets, colours and the settings of each encoder are module constants, not flags. The user's way to customise lyrics and timing is the `.lrc`/`.txt` file next to the song, not options.

The API token is read from `MVSEP_API_TOKEN`, falling back to a `.env` file in the current working directory, so run from the repo root or export the variable. `ffmpeg` and `ffprobe` must be on PATH.

## Architecture

`cli.py` orchestrates; the other modules in `src/mvsep_karaoke/` each own one concern:

- `mvsep.py`: API client. `run_job(audio, kind, options)` creates or resumes a job and returns its data.
- `media.py`: ffprobe (`probe`) and the loudness normalization (`normalize`).
- `lyrics.py`: LRC parsing and writing, LRCLIB lookup, and `align`, which turns lyric lines into word timings.
- `transcribe.py`: faster-whisper on the isolated lead vocals, returning timed words.
- `video.py`: builds an ASS subtitle script with karaoke tags and renders it with ffmpeg.
- `sync.py` and `sync_page/`: the hand-timing editor, a one-page app served on localhost.

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

`--sync` runs before the rest of the pipeline: `sync.edit` serves the files in `sync_page/` on a random localhost port, behind a random token in the path so other sites open in the browser cannot post to it, and blocks until the page's Close button (or Ctrl+C). The song is transcoded to WAV for the page, which loads it as a blob so seeking works without HTTP range support.

The page is `index.html` with `style.css` and seven scripts: `state.js` (constants, state and small helpers), `timing.js` (taps, cuts, undo, and the spread of a line's words by length), `lyrics.js` (the Edit lyrics box), `column.js` (the lyric column), `timeline.js` (the canvas and its dragging), `controls.js` (steps, buttons, keys, save and close) and `load.js` (reads the lyrics and the song, then starts the page). It was one 1,440-line file until the user asked for it to be split. The scripts are plain scripts, not modules, so everything in them is global: one file calls another without imports, and a test can reach all of it. Keep the order they have in `index.html`, because `controls.js` hands functions from the earlier files to the buttons while it loads and `load.js` starts the page. The server only answers for the files that are in the folder, looked up by name.

All editing logic is in the page's scripts. The page has three steps (`step`): `line`, where a tap times a whole line and spreads its words by length with the same `SECONDS_PER_CHAR` estimate as `lyrics.place`; `word`, where a tap times one word and gives the rest of the line provisional times; and `review`, where taps are off and the column follows the song. The user asked for lines to stay the first step and words to come second. Every change pushes an undo snapshot. Save posts the rows to the server, which writes the `.lrc`. A row that is not fully timed is written without a timestamp: `parse_lrc` reads such a line back as untimed, the editor shows it as still to do, and the CLI leaves it out of the video.

Each line has a `byWord` flag: its word times were set by hand, as opposed to estimated from its length. The `.lrc` has no place for that flag, since a saved line always carries word tags, so `recognise` works it out again on load: word times that are an even spread by length (within the file's centisecond rounding) were estimated. The same test catches lines the automatic pipeline could not match to the vocals, because `lyrics.place` spreads those with the same weights. A line's `end` is only set when the user chose it (Enter, or a dragged edge); null means it follows from the length and the next line, and is worked out again whenever a neighbour moves. A line tap on a `byWord` line shifts its words instead of estimating them again.

The timeline is a canvas drawn by `sketch`: the waveform (average level per 5 ms, read straight from the WAV; peak level draws as a solid bar on loud masters) scrolls under a fixed playhead. `spans()` lists what it shows and lets you drag, whole lines in the lines step and words otherwise. Blocks that touch share one edge, the outer edges of an estimated line stretch the estimate, and any other edge sets `byWord`.

Space only taps while the song plays; while it is paused the key handler starts the song instead. `tap()` itself has no such check, so a test can set `audio.currentTime` and call it.

There is no browser test in the repo. The page was checked by driving it with Playwright against installed Chrome (`uv run --with playwright python script.py`, `chromium.launch(channel="chrome")`), with `sync.edit` running in a thread and `webbrowser.open` replaced to capture the address. Its functions and state (`tap()`, `cut()`, `lines`, `cursor`, `step`, `place(time)` for a time's x position in the timeline) are globals, so they can be driven and inspected with `page.evaluate`. A check worth repeating after changes: open a real `.lrc`, save without touching anything, and the file must come back identical. Setting `BROWSER` to something like `cmd /c echo %s` stops `--sync` from opening a real browser window when the CLI itself is run in a test.

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

The output is an MKV with two FLAC audio tracks, both 48 kHz 16-bit: the karaoke track (stream-copied, default) and the original song (re-encoded, not normalized). The video is 24 fps unless `--fps` says otherwise, in one of the encoders in `CODECS`: `avc` is H.264 (`libx264 -preset slow -crf 20`, yuv420p), `ffv1` is lossless FFV1 level 3 in yuv444p10le, with every frame a keyframe and slice CRCs. The picture is made in the encoder's pixel format from the final upscale on, and the backdrop is floating point from its blur up to that point. The encoder settings, container and two-track layout were specified by the user, with FFV1 in yuv420p10le at first. The user found that FFV1 output still looked lossy, and two things in the picture caused it. 4:2:0 halves the colour resolution, and libass blends the lyrics into those half-size planes, which left dark fringes around the gold text: on a test frame the blue channel was off by 32 levels on average along text edges. The other cause was the 8-bit backdrop, described below.

Both codecs are BT.709, limited range, and tagged as such, at the user's request. Before that the conversion used ffmpeg's default BT.601 with no tags, and players that assume BT.709 for HD showed the gold lyrics as G=206 instead of 215. Three parts are needed, and each one was found missing in testing:
- `format=...:color_spaces=bt709:color_ranges=tv` makes the upscale convert with BT.709.
- `setparams` sets the primaries and transfer. The encoder takes its tags from the frames, and `-color_primaries`/`-color_trc` on the command line came out as `unknown`.
- `YCbCr Matrix: TV.709` in the ASS header. Without it the `ass` filter converts the lyric colours with BT.601, as VSFilter does, even on BT.709 frames. The backdrop was then right and the gold was off.

Checked by decoding a frame through its tags: gold is (254, 214, 1) and the backdrop matches the BT.601 render decoded as BT.601. The CRF was 26 at first; the user found that too lossy and asked for a lower one without naming a value, and 20 was picked without comparing the pictures. The frame rate was 60 until the user asked for 24 as the default. FFV1 is big: a 12 s test came to 82 MB (about 7 MB/s, so 1.2 to 1.5 GB for a three-minute song) against 3 MB for H.264. Before the backdrop was made in floating point, the same test was 118 MB and 6 MB: noise is expensive to keep.

The karaoke track is opened twice by ffmpeg: once for the bass pulse in the filter graph and once for the stream copy. With one input feeding both, the copy waits for video packets while x264 holds back its lookahead frames, the shared demuxer stalls the filter, and ffmpeg fails with `Cannot allocate memory` after "1000 buffers queued". At 60 fps the lookahead covered little enough of the song to get by; at 24 fps it failed every time. That diagnosis predates the `showwaves` bug below, which ends the same way, and was not checked again after the bug was found.

`showwaves` (ffmpeg 9.0.2) ends its stream with a timestamp hours after its last frame: 2 s of audio gave an end at 4026 s. Everything downstream inherits that end, and the `fps` filter pads the gap with copies of the last frame, about 96,000 of them for those 2 s. With FFV1 the render then failed at the end with `Cannot allocate memory`, after writing the whole video; with H.264 it succeeded but took twice as long. A `trim` after `fps` stops it. A `trim` straight after `showwaves` does not, because the end timestamp passes through it.

`video.subtitles` builds everything drawn over the backdrop as an ASS script. The lyrics are a column that scrolls up one row each time a new row starts; the row being sung sits in the centre and fills gold with `\kf` karaoke tags, and other rows fade, shrink and blur with distance (`look`). ASS allows one `\move` per event, so each row is emitted as one event per position it passes through, with `\t` animating the look during the scroll step. `turns[m]` is the moment row `m` reaches the centre. After a pause longer than `GAP` the next row scrolls in early and gets count-in dots (`count_in`). A title card shows during an intro of more than two seconds, and again in the middle of the frame when the song plays on for more than `GAP` after the last word; in that case the column fades out first instead of staying to the end.

The animations only change things that leave the text layout alone, because nothing here can measure text: a word's position is known to libass only. So a word is never scaled or moved on its own. The glow under the word being sung is a second copy of the centre row on a lower layer, with the same motion tags, a transparent fill and a blurred gold outline whose alpha is animated per word (`glow_text`); the two copies lay out identically, so they stay in register. Tags written after a `\t` override what it animates, which is how the glow reuses the row's tags and replaces only its colours. The count-in dots, the progress line and the drifting specks are `\p1` drawings, whose positions are chosen here. Layers from the back: 0 specks and progress line, 1 glow, 2 text.

`video.backdrop` is the ffmpeg filter graph under the script. `artwork` takes the embedded cover art (`media.cover`), or for a song without one makes a four-colour gradient from a hash of the title, limited to cyan through pink because gold lyrics disappear on yellow and orange. It also returns the gain that holds the backdrop to `PEAK`/`MEAN` brightness. Two copies of that picture rotate in opposite directions, are averaged and blurred, at 1/`SHRINK` of the frame and `BACKDROP_FPS`, then scaled up. The bass pulse has no analysis code: the karaoke track is low-passed, `showwaves` draws it as bars from the centre line, and scaling that drawing to one pixel gives its average, which is the level; `lagfun` gives it a slow decay and `blend=multiply` applies it.

From the blur on, the backdrop is `gbrpf32le`. It used to be 8-bit, and its gain, the saturation from `hue` (which also turned it into 8-bit YUV) and the dither of `vignette` all made rounding noise at 1/`SHRINK` size. Scaled up 6 times, that noise became 6-pixel blotches that moved with the backdrop and looked like compression. Measured as what a 25 px blur removes, it dropped from about 0.7 to 0.15 levels. Neither `hue` nor `vignette` works in float, so `saturate` writes a `colorchannelmixer` matrix (BT.601 luma, as `hue` uses) and the vignette is two still pictures made once by `geq`. The second picture is there to keep the old look: `vignette` on limited-range YUV also darkened the corners by up to 16/219 of full scale. The gain goes into the vignette picture because `colorchannelmixer` only takes values from -2 to 2. On a real cover the new backdrop is within about one level of the old one.

Things tried and dropped: spectrum bars from `showcqt` (one flat block of bass or a few stray spikes, depending on the weighting), `blend` with an expression (slow, and it wraps past 255 into green patches), and `vignette` at full size (12 s per 20 s of video; at the small size it is free).

The moving backdrop is what costs render time, not the filters: x264 at `-preset slow` has to work on every frame where it used to skip a still background. The 183 s test song went from 56 s to about 150 s per video, and from 45 MB to 63 MB. Those numbers are at CRF 26; at CRF 20 the same video is 123 MB, and its render took 230 s with other work running on the machine, so that time is an upper bound. `BACKDROP_FPS = 15` roughly halved the encode time of the backdrop alone in a 20 s test; how the pulse looks in 67 ms steps was not checked. It is 30 now, and the backdrop runs at the lower of that and the video's frame rate, so at 24 fps it is 24.

`LAYOUTS` holds the two shapes (horizontal 1920x1080, vertical 1080x1920). `max_chars` is how long a row may be before `split_line` breaks it, and has to be lowered if `font_size` is raised.

## Files

Songs and everything generated from them live in `audio/`, which is gitignored along with `*.flac`, `.env`, and `*.mvsep-job.json`.
