# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `RenderOptions.scale_across` and `scale_along`: printed length divided by
  nominal length for one printer, across the web and along the feed (0.5-1.5,
  default 1.0). Positions, line and box lengths, barcode heights, stop and
  present distances and the shapes drawn as images are corrected on the axis
  they run along; fonts and user bitmaps, which are sized in dots, are not.
  Measured on an Allegro 2: along the feed it prints 0.977 of nominal.
- `[printer] scale_across` and `scale_along` in the configuration (and
  `--set printer.scale_along=0.977`) pass those factors to every command that
  renders; a value outside 0.5-1.5 is refused with its key named.
- Named printers: `[printers.<name>]` sections in the configuration, each
  with the keys of `[printer]` (which it inherits from) plus `label_width` and
  `label_height`. `--printer` takes a name or a URI; `render` and `validate`
  take it too, so a named printer supplies its model and scale factors. An
  unknown name is refused with the configured names listed.
- `across_label`, `along_label` and `calibration_labels`: the two labels to
  measure a printer's scale factors with. The across label has a frame on the
  label edges and a bar filling its inside; the along label has a bar up to
  200 mm long along the feed. Both have a tick every 10 mm, and no two of
  their fields share a dot, since printers without field attributes cancel
  overlapping dots.

### Fixed

- On the Allegro 2, lengths in 1/100 in printed 1.6 % short, so a box and an
  image of the same size did not line up: the printer prints one 1/100 in as
  two whole dots (0.250 mm, not 0.254 mm). Its profile now converts with that
  unit; 100 mm is written as 400, not 394. Other models keep the exact unit
  until measured.
- A label that sets no `dot_size` now gets `D11` in its format on every model
  whose own default is `D22`, which is every profile except `dmx-600` and
  `dmx-800`. Measured on an Allegro 2:
  with the printer's default every image and bitmap-font field printed at
  twice the size the preview showed. One dot in the label JSON is now one
  printhead dot; a label that sets `dot_size` keeps its own value.
- A `tcp://` connection that the print server refuses is retried for up to
  3 seconds before it is reported. Some print servers refuse a new
  connection for a moment after the previous one closed (about 1-2 s measured
  on a SerComm PS301), so `datamax-dpl status` run right after `print` failed
  with "Connection refused" although nothing was wrong.
- Downloaded images (the `image` element, and circles and polygons drawn as
  images on models without those commands) printed upside down and could lose
  rows or print empty. Image rows are now sent bottom row first, one row record
  per row, with no repeat records; both behaviours were measured on an
  Allegro 2. Image downloads grow for images with many identical rows.
- The test suite no longer reads the app and host configuration layers of the
  machine it runs on. An autouse fixture points lib_layered_config's three
  app-root variables (`LIB_LAYERED_CONFIG_ETC`, `_MAC_APP_ROOT`, `_PROGRAMDATA`)
  at an empty directory under `tmp_path`, so a system-wide deploy (for example a
  `/etc/xdg/<slug>/config.d/99-*.toml`) can no longer fail tests locally while
  CI stays green. The subprocess tests in `test_display.py` and
  `test_logging_dotenv_isolation.py` dropped every `LIB_LAYERED_CONFIG*`
  variable and so read the real system layers; they now keep the redirect.
- The test suite no longer reads the developer's own user configuration layer. `user_layer_in_tmp_path`
  is now autouse and points the user layer (`XDG_CONFIG_HOME`, `LIB_LAYERED_CONFIG_MAC_HOME_ROOT`,
  `_APPDATA`, `_LOCALAPPDATA`) at an empty directory under `tmp_path`; `HOME` is no longer redirected,
  because git and uv read it.
- The doctests under `src/` no longer run against the machine's configuration layers: a fixture in
  `tests/conftest.py` never reaches them. The two layer redirects now live in a rootdir `conftest.py`,
  their variable lists in `tests/layer_roots.py`; pyproject sets `testpaths` to include `src` and
  `tests` (which keeps the root `conftest.py` out of doctest collection), adds `tests` to `pythonpath`
  and pyright's `extraPaths`, and gives pyright an execution environment rooted at `tests/`. A test
  checks that every doctest collected from `src/` gets both redirects.

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
