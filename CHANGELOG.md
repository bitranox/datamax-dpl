# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-10-23

First release. Built from the 1998 DPL programmer's manual and tested against
simulated printers and cameras only; no real printer or camera has been
attached yet.

### Added

- Label format in JSON (millimetres or inches, DPL-native bottom-left origin):
  text in the internal and scalable fonts, lines, boxes, circles, polygons,
  images, and barcodes including Code 128, EAN, UPC, Code 39, PDF417, MaxiCode
  and DataMatrix. `datamax-dpl schema` prints its JSON Schema.
- 20 profiles for the 18 printer models of the manual, with firmware- and
  option-dependent features; `models` and `capabilities` list them, and
  `docs/capabilities.md` is generated from them.
- Capability checks per model with a `degrade` policy (documented fallbacks,
  such as a circle sent as a downloaded bitmap, reported as findings) and a
  `strict` policy that refuses instead.
- Data batches: `{name}` placeholders filled from CSV or JSON records, one label
  per record, with cutting every N labels.
- Commands `render`, `validate`, `explain` (DPL back to JSON), `preview` (PNG at
  the printer's resolution; lettered text with the `preview` extra on Python
  3.11+), `print`, `status`, `detect`, `feed`, `cut`, `pause`, `cancel` and
  `reset`.
- Transports: TCP (port 9100), serial, a device file (USB printer class), a
  file and stdout. A TCP or serial write times out when the printer stops
  taking data for longer than the timeout, on Windows and macOS too; a timed
  out serial write drops what the port had not sent yet.
- Camera feedback: `capture` takes a photo, `verify` prints a label and puts the
  photo next to the expected preview.
- `--json` / `--json-bare` envelopes and exit codes 0 / 1 / 2 on every label and
  printer command, for scripts and LLM agents.
- Public library API for rendering, validating and printing from Python.
- A Claude Code skill (`skills/datamax-dpl/`), installable from this repository
  as a plugin marketplace.
