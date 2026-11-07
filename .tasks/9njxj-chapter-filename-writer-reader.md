---
status: todo
deps: [T5ksq2]
tags: [unsmell]
---
# One owner for the chapter filename format (writer side)

## Goal
The chapter filename format is spelled three times: the `chapter:` output template in `download_audio`, `CHAPTER_PAT`, and the comment in `Cli._download_to` that recovers titles from "NN - title". Task T5ksq2 covers only the reader side (parsing).

Done when the smell is gone and `uv run pyright`, `ruff check` and `pytest` pass.
## Next
Once T5ksq2's `Chapter` type exists, give it the output template too (e.g. `Chapter.OUTTMPL`), so writer and reader live together, plus a round-trip test: a name rendered from the template parses back to the same number and title.

Found by the unsmell run 20261107-221013 (findings b1-5; details in `.git/unsmell/20261107-221013/`).
