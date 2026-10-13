# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-script project: `make_karaoke.py` uploads an audio file to the [MVSEP API](https://mvsep.com/en/full_api), runs the MVSep Karaoke separation (`sep_type=49`), and downloads the resulting stems. There is no build step, linter, or test suite.

## Commands

The script declares its dependencies inline (PEP 723), so `uv` resolves them on each run. No virtualenv or `pyproject.toml` is involved.

```
uv run make_karaoke.py                      # og.flac -> og_karaoke.flac, model 6, FLAC 16-bit
uv run make_karaoke.py song.flac --out out  # another input, job state in out/
uv run make_karaoke.py --all-stems          # also save all four stems to --out
uv run make_karaoke.py --model 8            # premium accounts only
uv run make_karaoke.py --help
```

The API token is read from the `MVSEP_API_TOKEN` environment variable, falling back to a `.env` file next to the script (see `.env.example`). The token is only needed to create a job; polling and downloading work without it.

## Job flow and resume behaviour

`main()` runs create -> poll -> download. Right after a job is created, the create response (`hash`, `link`) is written to `<out>/job.json`. On any later run, an existing `job.json` short-circuits the upload: the script polls that hash and downloads its files again. This is deliberate, so an interrupted run never re-uploads or spends credits twice.

Consequence: to separate a different song or use different options, delete `<out>/job.json` or pass a different `--out`. Otherwise the arguments are silently ignored in favour of the saved job.

## MVSEP API facts not obvious from the docs

- `sep_type` is the `render_id` field in `GET https://mvsep.com/api/app/algorithms`, not `id` (MVSep Karaoke has `id` 48 and `render_id` 49). That endpoint is the source of truth for the `add_opt1`/`add_opt2` values of every algorithm and needs no token.
- For `sep_type=49`, `add_opt1` is the model (0-8) and `add_opt2=1` means "extract vocals first".
- Model 8 (BS Roformer 124 bands) is rejected on non-premium accounts with HTTP 400 and `{'success': False, 'errors': ['This option is available only for premium users.']}`. Model 6 with FLAC 16-bit output works on a free account. A rejected create makes no job.
- Create-time failures arrive in a top-level `errors` list, not in `data.message` as the docs describe.
- A finished job's `data.files` is a list of objects with `url` (download link) and `download` (suggested filename). This shape is not in the docs; it comes from the [official examples](https://github.com/ZFTurbo/MVSep-API-Examples).
- Stems come back at 44.1 kHz even when the input is 48 kHz, which is why `normalize()` resamples to 48 kHz.
- Non-premium accounts get one concurrent job and wait in a queue; the poll loop prints the queue position.
- The OpenAPI spec is at `https://mvsep.com/misc/mvsep-openapi-spec.json`, with paths prefixed by `/api`.

## Post-processing

Every downloaded file goes through `normalize()`, which needs `ffmpeg` on PATH and replaces the file in place with a 48 kHz 16-bit FLAC at -14 LUFS integrated, -1.0 dBTP ceiling (`TARGET_LUFS`, `TARGET_TRUE_PEAK`).

It is a two-pass `loudnorm`: pass one measures, pass two feeds the `measured_*` values back with `linear=true`. The target `LRA` is raised to at least the measured LRA, because loudnorm silently falls back to dynamic mode (which reshapes dynamics) when the target LRA is below the source's. It also falls back when the required gain would push the true peak over the ceiling, so quiet, peaky stems can still end up in dynamic mode. The mode used is printed per file.

With `--all-stems`, each stem is normalized on its own, so the stems no longer sum back to the original mix.

## Output

The deliverable is one file, `<input stem>_karaoke.flac`, written next to the input: the mix with only the lead vocals removed. It is the stem whose `type` is `Back instrum` in `data.files`, selected by that field (`KARAOKE_STEM`), not by filename.

With `--all-stems`, every stem is also saved to `--out` as `<input>_roformer_karaoke_mt_<model>_<stem>.flac`: `back-instrum` (same audio as the karaoke file), `instrum-only` (no vocals at all), `vocals-lead`, and `vocals-back`.

`karaoke/` and `.env` are gitignored.
