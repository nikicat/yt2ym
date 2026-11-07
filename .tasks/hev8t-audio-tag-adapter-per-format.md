---
status: todo
tags: [unsmell]
---
# One tag adapter per audio format

## Goal
`extract_cover`, `file_title` and `write_title_track` each branch on `.mp3` / `.m4a` with format-specific mutagen code. The M4A keys (`"\xa9nam"`, `"covr"`, `"trkn"`) are spelled at the writer and the reader (now type-checked via Literal overloads in `typings/mutagen/mp4.pyi`, but still two sites), and `AUDIO_EXTS` / `CHAPTER_PAT` spell the suffixes again.

Done when the smell is gone and `uv run pyright`, `ruff check` and `pytest` pass.
## Next
`Mp3Tags` / `M4aTags` adapters with `title()`, `cover()` and `set_title_track()`, looked up once by suffix in a dict keyed like `AUDIO_EXTS`; each tag key is private to its adapter.

Found by the unsmell run 20261107-210704 (findings b1-7, x-3 (remaining part); details in `.git/unsmell/20261107-210704/`).
