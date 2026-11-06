## What

<!-- One or two sentences: what changes and why. Link the issue if there is one. -->

## Checklist

- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run mypy src` passes
- [ ] `uv run pytest -q` passes, with tests for the change
- [ ] `CHANGELOG.md` has an entry under Unreleased (user-visible changes only)
- [ ] The commit messages follow Conventional Commits
- [ ] Nothing internal or secret is in the diff (this repository is public)
