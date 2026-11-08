---
status: todo
tags: [unsmell]
---
# Split the two meanings of --force

## Goal
One `force` bool carries two meanings (re-download; wipe the playlist before uploading) and is threaded through `download`, `upload`, `sync`, `download_to` and `_upload_files`, where it only picks a branch; with `split_chapters`, `url` and `out` it forms a clump passed from `download`/`sync` to `download_to`.

Done when the smell is gone and `uv run pyright`, `ruff check` and `pytest` pass.
## Next
Options: two named values (e.g. `redownload=` and a replace-vs-skip upload mode), or one per-command request object holding url, dest, split_chapters and force.

Found by the unsmell run 20261107-210704 (findings b1-22; details in `.git/unsmell/20261107-210704/`).
