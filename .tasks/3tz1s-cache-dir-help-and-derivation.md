---
status: todo
tags: [unsmell]
---
# Fix the cache-dir help text and its duplicated derivation

## Goal
`OUT_HELP` and README (subcommand table, How it works) say the default is `~/.cache/yt2ym/<videoid>/`, which is false when `$XDG_CACHE_HOME` is set (`default_cache_dir` honours it). `out or default_cache_dir(probe_url(url).id)` is written in both `Cli.download` and `Cli.sync`.

Done when the smell is gone and `uv run pyright`, `ruff check` and `pytest` pass.
## Next
Help/README say "`$XDG_CACHE_HOME/yt2ym/<videoid>/` (default `~/.cache/…`)"; one helper resolves the destination from `(url, out)` and both commands call it.

Found by the unsmell run 20261107-210704 (findings b1-20, x-1; details in `.git/unsmell/20261107-210704/`).
