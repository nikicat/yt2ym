---
status: todo
tags: [unsmell]
---
# Stop the README repeating code comments

## Goal
README "How it works" repeats facts that the code comments own: the reason for `--no-embed-chapters` (also the comment on that flag in `download_audio`) and "native m4a/AAC, no transcoding" (also the `-x` comment and `_download_to`'s docstring, the latter planned in T7wy23).

Done when the smell is gone and `uv run pyright`, `ruff check` and `pytest` pass.
## Next
Keep the README at the user's level (what you get: m4a audio, correct titles) and leave the mechanism and its reasons to the code comments.

Found by the unsmell run 20261107-221013 (findings b2-2, x-1, x-2; details in `.git/unsmell/20261107-221013/`).
