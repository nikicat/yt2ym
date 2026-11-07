---
status: todo
tags: [unsmell]
---
# Replace YandexMusic._client/_uid co-dependent optionals

## Goal
`YandexMusic._client` and `_uid` are both `None` or both set, so `uid()` needs `assert self._uid is not None`. Since the `_Api` refactor, `_api()` is `uid()`'s only caller, so `uid()` is now just an assert bridge over the two optionals (re-check finding r1-3).

Done when the smell is gone and `uv run pyright`, `ruff check` and `pytest` pass.
## Next
One cached optional record `(client, uid)` set once by `_ensure_client`, which returns it; `uid()` (or `_api()` directly) reads it without the assert.

Found by the unsmell run 20261107-210704 (findings b1-13, r1-3; details in `.git/unsmell/20261107-210704/`).
