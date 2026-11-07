---
status: todo
tags: [unsmell]
---
# Rewrite YandexMusic/Cli class docs

## Goal
The `YandexMusic` doc lists its members ("OAuth token + lazy client + upload operations") and the `Cli` doc its methods and field.

Done when the smell is gone and `uv run pyright`, `ruff check` and `pytest` pass.
## Next
Each states its place: `YandexMusic` is the only way the tool talks to Yandex Music for one account; `Cli` maps subcommands onto it and the downloader.

Found by the unsmell run 20261107-210704 (findings b1-19; details in `.git/unsmell/20261107-210704/`).
