# Contributing Guide

Thanks for helping improve datamax-dpl. This page covers the workflow and the checks a change has
to pass.

## 1. Workflow

1. Fork and branch, with a short branch name (`feature/dot-size-preview`, `fix/serial-timeout`).
2. Keep commits focused; leave unrelated refactors for their own change.
3. Run `make test` before pushing.
4. Update the documentation and the changelog entries the change affects.
5. Open a pull request that references the issue, if there is one.

## 2. DPL facts need a source

Every rule about what a printer accepts must trace to a line in
[docs/dpl-reference.md](docs/dpl-reference.md), which summarises the 1998 Datamax DPL manual. If a
change needs a fact that is not there, add it to that file with its source (a manual section, or
a test print on a named model and firmware). Do not paste text from the manual; it is copyrighted.

Test prints are welcome. A report that says "model X, firmware Y, this label, this photo" is the
kind of evidence the project lacks most right now.

## 3. Commits and pushes

- Commit messages are imperative (`Add dot size to the preview`, `Fix serial timeout`).
- `make test` runs the full lint, type and test pipeline. It makes no commit, but it may raise
  dependency floors in `pyproject.toml` and apply ruff's formatting, so look at the working tree
  afterwards.
- `make push` runs the tests, commits and pushes. Give the message with `MSG="..."`; without one
  it prompts. `make help` lists every target.

## 4. Coding standards

- Keep the layers: `domain` (pure label model, profiles, encoders) imports nothing from
  `application` or `adapters`; adapters (CLI, transports, cameras, configuration) may import both.
  import-linter enforces this.
- Small, single-purpose functions; pyright in strict mode with zero errors; Google-style
  docstrings; an `__all__` in every module.
- Keep runtime dependencies few. Pillow stays in the optional `preview` extra.

## 5. Tests

- `make test` runs Ruff, Bandit, import-linter, pip-audit, Pyright and Pytest with coverage. See
  [DEVELOPMENT.md](DEVELOPMENT.md).
- Drive the real code through its public functions. Inject collaborators (printer opener,
  camera, clock) through parameters, use the in-memory adapters in `datamax_dpl.adapters.memory`,
  and do not monkeypatch the module under test.
- Mark every test `@pytest.mark.os_agnostic` or with the `os_*` marker it needs.
- A regression test must fail once on the old code before it counts.
- After changing a printer profile, run `.venv/bin/python scripts/gen_capabilities.py --write`.

## 6. Checklist

- [ ] `make test` passes locally.
- [ ] README, `docs/` and `CHANGELOG.md` describe the change.
- [ ] Every new DPL rule points at `docs/dpl-reference.md`.
- [ ] No generated files, virtual environments or secrets are committed.

## 7. Secrets

Tokens (Codecov, PyPI) and camera passwords belong in `.env` (ignored by git) or CI secrets,
never in a tracked file.
