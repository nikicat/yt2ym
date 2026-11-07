---
status: todo
tags: [unsmell]
---
# Give the token sources one owner

## Goal
`$YANDEX_MUSIC_TOKEN` is read by click (`envvar=`) and again by `os.getenv` in `YandexMusic.from_args`, whose env branch the CLI can never reach. The token sources are listed in the module doc, the missing-token error, `TOKEN_HELP` and README (First-time setup); `TOKEN_HELP` spells `~/.config/yt2ym/token` beside `TOKEN_FILE`.

Done when the smell is gone and `uv run pyright`, `ruff check` and `pytest` pass.
## Next
Drop the `os.getenv` branch (click owns the env var); build `TOKEN_HELP` from `TOKEN_FILE`; the module doc points to `--help` instead of listing the sources.

Found by the unsmell run 20261107-210704 (findings b1-10, x-2; details in `.git/unsmell/20261107-210704/`).
