---
status: todo
tags: [unsmell]
---
# Parse chapter filenames into a Chapter type

## Goal
Chapter filenames are parsed by regex group position (`m.group(2)`, `int(m.group(1))` in `Cli._download_to` and in the tests); `CHAPTER_PAT.match` is repeated three times; the loop in `_download_to` selects chapter files and writes tags in one body.

Done when the smell is gone and `uv run pyright`, `ruff check` and `pytest` pass.
## Next
A `Chapter` NamedTuple `(number, title)` with `Chapter.parse(name) -> Chapter | None` using named groups; `existing_audio` and `_download_to` use it, and the tag loop runs over the parsed chapters.

Found by the unsmell run 20261107-210704 (findings b1-15; details in `.git/unsmell/20261107-210704/`).
