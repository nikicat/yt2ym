---
status: todo
tags: [unsmell]
---
# Rewrite narrating docstrings as contracts

## Goal
`YandexMusic.login`, `download_to`, `Cli._upload_files`, `Cli.sync` docs retell their steps (arrows, "first wipes …, then uploads"); `login` ("Smart TV client"), `clear_playlist` ("via the /change endpoint") and `_download_to` ("native m4a/aac") name the mechanism; `file_title` opens with its caller's reason.

Done when the smell is gone and `uv run pyright`, `ruff check` and `pytest` pass.
## Next
Each doc states its contract (what comes out, what changes, "Errors when …"); mechanisms move to `#` comments at the code.

Found by the unsmell run 20261107-210704 (findings b1-18; details in `.git/unsmell/20261107-210704/`).
