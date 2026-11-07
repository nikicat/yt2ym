---
status: todo
tags: [unsmell]
---
# Parse the playlist argument at the CLI edge; kind as int

## Goal
The `--playlist` / `PLAYLIST` argument (a numeric kind or a name) stays raw text through four `Cli` methods and is classified with `isdigit()` only inside `YandexMusic.resolve_or_create_playlist`. The kind then travels as `str` through about a dozen signatures (`_Api.playlist_url`, `_Api.upload_url`, `fetch_playlist`, `clear_playlist`, `upload_many`, …) although `yandex_music.Playlist.kind` is an int; `resolve_or_create_playlist` converts with `str(p.kind)` and its doc says "Returns the numeric kind as a string".

Done when the smell is gone and `uv run pyright`, `ruff check` and `pytest` pass.
## Next
A click `ParamType` (or callback) that parses the argument into `int | PlaylistName` at the edge; `resolve_or_create_playlist` takes the parsed value and returns `int`; every `kind` parameter becomes `int`, and the doc sentence about the string goes.

Found by the unsmell run 20261107-210704 (findings b1-3, b1-4; details in `.git/unsmell/20261107-210704/`).
