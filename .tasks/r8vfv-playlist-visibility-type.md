---
status: todo
tags: [unsmell]
---
# Replace the public bool with a Visibility type

## Goal
`"public" if public else "private"` is written in both `YandexMusic.create_playlist` and `Cli.create_playlist`; the `public` bool only picks that string.

Done when the smell is gone and `uv run pyright`, `ruff check` and `pytest` pass.
## Next
A `Visibility = Literal["public", "private"]` (or enum) taken by both, built once from the `--public` flag.

Found by the unsmell run 20261107-210704 (findings b1-9; details in `.git/unsmell/20261107-210704/`).
