---
status: todo
tags: [unsmell]
---
# Stop clear-playlist and set-cover from creating playlists

## Goal
`Cli.clear_playlist` and `Cli.set_cover` resolve their playlist with `YandexMusic.resolve_or_create_playlist`. A mistyped name therefore creates a new empty private playlist, and `clear-playlist` then reports clearing it. Only `upload`, `sync` and `sync-album` should create a missing playlist.

Done when those two commands error on an unknown name, the README and `PLAYLIST_HELP` say which commands create, and `uv run pyright`, `ruff check` and `pytest` pass.
## Next
Split a lookup-only `resolve_playlist(playlist) -> int` (errors with click.ClickException on an unknown title) out of `resolve_or_create_playlist`, and use it in the two commands. This changes CLI behaviour.

Found by the unsmell run 20261107-224933 (finding b1-7; details in `.git/unsmell/20261107-224933/`).
