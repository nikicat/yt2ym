---
status: todo
tags: [unsmell]
---
# Remove the Cli.login pass-through

## Goal
`Cli.login`'s whole body is `await YandexMusic.login()`, and it is that method's only caller.

Done when the smell is gone and `uv run pyright`, `ruff check` and `pytest` pass.
## Next
The click `login` command calls `YandexMusic.login()` directly; `Cli.login` goes.

Found by the unsmell run 20261107-210704 (findings b1-21; details in `.git/unsmell/20261107-210704/`).
