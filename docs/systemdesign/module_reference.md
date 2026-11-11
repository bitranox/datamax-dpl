# Module Reference: datamax-dpl

## Status

Describes the modules as they are in the source tree. Update it with every change that adds,
moves or removes a module. Hardware behaviour is checked on one Allegro 2 only: no other model
has been attached.

## Links & References

**Repository:** https://github.com/bitranox/datamax-dpl
**PyPI:** https://pypi.org/project/datamax-dpl/
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
4. Transports (tcp, serial, device file, file, stdout), status and model detection, and a preview
   PNG.
5. A CLI whose label and printer commands answer with a JSON envelope `{ok, command, data,
   skipped}` and exit 0 / 1 / 2 (130 on Ctrl-C, 141 when the reader of the output went away).

## Architecture Integration

```
composition/   wires production or in-memory adapters into AppServices
adapters/      CLI, configuration, logging, transports, preview lettering, memory doubles
application/   use cases and ports (Protocols)
domain/        label model, profiles, encoders, decoder, preview, status parsing; no I/O
```

import-linter enforces the direction: domain imports nothing above it; application imports
domain; adapters import application and domain; composition imports everything.

Data flow of `datamax-dpl print`: `adapters/cli/label_io.py` reads the label and records ->
`print_label` in `application/print_flow.py` decides what the link allows and has
`application/use_cases.py` fill placeholders and call `domain/render.py` -> the encoders in
`domain/encode_*.py` and `domain/job.py` write records and findings -> `print_label` refuses a job
with an error finding or sends it through a transport from `adapters/transport/`, with
`application/upload_check.py` checking each label's images on a two-way link ->
`adapters/cli/runner.py` and `adapters/cli/envelope.py` turn the outcome into output and an exit
code.

---

## File Index

### Domain (`src/datamax_dpl/domain/`)

- `label.py` - the label model: job settings and the nine element types.
- `_invariants.py` - import-time checks that an update's fields and a lookup table stay in step with
  the models and enums they name.
- `enums.py` - units, rotations, fill patterns, attributes, speed letters, media, policy, severity,
  finding codes, command kinds, status flags, model families and hardware options.
- `units.py` - length conversion between label units, DPL units and printer dots.
- `symbology.py` - barcode symbologies and their DPL identifiers.
- `barcodes.py` - barcode data checks per symbology, check digits, PDF417 and DataMatrix fields.
- `charset.py` - text to font bytes (code page 437, symbol sets), unprintable characters.
- `profiles.py` - the printer model profiles and their feature tables.
- `frozen_mapping.py` - the read-only mapping frozen value objects hold (`FrozenMapping`); it copies
  and pickles.
- `held_images.py` - the image names a printer already holds in a job's image module.
- `printable.py` - text from outside the program made safe to show on a terminal.
- `reset_label.py` - the calibration label that takes the shift after a printer reset.
- `findings.py` - findings and the collector that applies the fallback policy.
- `render_context.py` - render options, the per-render context, content names of drawn images
  (`drawn_image_name`).
- `render.py` - `build_job` and `render`: label to DPL bytes plus findings; `iter_jobs` for a batch,
  and `job_context` / `feed_context`, the context a label's job or a feed builds its system commands with.
- `job.py` - system commands, format settings, quantity and cutting.
- `batch.py` - cutting every N labels when each label of a batch is its own format.
- `media_feed.py` - the feeds: `plain_feed` (`feed`) and `media_feed`, which returns the printer from continuous
  media to a label sensor (`feed --media`).
- `calibration.py` - the two calibration labels, across the web and along the feed.
- `transparency.py` - emulating a transparent label on a model without the `A` command.
- `encode_common.py` - positions, field length, attributes and counters shared by encoders.
- `encode_text.py` - text and scalable text fields and their font fallbacks.
- `encode_graphics.py` - lines, boxes, circles, polygons, images, QR Codes (`encode_qr`).
- `encode_barcode.py` - barcode fields, symbology gates, bar widths.
- `geometry.py` - pixel points, rectangles, sizes and spans on the grid of printer dots.
- `raster.py` - 1-bit raster drawing.
- `raster_fallback.py` - graphics a model cannot draw, as downloaded images.
- `overlap.py` - which fields share printed dots (`find_overlaps`): groups of exact fields, and text,
  barcodes and stored images by their rectangle.
- `qr.py` - the QR Code module matrix via segno (`qr_bitmap`, `QrDataTooLongError`).
- `image_records.py` - the Datamax image download format.
- `image_budget.py` - the image memory budget: bytes a download holds, halving (`halve`, `magnify`).
- `records.py` - byte-level formatting of DPL records.
- `placeholders.py` - `{name}` placeholders and filling them from records.
- `preview.py` - preview bitmap at printer resolution.
- `png.py` - minimal PNG writer.
- `decoder.py` - reads DPL back: explains commands, rebuilds labels.
- `status.py` - status requests and parsing of the answers.
- `memory.py` - the memory module directory (`<STX>W`) and its parser.
- `timing.py` - the seconds rule for timeouts (`check_timeout`, named by `TimeoutName`), the
  read-until-silence deadline, and `TransportTuning` (how the transports pace and time what they write).
- `detect.py` - suggests model profiles from printer answers.
- `errors.py` - domain exception types.

### Application (`src/datamax_dpl/application/`)

- `ports.py` - Protocols the adapters implement (printer, job spool, clock, sleep, configuration, logging), and
  the values they take as one: `PrinterEndpoint` (printer URI, timeout and opener) and
  `DeployPermissionRequest` (the permission values a config deploy hands to lib_layered_config).
- `use_cases.py` - render one label or a batch, print, send commands, query status, detect, read the memory directory.
- `upload_check.py` - check a job's images reached the printer before the format that places them.
- `print_flow.py` - print a label in one call, deciding what the link allows; the `print` command and library callers take it.

### Adapters (`src/datamax_dpl/adapters/`)

- `cli/main.py` - entry point and execution wrapper.
- `cli/root.py` - root command group and global options (`--json`, `--set`, `--profile` ...).
- `cli/commands/labels.py` - render, validate, explain, schema, models, capabilities.
- `cli/commands/printer.py` - print, status, detect, memory, reset/cancel/pause/feed/cut, preview.
- `cli/commands/model_options.py` - options shared by rendering and printer commands.
- `cli/commands/calibrate.py` - print the calibration labels, store what was measured on them.
- `cli/commands/config.py` - config, config-deploy, config-generate-examples.
- `cli/commands/info.py` - info.
- `cli/label_io.py` - reading labels and data records from files or stdin.
- `cli/json_array.py` - walks a JSON array `--data` file one element at a time.
- `cli/job_file.py` - writing a batch's job to a file as it is rendered, replacing the target only when every record passed.
- `cli/job_spool.py` - keeping a batch's records on disk as the check pass rendered them, so the send reads them back instead of rendering again (`FileJobSpool`, a `JobSpool`).
- `cli/interrupt.py` - noting a Ctrl-C before it is raised, so a pass over the records finds one a C extension swallowed.
- `cli/gone_reader.py` - telling a reader of the output that went away from a failure that needs saying.
- `cli/settings.py` - `[printer]` and `[preview]` settings with command-line overrides.
- `cli/runner.py` - runs a command body, maps outcomes to output and exit codes.
- `cli/envelope.py` - the JSON envelope and the command names it carries (`CommandName`).
- `cli/payloads.py` - per-command JSON payloads.
- `cli/config_load.py` - configuration loading for the CLI.
- `cli/context.py`, `cli/constants.py`, `cli/exit_codes.py`, `cli/safe_console.py`, `cli/typed_click.py` - CLI plumbing.
- `transport/uri.py` - printer URI parsing.
- `transport/tcp.py`, `transport/serial.py`, `transport/device.py`, `transport/file.py` - the transports;
  `TcpTransport` waits before close until a link behind a print server carried the job, timed by a `LinkTimer`.
- `transport/__init__.py` - `open_printer(uri, timeout=...)`.
- `preview/__init__.py` - lettered preview with Pillow when the `preview` extra is installed.
- `config/loader.py`, `config/deploy.py`, `config/display.py`, `config/overrides.py` - layered configuration.
- `config/sections.py` - the `[printer]`, `[printers.<name>]` and `[preview]` section names (`ConfigSection`).
- `config/user_edit.py` - writes a printer's values into the user layer (`calibrate set`).
- `config/defaultconfig.toml`, `config/defaultconfig.d/*.toml` - bundled defaults (`50-printer`, `90-logging` ...).
- `logging/setup.py` - lib_log_rich initialisation.
- `error_text.py` - an exception's message as a user reads it, for every adapter that quotes one in its own message.
- `memory/printer.py` (`RecordingPrinter`), `memory/config.py`, `memory/logging.py` - in-memory doubles.

### Composition and entry points

- `composition/__init__.py` - `build_production()` and `build_testing()`.
- `entry.py` - console script `datamax-dpl`.
- `__main__.py` - `python -m datamax_dpl`.
- `__init__.py` - the package's public API (`datamax_dpl.__all__`, see Known Limitations).
- `__init__conf__.py` - static package metadata.
- the `__init__.py` of each layer and subpackage (`domain`, `application`, `adapters` and its `cli`,
  `cli/commands`, `config`, `logging`, `memory`) - the package docstring and re-exports of the
  subpackage's main names (the CLI commands, the config and memory adapters, the ports).

### Outside the package

- `scripts/gen_capabilities.py` - generates the model tables of `docs/capabilities.md`.
- `scripts/image_ladder.py`, `length_ladder.py`, `qr_batch.py`, `status_cycles.py` - test prints and
  cycles for measuring a real printer; the tests import them too.
- `examples/` - sample labels and data records.
- `notebooks/Quickstart.ipynb` - executed by CI.
- `tests/` - one `test_<module>.py` per module or behaviour; `test_capabilities_doc.py` keeps the
  capabilities document current.

---

## Testing Approach

Tests drive public functions and the CLI. Collaborators are injected: the CLI tests wire
`build_testing()` with a `RecordingPrinter`; transports are tested against real sockets on
127.0.0.1 and pseudo-terminals. Doctests in the modules run with the suite.

## Known Limitations

- Only the 1998 DPL manual is covered; later models and commands are not. QR codes are drawn and
  sent as images.
- Only an Allegro 2 has been checked on a real printer.
- The package root exports the label model and its enums, `render`, `build_job`, `render_batch`,
  `iter_batch`, `check_batch`, `record_renders`, `fill_label`, `open_printer`,
  `printer_uri_is_one_way`, `print_label`, `PrintRequest`, `print_checked`, `explain`, `get_config`,
  `TransportTuning`, `HeldImages`, the application ports, the profiles, the calibration labels, the preview and the
  errors (`datamax_dpl.__all__`); anything else, such as `job_context` and `feed_context`, is reached through module
  paths (`datamax_dpl.domain.render` and so on).
