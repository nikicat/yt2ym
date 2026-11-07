---
status: todo
tags: [unsmell]
---
# Deduplicate the audio-file listing

## Goal
`existing_audio` rebuilds `collect_audio`'s listing (`sorted(p for p in X.iterdir() if p.suffix.lower() in AUDIO_EXTS)`), and the is-audio test appears a third time in `Cli.set_cover`.

Done when the smell is gone and `uv run pyright`, `ruff check` and `pytest` pass.
## Next
An `is_audio(path)` predicate; `existing_audio` calls `collect_audio(dest)`.

Found by the unsmell run 20261107-210704 (findings b1-16; details in `.git/unsmell/20261107-210704/`).
