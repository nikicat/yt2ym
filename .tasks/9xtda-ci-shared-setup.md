---
status: todo
tags: [unsmell]
---
# Deduplicate the CI job setup

## Goal
All four CI jobs repeat the same checkout (SHA-pinned, `persist-credentials: false`) + setup-uv (SHA-pinned) + `uv sync --frozen` steps.

Done when the smell is gone and `uv run pyright`, `ruff check` and `pytest` pass.
## Next
Options: a local composite action `.github/actions/setup/action.yml` used by each job; or merge typecheck, lint and zizmor into one job, keeping test separate. Keep SHA pinning and the zizmor job.

Found by the unsmell run 20261107-210704 (findings b2-5; details in `.git/unsmell/20261107-210704/`).
