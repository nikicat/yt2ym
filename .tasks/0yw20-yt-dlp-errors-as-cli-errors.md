---
status: todo
tags: [unsmell]
---
# Make both yt-dlp calls fail the same way

## Goal
`probe_url` lets yt-dlp's `DownloadError` and pydantic's `ValidationError` escape as tracebacks, while `download_audio` raises a `click.ClickException`. The two yt-dlp entry points should report failures the same way.

Done when the smell is gone and `uv run pyright`, `ruff check` and `pytest` pass.
## Next
Catch `yt_dlp.utils.DownloadError` (add it to `typings/yt_dlp`) and `ValidationError` in `probe_url` and raise `click.ClickException` with the URL, like `download_audio` does.

Found by the unsmell run 20261107-221013 (findings b1-4; details in `.git/unsmell/20261107-221013/`).
