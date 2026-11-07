---
status: todo
tags: [unsmell]
---
# Move Cli._download_to off Cli

## Goal
`Cli._download_to` never uses `self`. It is a free function about a download directory sitting on the CLI class.

Done when the smell is gone and `uv run pyright`, `ruff check` and `pytest` pass.
## Next
Make it a module-level function next to `download_audio` (or fold it into a small type for the cache directory, if T3tz1s's destination helper lands first).

Found by the unsmell run 20261107-221013 (findings b1-8; details in `.git/unsmell/20261107-221013/`).
