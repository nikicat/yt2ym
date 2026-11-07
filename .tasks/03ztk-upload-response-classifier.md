---
status: todo
tags: [unsmell]
---
# Name the storage upload response check

## Goal
`_Api.upload_one` classifies the storage response inline (status in 200/201, body containing OK or CREATED), while its sibling rule for API responses is the named `_ok_text`.

Done when the smell is gone and `uv run pyright`, `ruff check` and `pytest` pass.
## Next
A named `_ok_upload(r)` next to `_ok_text` that owns the status set and the body test; `upload_one` calls it.

Found by the unsmell run 20261107-210704 (findings b1-14; details in `.git/unsmell/20261107-210704/`).
