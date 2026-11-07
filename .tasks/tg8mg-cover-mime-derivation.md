---
status: todo
tags: [unsmell]
---
# Move the cover MIME derivation onto Cover

## Goal
The image MIME type is derived from `MP4Cover.imageformat` in `extract_cover` and again from the file suffix in `Cli.set_cover`; `Cover.mime` is a bare `str`.

Done when the smell is gone and `uv run pyright`, `ruff check` and `pytest` pass.
## Next
`Cover` owns the derivation (e.g. `Cover.from_image_file(path)`, and a PNG/JPEG `Literal` for `mime`); both sites call it.

Found by the unsmell run 20261107-210704 (findings b1-8, x-4; details in `.git/unsmell/20261107-210704/`).
