# Installation

datamax-dpl needs Python 3.10 or newer. Every install registers one command, `datamax-dpl`;
`python -m datamax_dpl` runs the same CLI.

The package is not on PyPI yet. Until it is, install from GitHub; the PyPI lines below work once
the first release is published.

## Extras

| Extra     | Adds                          | Needed for                                                                                   |
|-----------|-------------------------------|----------------------------------------------------------------------------------------------|
| `preview` | Pillow (Python 3.11 or newer) | Lettered previews: `preview` and `verify` write the text into the PNG instead of grey cells. |

Serial printers work without an extra: pyserial is a regular dependency.

## With uv (recommended)

[uv](https://docs.astral.sh/uv/) installs and runs Python tools in isolated environments.

```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Run without installing:

```bash
uvx --from git+https://github.com/bitranox/datamax-dpl datamax-dpl --help
uvx --from "datamax-dpl[preview] @ git+https://github.com/bitranox/datamax-dpl" datamax-dpl --help
```

Install as a command line tool (isolated environment, added to PATH):

```bash
uv tool install git+https://github.com/bitranox/datamax-dpl
# once on PyPI
uv tool install datamax-dpl
uv tool upgrade datamax-dpl
```

Add it to a project:

```bash
uv add git+https://github.com/bitranox/datamax-dpl
# once on PyPI
uv add datamax-dpl
```

## With pip

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install "git+https://github.com/bitranox/datamax-dpl"
# with lettered previews
pip install "datamax-dpl[preview] @ git+https://github.com/bitranox/datamax-dpl"
# once on PyPI
pip install datamax-dpl
pip install "datamax-dpl[preview]"
```

## From a checkout

```bash
git clone https://github.com/bitranox/datamax-dpl.git
cd datamax-dpl
pip install .                  # runtime only
pip install -e ".[dev]"        # editable, with the development tools
```

`make dev` does the editable install with uv; see [DEVELOPMENT.md](DEVELOPMENT.md).

## Supply-chain note

The repository commits a `uv.lock` that pins every dependency, transitive ones included, to an
exact version and file hash, with a cooldown (`exclude-newer` under `[tool.uv]` in
`pyproject.toml`) that leaves out releases younger than that. CI installs from the lock. The
installable package only declares lower bounds (`>=`), so a plain `pip install` takes the newest
release of every dependency at install time. To run the exact dependency tree CI tests, use the
lock from a checkout:

```bash
git clone https://github.com/bitranox/datamax-dpl.git
cd datamax-dpl
uv sync --locked
uv run datamax-dpl --help
```

A cooldown of your own (`uv tool install --exclude-newer "7 days" ...`) fails while the package
requires a release of one of its own libraries that is younger than the cooldown; the
`exclude-newer-package` table in `pyproject.toml` exempts those libraries for the lock.

## Check the install

```bash
datamax-dpl --version
datamax-dpl info
```
