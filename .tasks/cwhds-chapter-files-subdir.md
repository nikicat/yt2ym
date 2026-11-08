---
status: todo
tags: [unsmell, bug]
---
# Keep chapter files apart from the whole-video file

## Goal
`Chapter.parse` tells a chapter file from the whole-video file only by the "NN - title" shape of its name. A video whose title starts like that ("1989 - Live") is therefore taken for a chapter. `download_to` rewrites its title and track tags, and in split mode `existing_audio` returns it with the real chapters, so it is uploaded as an extra track.

Done when a whole-video file can never be parsed as a chapter, and `uv run pyright`, `ruff check` and `pytest` pass.
## Decisions
- Fix: yt-dlp writes chapter files into a `chapters/` subdirectory of the cache dir, which `Chapter` owns next to `OUTTMPL`.
## Ruled out
- A stricter name pattern (exactly two digits): it narrows the bug, but a video titled "01 - Intro" still matches.
## Next
An existing cache keeps its chapters at the top level. The new code would read them as singles, so in split mode it would skip the download and upload every file. Either move top-level chapter files into `chapters/` when the subdirectory is missing, or version the cache directory so old caches are downloaded again.

Found by the unsmell run 20261108-021258 (finding b1-1; details in `.git/unsmell/20261108-021258/`).
