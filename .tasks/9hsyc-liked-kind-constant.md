---
status: todo
tags: [unsmell]
---
# Name the Liked playlist kind once

## Goal
"Kind 3 is Liked" is written in `Cli.playlists` (the hard-coded `3\tМне нравится (Liked)` line), in `PLAYLIST_HELP`, and in `README.md` (the `--playlist` paragraph). The three must agree.

Done when the code spells it once, and `uv run pyright`, `ruff check` and `pytest` pass.
## Next
A `LIKED_KIND = 3` constant used by `Cli.playlists` and formatted into `PLAYLIST_HELP`. The README example can stay.

Found by the unsmell run 20261107-224933 (finding b1-8; details in `.git/unsmell/20261107-224933/`).
