---
status: todo
tags: [unsmell]
---
# Use functools.cached_property for Cli.ym

## Goal
`Cli.ym` is a hand-rolled lazy property over an optional `_ym` field.

Done when the smell is gone and `uv run pyright`, `ruff check` and `pytest` pass.
## Next
`@functools.cached_property` on `ym`; the `_ym` field goes.

Found by the unsmell run 20261107-210704 (findings b1-12; details in `.git/unsmell/20261107-210704/`).
