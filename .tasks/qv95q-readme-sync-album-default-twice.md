---
status: todo
tags: [unsmell]
---
# State the sync-album playlist default once in the README

## Goal
The README says twice that `sync-album`'s playlist defaults to the video title and is created when missing: in the subcommand table and in the paragraph under it.

Done when the smell is gone and `uv run pyright`, `ruff check` and `pytest` pass.
## Next
Keep it in one place (the table row), and let the paragraph cover only what applies to every command (`--playlist` takes a kind or a name).

Found by the unsmell run 20261107-221013 (findings b2-3; details in `.git/unsmell/20261107-221013/`).
