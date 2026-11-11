# SKILL.md review, 2026-11-11: correctness and completeness pass

Type: reference skill. Test: retrieval by quote-back, pre-edit text (RED) against edited text
(GREEN), on opus and haiku.

## Change

- SKILL.md: where `render` puts the job under `--json` (`data.dpl`, `data.dpl_base64`), that
  `render -o FILE` still prints the envelope and `render -o -` is refused with `--json` (exit 2).
- references/cutting-stops-and-printer-state.md: `feed` takes no `--model`/`--cutter`; with the
  cutter unset it sends the form feed alone (`software_switches_not_sent`); the remedy is
  `--set printer.model=ID --set printer.cutter=yes` (or `no`) or a `--printer NAME` section.
- references/printer-controls-and-calibration.md: `feed` sends `<STX>V0` then `<STX>F`;
  `feed --media` sends the stop of a label that is not cut, never the cutter stop, and reports
  only the model stop position; the Allegro 2 byte strings of both feeds.
- references/exit-codes-timeouts-and-retries.md: `link_bytes_per_second = 0` is the key that
  turns the tcp close wait off, margin included; an answer ends the wait; a failed write leaves
  none; serial, device, file and stdout never wait.
- references/library.md: `open_printer(..., tuning=TransportTuning(...))` and
  `functools.partial(open_printer, tuning=...)` in a `PrinterEndpoint`.

## Commands executed (allegro-2, file: targets, isolated HOME/XDG)

- [x] `--set printer.model=allegro-2 feed`: `<STX>F` only, `software_switches_not_sent`.
- [x] same with `--set printer.cutter=yes` and with `=no`: `<STX>V0<STX>F` both.
- [x] `feed --media gap` with cutter yes and with no: `<STX>n<STX>c0000<STX>e<STX>V0<STX>f263<STX>F`.
- [x] `--json render l.json --model allegro-2 -o -`: exit 2; `-o FILE`: exit 0, envelope with
      `dpl` and `dpl_base64`.
- [x] Library: `open_printer('file:...', timeout=10, tuning=TransportTuning(...)).send(...)` and
      `print_label` with `PrinterEndpoint(open_printer=partial(open_printer, tuning=...))` run.
- [x] `tests/test_skill_matches_cli.py` passes; frontmatter loads to `['description', 'name']`
      (description 706 characters).

## Probe (6 questions: feed bytes and cutter remedy, `feed --media` stop and findings, link
## defaults and the off switch, library close wait, `--json render -o -`, `config` exit codes)

- [x] RED (pre-edit text from `git show HEAD`), opus and haiku: both answered NONE or inferred on
      Q1, Q2 (stop inferred, findings NONE), Q4 (NONE) and Q5 (NONE, read as a conflict with the
      one-envelope rule); Q3 named no key ("Set 0").
- [x] GREEN (edited text), opus and haiku: all six answered with direct quotes.
- [x] GREEN gaps closed: `cutter = no` also sends `<STX>V0`; the exact Allegro 2 bytes of both
      feeds; the exit code of `render -o -` under `--json`; `render -o FILE` under `--json`.
- [x] Re-probe of Q1, Q2 and Q5 on the final text, haiku: all three answered with direct quotes
      (`<STX>V0<STX>F` for cutter yes and no; the `feed --media gap` byte string; `-o FILE`
      prints the envelope, `-o -` exits 2). No new undecided gap.
- Re-probe gaps declined: what `<STX>n` and `<STX>e` mean is docs/dpl-reference.md material, not
  needed to run a feed; exit 0 for a successful `render -o FILE` follows from the "0 done" line of
  SKILL.md.
- [x] Re-probe gap closed: one label is written straight to `-o FILE` only once it passed
      (`adapters/cli/commands/labels.py`, `_rendered` and `_write_output`, `Path.write_bytes`);
      a `--data` batch writes beside the target and replaces it (library.md). SKILL.md says so.
- Tiers: opus, haiku. Cells: 4 plus 1 re-probe.
- Gaps declined: the sensor-select letter for `reflective` (`<STX>r`) is in the existing
  `<STX>e`/`<STX>r` line of printer-controls-and-calibration.md; whether `feed --media` sends
  start of print or label length is shown by the Allegro 2 byte string, which has neither.

Decisions: none - this skill is not mirrored into the central marketplace and keeps no decision ledger.
