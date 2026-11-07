---
status: todo
tags: [unsmell]
---
# Extract the upload retry loop

## Goal
`YandexMusic.upload_many` hand-builds retry with exponential backoff (attempt counter, growing sleep, give-up) around the domain loop.

Done when the smell is gone and `uv run pyright`, `ruff check` and `pytest` pass.
## Next
Options: adopt `tenacity` or `stamina` (new dependency), or a small generic `retry_async(fn, attempts, on=RuntimeError)` with its own test. `upload_many` keeps only the reverse-order loop.

Found by the unsmell run 20261107-210704 (findings b1-11; details in `.git/unsmell/20261107-210704/`).
