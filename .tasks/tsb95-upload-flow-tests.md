---
status: todo
tags: [tests]
---
# Offline tests for the upload flow

## Goal
The upload flow has no tests, so a refactor can break it unnoticed. It is only checked by hand against Yandex Music. The tests should cover:

- `Cli._upload_files` skips files whose title the playlist already holds;
- `Cli._upload_files` with `replace=True` empties the playlist and uploads every file;
- `YandexMusic.upload_many` retries a failed upload, and gives up after `attempts`;
- `upload_many` uploads in reverse order, so the first file ends up at the top;
- the repeated tracks that a retry adds are removed, and only for the titles just uploaded;
- `resolve_playlist` errors on an unknown title, while `resolve_or_create_playlist` creates a private playlist.

Done when these run without network in `pytest`, and `uv run pyright`, `ruff check` and `pytest` pass.
## Next
Fake the boundary, not the logic. For `Cli`, a fake `YandexMusic` that keeps a playlist as a list. For `upload_many`, a fake `_Api` whose `upload_one` fails a given number of times; patch `asyncio.sleep` so the retries don't wait.
