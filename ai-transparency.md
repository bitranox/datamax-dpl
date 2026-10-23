# AI transparency

The author and owner of this project is the human, [@bitranox](https://github.com/bitranox). The
design decisions are theirs, and they answer for everything published here. An AI assistant
(Claude, run through the Claude Code CLI) was used as a tool under their direction, for much of
the typing and the legwork. This page says where, so you can weigh the work on its merits. The
general reasoning is in [ai-stance.md](ai-stance.md).

## The human's part

- The problem: driving old Datamax label printers from scripts and from LLM agents, with a label
  format a person or a model can write, and without finding out on the printer that a model has
  no circles or no metric formats.
- The shape of the solution: a JSON label model in millimetres with DPL's bottom-up coordinates;
  one profile per model from the 1998 DPL manual, with `generic` and `permissive` beside them;
  a documented fallback per missing feature and a policy to choose between degrading and
  refusing; JSON envelopes and three exit codes for machine callers; tcp, serial, device file,
  file and stdout transports; a preview and a camera loop to check output without guessing.
- The layering (domain, application, adapters, composition, enforced by import-linter), strict
  typing, the test rules (real sockets and pseudo-terminals instead of patched internals), and the
  release pipeline.
- Directing and reviewing the work. Commits go out under the human's name, with no AI co-author
  line.

## Where the AI was used

- Drafting `docs/dpl-reference.md`, the project's own summary of the DPL manual, for the human
  to review. Where the manual contradicts itself, that file names the reading taken.
- Writing the modules, tests and documentation to the human's design, and keeping the docs in
  step with the code: every command shown in the README and in `docs/` was run against the
  installed package before it was written down.
- Laying out options at decision points for the human to choose from.

None of the decisions, and none of the accountability, were the AI's.

## What has been checked, and what has not

`make test` is the gate, and CI runs the same checks on Linux, macOS and Windows for Python 3.10
to 3.15: Ruff, Pyright in strict mode, import-linter, Bandit, pip-audit and Pytest with coverage,
including the doctests in the source.

What has NOT been checked: output on a real printer. No Datamax printer and no camera have been
attached yet. The DPL the code writes follows the manual and passes the tests against simulated
printers, but nobody has watched a label come out. The README says so too, and this page will
change when that happens.

## Checking it yourself

- The source is in `src/datamax_dpl/`; the module map is in
  [docs/systemdesign/module_reference.md](docs/systemdesign/module_reference.md).
- `datamax-dpl explain` decodes any DPL file command by command, so you can compare the output
  with your own manual.
- The tests are in `tests/`. Run `make test`.

If something does not line up, open an issue.

## License

The code and text are under the MIT License (see [LICENSE](LICENSE)). Anthropic's terms put
ownership of model output with the user, so the human owns this and answers for it.
