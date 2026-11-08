---
status: todo
tags: [unsmell, bug]
---
# Make set-cover fail on an audio file without a cover

## Goal
`yt2ym set-cover PLAYLIST file.m4a`, given a file with no embedded cover, only logs "no APIC cover in …; skipping playlist cover" and exits 0, so a script cannot tell that nothing happened. The message also says APIC for .m4a files, which have no APIC frame.

Done when `set-cover` errors in that case, `sync`'s cover step still skips quietly, and `uv run pyright`, `ruff check` and `pytest` pass.
## Next
`Cli.set_cover` raises `click.ClickException("no embedded cover in <file>")` when `extract_cover` returns None. `_set_cover_from` keeps its skip for `sync`, with format-neutral wording ("no embedded cover").

Found by the unsmell run 20261108-021258 (finding b1-12; details in `.git/unsmell/20261108-021258/`).
