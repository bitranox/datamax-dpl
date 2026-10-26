# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- The test suite no longer reads the app and host configuration layers of the
  machine it runs on. An autouse fixture points lib_layered_config's three
  app-root variables (`LIB_LAYERED_CONFIG_ETC`, `_MAC_APP_ROOT`, `_PROGRAMDATA`)
  at an empty directory under `tmp_path`, so a system-wide deploy (for example a
  `/etc/xdg/<slug>/config.d/99-*.toml`) can no longer fail tests locally while
  CI stays green. The subprocess tests in `test_display.py` and
  `test_logging_dotenv_isolation.py` dropped every `LIB_LAYERED_CONFIG*`
  variable and so read the real system layers; they now keep the redirect.

## [0.2.1] - 2026-10-25

### Fixed

- Library: `RenderResult.model_dump_json()` and `BatchResult.model_dump_json()`
  raised `PydanticSerializationError` on any job that is not valid UTF-8, which
  includes plain text with an umlaut (`Größe` is written as `Gr\x94\xe1e`). The
  job bytes are now written to JSON as base64 and read back from it the same
  way. The CLI's own JSON output is unchanged.

## [0.2.0] - 2026-10-24

### Added

- Library: `FindingCode`, `BarcodeProblemCode`, `CommandKind`, `StatusProblem`,
  `HardwareOption` and `MediaType` import from `datamax_dpl`, like the other
  enums, so `RenderOptions(options={HardwareOption.DATAMATRIX})` works as its
  docstring shows.

### Fixed

- `verify -` read the label from stdin twice, so the second read found nothing
  and the command failed with "- is not valid JSON". The label is now read once.
- `explain` on a stream with an unreadable format setting (`Qab12`, `Hxx`, a
  bare `:`, a speed letter that is no speed) reported only a `ValueError` and
  exited 2, hiding every other command. It now explains the whole stream,
  reports the setting as a problem (`Qab12: ValueError`), ignores it, and exits
  1, the way an unreadable record is already handled.
- `docs/capabilities.md` named 32 of the finding codes. A new "Finding codes"
  section lists every one with its severity, and a test keeps it in step with
  the code.

### Changed

- `detect --json` reports the label size unit as `"in"` or `"mm"`, the values
  `Unit` uses everywhere else, instead of the printer's raw `"IN"` / `"MM"`.
- An unknown hardware option is refused with exit 2 instead of being ignored:
  in `[printer] options`, in its environment variable, and in `--option`. A
  misspelt option used to make the feature it unlocks look unavailable, with no
  message saying why.
- Library: fixed vocabularies are enums instead of plain strings.
  `Finding.code` is `FindingCode`, `BarcodeProblem.code` is
  `BarcodeProblemCode`, `DecodedCommand.kind` is `CommandKind`,
  `PrinterStatus.problems` holds `StatusProblem`, `MemoryReport.units` is
  `Unit`, `RenderOptions.options` holds `HardwareOption`, and
  `CapturedImage.media_type` is `MediaType`. Each member is a `str` that compares
  equal to its old value, except `MemoryReport.units`, which was `"IN"` / `"MM"`
  and is now `"in"` / `"mm"`. The JSON output is unchanged apart from that
  `detect` unit.

## [0.1.1] - 2026-10-24

### Fixed

- `explain` keeps a field's counter (`+001`, `-005`, `> 02`, `<012`) on the
  rebuilt label, and reports a counter line that has no text or barcode field
  right before it.
- `explain` on a file holding more than one label format reports why it
  rebuilds no label, and exits 1, instead of returning `label: null` with no
  problem.

### Security

- Development dependencies: filelock 4.2.1 (CVE-2026-112906, CVE-2026-112914,
  CVE-2026-112940; on Python 3.11 and newer, where the fix exists) and
  virtualenv 21.14.10 (CVE-2026-113713). The installed package's own
  dependencies are unchanged.

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
