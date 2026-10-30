# Module Reference: datamax-dpl

## Status

Describes the modules as they are in the source tree. Update it with every change that adds,
moves or removes a module. Hardware behaviour is unverified: no real printer or camera has been
attached yet.

## Links & References

**Repository:** https://github.com/bitranox/datamax-dpl
**PyPI:** https://pypi.org/project/datamax-dpl/ (not published yet)
**Documentation:** README.md, INSTALL.md, CONFIG.md, DEVELOPMENT.md, CONTRIBUTING.md,
docs/label-spec.md, docs/capabilities.md, docs/dpl-reference.md

---

## Problem Statement

Datamax label printers speak DPL, a line-oriented format of fixed-width records. Writing it by
hand is error-prone, and the 18 models in the 1998 manual differ in resolution, print width,
speeds, fonts, graphics and barcodes, so a job that prints on one model is refused or misprinted on
another. Scripts and LLM agents need a format they can write safely and an answer they can parse.

## Solution Overview

1. A JSON label model (`domain/label.py`) in millimetres, inches or dots, with DPL's bottom-up
   coordinates and `{name}` placeholders.
2. One profile per model (`domain/profiles.py`) with limits and a feature table, plus `generic`
   and `permissive`.
3. Encoders that write the DPL records and record a finding for every deviation; the fallback
   policy (`degrade` or `strict`) decides whether a deviation is a warning or an error.
4. Transports (tcp, serial, device file, file, stdout), status and model detection, a preview
   PNG, and a camera loop (`verify`).
5. A CLI whose label and printer commands answer with a JSON envelope `{ok, command, data,
   skipped}` and exit 0 / 1 / 2.

## Architecture Integration

```
composition/   wires production or in-memory adapters into AppServices
adapters/      CLI, configuration, logging, transports, cameras, preview lettering, memory doubles
application/   use cases and ports (Protocols)
domain/        label model, profiles, encoders, decoder, preview, status parsing; no I/O
```

import-linter enforces the direction: domain imports nothing above it; application imports
domain; adapters import application and domain; composition imports everything.

Data flow of `datamax-dpl print`: `adapters/cli/label_io.py` reads the label and records ->
`application/use_cases.py` fills placeholders and calls `domain/render.py` -> the encoders in
`domain/encode_*.py` and `domain/job.py` write records and findings -> `print_result` in
`application/use_cases.py` refuses a job with an error finding or sends the bytes through a
transport from `adapters/transport/` -> `adapters/cli/runner.py` and `adapters/cli/envelope.py`
turn the outcome into output and an exit code.

---

## File Index

### Domain (`src/datamax_dpl/domain/`)

- `label.py` - the label model: job settings and the nine element types.
- `enums.py` - units, rotations, fill patterns, attributes, speed letters, media, policy, severity.
- `units.py` - length conversion between label units, DPL units and printer dots.
- `symbology.py` - barcode symbologies and their DPL identifiers.
- `barcodes.py` - barcode data checks per symbology, check digits, PDF417 and DataMatrix fields.
- `charset.py` - text to font bytes (code page 437, symbol sets), unprintable characters.
- `profiles.py` - the printer model profiles and their feature tables.
- `findings.py` - findings and the collector that applies the fallback policy.
- `render_context.py` - render options, the per-render context, content names of drawn images
  (`drawn_image_name`).
- `render.py` - `build_job` and `render`: label to DPL bytes plus findings.
- `job.py` - system commands, format settings, quantity and cutting.
- `encode_common.py` - positions, field length, attributes and counters shared by encoders.
- `encode_text.py` - text and scalable text fields and their font fallbacks.
- `encode_graphics.py` - lines, boxes, circles, polygons, images, QR Codes (`encode_qr`).
- `encode_barcode.py` - barcode fields, symbology gates, bar widths.
- `raster.py` - 1-bit raster drawing.
- `raster_fallback.py` - graphics a model cannot draw, as downloaded images.
- `qr.py` - the QR Code module matrix via segno (`qr_bitmap`, `QrDataTooLongError`).
- `image_records.py` - the Datamax image download format.
- `records.py` - byte-level formatting of DPL records.
- `placeholders.py` - `{name}` placeholders and filling them from records.
- `preview.py` - preview bitmap at printer resolution.
- `png.py` - minimal PNG writer.
- `decoder.py` - reads DPL back: explains commands, rebuilds labels.
- `status.py` - status requests and parsing of the answers.
- `memory.py` - the memory module directory (`<STX>W`) and its lenient parser.
- `detect.py` - suggests model profiles from printer answers.
- `camera.py` - camera settings and captured images.
- `errors.py` - domain exception types.

### Application (`src/datamax_dpl/application/`)

- `ports.py` - Protocols the adapters implement (printer, camera, clock, configuration, logging).
- `use_cases.py` - render one label or a batch, print, send commands, query status, detect, read the memory directory.
- `verify.py` - print, wait for the printer, photograph, return photo and expected preview.

### Adapters (`src/datamax_dpl/adapters/`)

- `cli/main.py` - entry point and execution wrapper.
- `cli/root.py` - root command group and global options (`--json`, `--set`, `--profile` ...).
- `cli/commands/labels.py` - render, validate, explain, schema, models, capabilities.
- `cli/commands/printer.py` - print, status, detect, memory, reset/cancel/pause/feed/cut, preview, capture, verify.
- `cli/commands/model_options.py` - options shared by rendering and printer commands.
- `cli/commands/config.py` - config, config-deploy, config-generate-examples.
- `cli/commands/info.py` - info.
- `cli/label_io.py` - reading labels and data records from files or stdin.
- `cli/settings.py` - `[printer]` and `[camera]` settings with command-line overrides.
- `cli/runner.py` - runs a command body, maps outcomes to output and exit codes.
- `cli/envelope.py` - the JSON envelope.
- `cli/payloads.py` - per-command JSON payloads.
- `cli/config_load.py` - configuration loading for the CLI.
- `cli/context.py`, `cli/constants.py`, `cli/exit_codes.py`, `cli/safe_console.py`, `cli/typed_click.py` - CLI plumbing.
- `transport/uri.py` - printer URI parsing.
- `transport/tcp.py`, `transport/serial.py`, `transport/device.py`, `transport/file.py` - the transports.
- `transport/__init__.py` - `open_printer(uri, timeout=...)`.
- `camera/command.py`, `camera/http.py`, `camera/file.py` - the camera kinds; `camera/__init__.py` picks one.
- `preview/__init__.py` - lettered preview with Pillow when the `preview` extra is installed.
- `config/loader.py`, `config/deploy.py`, `config/display.py`, `config/overrides.py` - layered configuration.
- `config/defaultconfig.toml`, `config/defaultconfig.d/*.toml` - bundled defaults (`50-printer`, `60-camera`, `90-logging` ...).
- `logging/setup.py` - lib_log_rich initialisation.
- `memory/printer.py` (`RecordingPrinter`), `memory/camera.py` (`FakeCamera`), `memory/config.py`, `memory/logging.py` - in-memory doubles.

### Composition and entry points

- `composition/__init__.py` - `build_production()` and `build_testing()`.
- `entry.py` - console script `datamax-dpl`.
- `__main__.py` - `python -m datamax_dpl`.
- `__init__.py` - package exports (`get_config`, `print_info`).
- `__init__conf__.py` - static package metadata.

### Outside the package

- `scripts/gen_capabilities.py` - generates the model tables of `docs/capabilities.md`.
- `examples/` - sample labels and data records.
- `notebooks/Quickstart.ipynb` - executed by CI.
- `tests/` - one `test_<module>.py` per module or behaviour; `test_capabilities_doc.py` keeps the
  capabilities document current.

---

## Testing Approach

Tests drive public functions and the CLI. Collaborators are injected: the CLI tests wire
`build_testing()` with a `RecordingPrinter` and a `FakeCamera`; transports are tested against real
sockets on 127.0.0.1 and pseudo-terminals. Doctests in the modules run with the suite.

## Known Limitations

- Only the 1998 DPL manual is covered; later models and commands are not. QR codes are drawn and
  sent as images.
- Only an Allegro 2 has been checked on a real printer; no camera has been attached yet.
- The package root exports the label model and its enums, `render`, `build_job`, `render_batch`,
  `fill_label`, `open_printer`, `explain`, the profiles and the preview (`datamax_dpl.__all__`);
  anything else is reached through module paths (`datamax_dpl.domain.render` and so on).
