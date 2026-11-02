# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `DatamaxDplError`, exported from the package root, is the base of every
  exception the package raises; `UnknownModelError`, `MissingFieldError`,
  `QrDataTooLongError` and `PreviewTooLongError` keep `LookupError` or
  `ValueError` as a second base. `AnswerNotEndedError`, `CameraError`,
  `InvalidLoggingConfigError`, `PrinterProtocolError`, `QrDataTooLongError`,
  `UnsupportedOperationError` and `UploadRefusedError` can now be imported
  from the root as well. The README states the contract.
- `[printer] status_wait` (default 30 s, at most 2147483 s) and
  `status_retry_interval` (0.25 s), also per named printer: `status` and
  `memory` keep trying for the printer's first answer while a print server is
  still busy with an earlier job. A connection that took no request - refused,
  timed out while connecting, or closed or reset before the request was sent
  or before a byte of answer - is tried again on a new one; an open but
  silent connection is read until the wait ends and never asked a second
  time, so no second request is left outstanding. A malformed answer is
  reported, not asked again. When the wait runs out the error gives the
  seconds actually waited (for `memory` at most 120 s, the cap on one
  answer), counts the attempts, quotes the last failure and names
  `status_wait` and `--timeout`. `TransportError.unanswered` marks the
  failures that may be retried, and `TransportError.reason` is the message
  without the transport's advice; `application.use_cases.AnswerWait`
  carries the wait for library callers.
- Upload check on a two-way link: `print` and `verify` send each label that
  downloads an image in two parts on one connection, its settings, clear and
  downloads first, then its format, cut and clear after only once the
  printer's graphic directory (`<STX>W`) lists every name the label
  downloaded. A download into a full module is lost with no error, and how
  much of a module an image takes depends on its content, so no count made
  before sending can tell. A name missing, or no answer, starts the next
  round: round 2 clears the module where a clear may go (`<STX>q` on the
  model's built-in RAM, the plain Prodigy's `<STX>R` for the label's own
  names, never a plug-in module) and uploads again; where none may go it
  uploads only what the last read did not list, reading the names again first
  when that read got no answer. Round 3 clears again and uploads the images
  halved 2x2 (not under `strict`). Still missing: the label is refused
  (`image_upload_refused`, error), its format and every label after it are not
  sent, and the module is cleared again where a clear may go. Each round after
  the first reports `image_upload_retried` (info); a label that, rendered
  again for a round, has an error finding is refused there
  (`image_upload_render_refused`, error) before anything of that round is
  sent. Each label of a `--data` batch is checked on its own and passes on to
  the next what its last answered read listed. A job whose images go to module
  `C` is refused on a two-way link before anything is sent
  (`image_module_unlisted`, error): no directory lists the default module by
  that letter. A one-way link (a file, stdout, a device file) and
  `render`/`validate` never check. As the check reads what the printer stored,
  `print` and `verify` on a two-way link leave the model's image memory budget
  out and send images at full size (`image_budget_skipped`, info, says when
  the budget would have halved them); a value set in `image_memory_bytes`
  still applies. The longest a label waits before `image_upload_refused`, with
  W for `stored_names_wait`, is 3 + 2 x `upload_check_rounds` x W, 183 s with
  the defaults, for the first label on built-in RAM or on a plug-in module;
  213 s on built-in RAM where the names are read before the job; for a later
  label of a batch 180 s on built-in RAM and 150 s on a plug-in module (180 s
  when it reads the names before its round 1). CONFIG.md breaks these down.
  Library: `RenderResult.upload_end`, `retry_clear`, `retry_clear_empties` and
  `retry_clear_may_go` carry what a caller needs to run the check,
  `RenderOptions.model_image_budget` (default True) leaves the model's budget
  out when False, and `application.upload_check.send_checked` and
  `send_checked_label` run the check and return `CheckedSend` (`result`,
  `all_sent`).
- `[printer] upload_check_rounds` (default 3, 1 to 3): how many rounds the
  upload check runs before `image_upload_refused`. 1 uploads and reads once, 2
  adds the clear and the second upload, 3 the halved upload.
- `[printer] stored_names_wait` (default 30 s, more than 0, at most 2147483
  s): how long `print` and `verify` keep trying for each read of the printer's
  stored image names, before a job no clear precedes and in every round of the
  upload check, pausing `status_retry_interval` between two connections. The
  answer's last byte must arrive within it; the 1 s of silence that ends the
  answer comes on top. `--timeout` leaves it.
- `[printer] held_image_severity` (`info`, `warning` or `error`, default
  `warning`). Where no clear precedes a label's downloads (a module that is
  not the model's built-in RAM, a model that can clear nothing), `print` and
  `verify` first read the printer's stored graphic names and leave out the
  download of each name it holds (`image_name_held`); where the names cannot
  be read every image is downloaded (`image_names_unread`). The key sets the
  severity of both findings, and `error` refuses such a label whatever
  `policy` says. `RenderOptions.held_image_severity` carries it.
- `[printer] image_module` (`A` to `E`, unset by default): the memory module
  images are downloaded to. Unset takes the model's built-in module
  (`datamax-dpl capabilities MODEL` shows it): A on most models, E on the DMX
  600/800. The Prodigy and Prodigy Plus have none, so a job that downloads an
  image there is refused (`image_module_unknown`) until it is set. 0.4.0
  downloaded every image to module A. `RenderOptions.image_module` and
  `RenderResult.image_module` carry it as an `ImageModule` (`"A"` in JSON).
- `[printer] send_chunk_bytes` (default 1024, more than 0, at most 65536): the
  largest piece of a job one write over tcp or serial takes. `timeout` bounds
  each piece, so it means the printer took no data for that long; 0.4.0 wrote
  64 KiB pieces over tcp and the whole job in one write over serial.
- `[printer] verify_poll_interval` (default 0.5 s, more than 0, at most
  2147483 s): seconds between a busy answer and the next status poll while
  `verify` waits for the printer to finish its job; fixed at 0.5 s in 0.4.0.
- `[printer] one_way_settle` (default 5 s, 0 to 2147483 s): seconds `verify`
  waits after sending, instead of polling, on a channel that cannot read
  status (a file, a device file); fixed at 5 s in 0.4.0.
- The seven keys above are read from a `[printers.<name>]` section and their
  environment variables as well. A bad value is refused naming its key,
  `printer.<key>: ...` or `printers.<name>.<key>: ...` (for example
  `printer.upload_check_rounds: Input should be less than or equal to 3`),
  with exit 2, by every command that reads the printer settings. 0.4.0
  silently ignored these keys, so a value it accepted may now be refused.
- Library: `domain.geometry` (`DotPoint`, `Rect`, `Size`, `Span`),
  `domain.frozen_mapping.FrozenMapping`, `domain.status.parse_serial_test`
  (reads the `<STX>k` answer), the enums `domain.enums.StatusFlag`,
  `CounterSign` and `ValidationErrorType`, `domain.profiles.CalibrationKey`
  and `CalibrationDefaults.value(key)`, `BatchResult.joined(results, *,
  batch_findings=(), cut_after=())`, and `adapters.config.sections`
  (`ConfigSection`, `SectionPath`, `printer_section`, `PRINTER_SECTION`,
  `CAMERA_SECTION`).
- Library: the enums `ImageModule` (`A` to `E`) and `PrinterStop` (its one
  member `printer`, which `Label.stop_position` and
  `RenderOptions.stop_position_mm` hold after validation) import from
  `datamax_dpl`; `PrinterProfile.family` (a `ModelFamily`, None for `generic`
  and `permissive`), `PrinterProfile.image_module` and
  `PrinterProfile.ram_modules` (a frozenset of `ImageModule`) are new.
- Library: `PrinterSetting` imports from `datamax_dpl`: the enum of persistent
  printer settings a job can send before its format (`media`, `stop`,
  `start_of_print`, `max_label_length`, `feed_rate`, `software_switches`), as
  `RenderOptions.send_settings` holds them.
- Library: `pillow_available()` imports from `datamax_dpl`: True when the
  `preview` extra (Pillow) is installed.
- Library: `preview_findings(label)` imports from `datamax_dpl`: the findings
  for what the preview PNG of a label cannot show: one
  `preview_text_approximated` (info) when the label has text and Pillow is
  not installed, else none.
- `[preview] max_length_mm` (default 2000.0): `preview` refuses a label whose
  picture would be longer than this either way, exit 2, before drawing it, and
  `verify` before printing it; `render_preview_png` takes `max_length_mm` and
  raises `PreviewTooLongError`, exported from `datamax_dpl`. A height of
  100000 mm took 118 s and 1.47 GB, and 1e9 raised `MemoryError`.
- `UnknownModelError`, `MissingFieldError`, `ConfigurationError` and
  `PrinterTimeoutError` can be imported from `datamax_dpl`.

### Changed

- A number in the `[printer]`, `[printers.<name>]`, `[camera]` or `[preview]`
  section refuses a boolean (`printer.timeout: needs a number, got true`),
  which used to read as 1 or 0: `timeout = true` was a 1 second timeout, and
  `offset_along = false` beat the model's own default. Numeric text stays
  accepted, because `.env` delivers `1.50` as text. `label_width` and
  `label_height` now stop at 100000 too.
- A number or boolean in a label document, or in `RenderOptions`, must now
  have that JSON type: numeric strings (`"x": "10"`), a boolean as a number
  (`"x": true`) and a number as a boolean are refused naming the field, and so
  is a whole float for an integer field (`"quantity": 2.0`). They were read as
  10, 1 and 2, while `datamax-dpl schema` says number. Coded numbers follow:
  `"fill": "1"`, `"attribute": true` and `"datamatrix": {"ecc": false}` are
  refused with `needs a whole number`. An integer for a length
  stays fine. `{name}` placeholders fill text and `data` only, so `--data` is
  not affected.
- A key the `[printer]`, `[printers.<name>]`, `[camera]` or `[preview]`
  section does not declare is now refused naming it (`printer.timout: Extra
  inputs are not permitted`, exit 2), and so is a section that is no table
  (`printer = 5`). Both were dropped silently, so a typo left the setting at
  its default. A named printer's section is checked when that printer is
  chosen.
- `--timeout` on `status` and `memory` is now the whole wait for the first
  answer, retries included, in place of `status_wait`; before, it bounded
  one connect and one answer wait. Without `wait`, `query_status` and
  `read_memory_directory` keep trying for `timeout` seconds in all.
- A printer that refuses every connection, or that cannot be connected to in
  time, now makes `status` and `memory` fail only when their wait runs out:
  a mistyped port, or a printer switched off behind a print server that
  refuses, takes `status_wait` (30 s) to report, where it took about 3 s
  before, and is reported as a `PrinterTimeoutError` instead of a
  `TransportError`. An unknown or unreachable host still fails at once.
- `read_memory_directory` with no kinds returns at once without opening the
  printer.
- Every downloaded image is named `D` plus 7 characters from the 62 symbols
  `0-9 A-Z a-z` (the leading 8 bytes of its sha256 in base 62) in place of
  7 hex digits: two different images share a name about 1 in 62**7
  (3.5e12) per pair instead of 1 in 2**28. Names compare by exact letter
  case; lower case was measured kept and matched exactly on one Allegro 2
  (firmware CA02 03.04) only, unverified on other models. An image a 0.4.0
  job left on a module no clear empties is not recognised by its old name
  and is downloaded once more: it costs module memory, never a wrong
  picture. On a nearly full module that is never cleared that download can
  be lost with no error, and the label then prints without the image; on a
  two-way link the upload check of `print` and `verify` catches that before
  the format goes out.
- `allegro-2` model values measured on the printer: `max_format_rows` is
  1648 (about 201 mm at `scale_along` 0.976), the lowest row any test
  format was cut at; where the printer cuts depends on the format's
  content (a tall element from the bottom was cut at 1648 to 1652, a
  format with nothing tall printed to 1675), so the limit is conservative
  and `max_format_rows` raises it. The tear-off `stop_position` is 66.8 mm,
  sent as `<STX>f263`.
- `format_past_max_length` says the model may cut a format past the row, not
  that it cannot hold it.
- `calibrate print` and `calibrate set` default `--along-length` to the
  longest bar in whole 10 mm, at most 200 mm, that one format holds at the
  printer's current calibration (190 mm on an Allegro 2 at 0.976), so the
  default bar prints and `set` measures against the length printed. Where a
  format limit applies, `set` works the length out from `label_width` or
  `--width` and refuses without either (pass `--along-length`). An
  `--along-length` that does not fit is still refused with the longest that
  fits named. `domain.calibration.default_along_length` and
  `domain.render.format_row_limit` expose the two rules.
- A malformed `--firmware` is refused as a configuration error, `--firmware:
  firmware must look like 2.01, got '2'`, with exit 2 as before; 0.4.0 said
  only `firmware must look like 2.01, got '2'`, a `ValueError` in the JSON
  envelope. A malformed `[printer] firmware` is refused as `printer.firmware:
  ...` (`printers.<name>.firmware: ...` for a named printer) by every command
  that reads the printer settings, `status` and `feed` included, even when a
  valid `--firmware` is given; 0.4.0 refused it only where a label was
  rendered without `--firmware`. `--firmware ""` means not given, so a
  configured firmware applies; 0.4.0 read it as unknown firmware.
- Library: `RenderOptions.firmware` is the parsed `domain.profiles.Firmware`
  (`major`, `minor`) or None, default None, in place of a string defaulting to
  `""`. Text is still accepted: `"2.01"` becomes `Firmware(major=2, minor=1)`,
  and `""` and None become None (0.4.0 refused None). A malformed version is
  refused with a `ValidationError` when the `RenderOptions` is built, instead
  of a `ValueError` from `render`. `RenderOptions.model_dump(mode="json")`
  gives the firmware as `[2, 1]`, not `"2.01"`.
  `domain.profiles.ParsedFirmware` is the field's type.
- With a configuration that cannot be loaded and a bad `--option`, the
  `--option` refusal is reported, where 0.4.0 reported the load failure; both
  exit 2.
- Library, results that no longer unpack as tuples: `calibration_labels`
  returns `CalibrationLabels` (`across`, `along`), exported from
  `datamax_dpl`, in place of a pair of labels;
  `domain.encode_barcode.bar_widths` returns `BarWidths` (`wide`, `narrow`),
  `domain.preview.widest_cell_mm` returns `CellMm` (`advance`, `height`) and
  `domain.encode_graphics.qr_symbol_position` returns
  `domain.records.RecordPosition` (`row`, `column`).
  `domain.records.polygon_record` takes `RecordPosition` points, not tuples.
  `PrinterProfile.default_dot_size` is a `DotSize` (`width`, `height`), which
  no longer compares equal to a tuple such as `(2, 2)`.
- Library, named tuples that still unpack and compare like the old tuples:
  `domain.profiles.Firmware` (`major`, `minor`); `domain.raster.Bitmap.bounds()`
  returns a `domain.geometry.Rect` and `Bitmap.draw_polygon` takes
  `DotPoint`s; `domain.preview.Canvas.point` returns a `DotPoint` and
  `FieldArea.part(along=, up=)` takes `Span`s; `domain.preview.Rect` now lives
  in `domain.geometry` and still imports from `domain.preview`;
  `domain.render_context.PlacedImage.size` is a `Size`.
  `PlacedImage.dots` and `domain.records.image_record(magnification=)` use
  `domain.records.Magnification` (`across`, `along`; `UNMAGNIFIED` is 1x1).
  `PrinterStatus.flags()` returns `domain.status.FlagState` (`flag`,
  `is_set`), whose `flag` is a `StatusFlag` equal to its old name.
- Library: read-only mappings in place of dicts. `PrinterProfile.features` is
  a `FrozenMapping` (assigning raises `TypeError`, and `dataclasses.asdict`
  keeps it a `FrozenMapping`); `domain.enums.SPEED_IPS`,
  `domain.symbology.SYMBOLOGY_CODES` and `SYMBOLOGY_BY_IDENTIFIER` and
  `adapters.camera.file.MEDIA_TYPE_BY_SUFFIX` are read-only.
- Library: `domain.memory.MemoryModule` holds the module letter in `letter`
  (the constructor takes `letter=`, and still `module=`), and
  `MemoryModule.module` is a read-only property, an `ImageModule` or None for
  a letter outside A to E, where it was the letter as a string. The JSON key
  stays `module`.
- Library: `domain.enums.MemoryKind` is a `str` enum (`font`, `graphic`,
  `format`) in place of the `Literal` alias in `domain.memory`, which still
  imports it. `MemoryDirectory.kind`, `parse_module_directory(kind=)`,
  `read_memory_directory(kinds=)` and the keys of
  `domain.status.MEMORY_DIRECTORY_REQUESTS` hold its members; each compares
  equal to its old string, and the JSON is unchanged.
- Library: `domain.image_records.download_command(name, bitmap, *, module)`
  takes the `ImageModule` to download to as a required keyword; 0.4.0 always
  wrote module A. `domain.image_budget.ImageBudget(limit, model=None,
  provenance="")` names the model whose measured budget it is, None for a
  configured one, in place of the required `source` text; `describe()` reads
  as before.
- Library: enums in place of strings for `domain.timing.check_timeout(name=)`
  (`TimeoutName`) and `adapters.transport.uri.transport_error(action=)`
  (`ChannelAction`). The calibration values that
  `application.ports.SetPrinterValues` and
  `adapters.config.user_edit.write_printer_values` and
  `set_user_printer_values` take are keyed by `CalibrationKey`.
- Library: `adapters.camera.capture_with_http`, `capture_with_command` and
  `capture_from_directory` return `adapters.camera.stored.StoredImage`
  (`path`, `media_type`), not a `Path`; `adapters.camera.http` has
  `MEDIA_TYPE_BY_CONTENT_TYPE` and `EXTENSION_BY_MEDIA_TYPE` in place of
  `EXTENSION_BY_CONTENT_TYPE`.
- Library: named records in place of tuples in the adapters:
  `adapters.config.overrides.nest_overrides` returns `NestedOverrides`
  (`tree`, `keys`); `adapters.cli.config_load.load_config` returns, and
  `start_logging(services, loaded, *, env_file)` takes and returns, a
  `ConfigLoad` (`config`, `error`); `adapters.cli.context.TracebackState` is a
  frozen dataclass (`enabled`, `force_color`);
  `adapters.memory.RecordingPrinter.opened` holds `PrinterOpen` (`uri`,
  `timeout`). The CLI's command helpers take typed values: `PrinterOverrides`
  for `resolve_settings` and `labels.build`, `CommandName` for `run_command`
  and `report_failure`.
- A library caller of `verify_print` with no camera is told
  `CameraSettings.kind is none`, not a config section it may not have; the CLI
  still names `[camera] kind`.
- The module-level tables `FONT_BYTES`, `SYMBOL_SET_CODECS`, `MODEL_PROFILES`
  and `PROFILES` are read-only `Mapping`s: a caller that wrote to one changed
  it for the whole process.

### Removed

- Library: `BatchResult.findings()` (each of `BatchResult.results` carries its
  `findings`); `cut_across_records` from `domain.batch` and
  `application.use_cases` (`domain.batch.batch_cuts` returns `BatchCuts` with
  the records a cut follows, and `BatchResult.joined` joins the jobs);
  `adapters.camera.media_type_for` (`StoredImage.media_type` carries the
  type); `adapters.camera.http.EXTENSION_BY_CONTENT_TYPE`;
  `ImageBudget.source` (`ImageBudget.model`);
  `adapters.cli.commands.printer.ALL_KINDS` (`KindChoice.ALL`).
- Library: `build_job(filled=)`, `build_jobs(filled=)` and
  `build_record(filled=)`; a label from `fill_label` carries it.

### Fixed

- A label holds at most 2048 elements, refused when it is read; `attribute` 2
  on a model without the `A` command no longer holds every pair of overlapping
  fields in memory, and skips the comparison when more printer-drawn fields
  than the model's field limit make the refusal certain. A 1 MB label took a
  minute and ended in `MemoryError`.
- `explain` reports a record whose width or height byte is no multiplier as a
  problem; a byte such as `0xDF` raised `TypeError` out of it.
- A camera that answers with anything but a usable image no longer leaves its
  socket to the garbage collector; `capture_with_http` closes the response on
  every route.
- `check_timeout` (and so `open_printer`) refuses a timeout too large for a
  float with its documented `ValueError`; `10**400` escaped as
  `OverflowError`.
- `calibrate set` no longer writes a value the next load refuses: `--top nan`
  wrote `offset_along = nan` into the user's configuration, after which every
  command failed until the file was edited by hand. A border that is not
  finite is refused, and the section with the new values in it is validated
  before the file is touched.
- A host name with an empty or over-long label (`tcp://a..b:9100`, a
  64-character label, more than 253 characters in all) is refused with
  `ConfigurationError` when the printer is opened, and a camera URL nothing
  can connect to (`http://exa mple/`, `http://a..b/x`) raises `CameraError`,
  exit 2. They escaped as `UnicodeEncodeError` and `http.client.InvalidURL`,
  the second at exit 1.
- Any failure of a label or printer command now exits 2 with an error envelope
  under `--json`/`--json-bare`, and `--traceback` shows where it came from. A
  failure the code did not foresee used to exit 1, which documents a refusal,
  with nothing on stdout to parse.
- A graphic drawn as an image is refused before any bitmap is made for it when
  it could not be sent: wider than one image row (`image_too_wide`, now worded
  `element 3 (polygon) would be 2100 dots wide`, with no image name) or taller
  than a DPL row reaches (`distance_too_large`). A circle of radius 10000 mm
  took 2.2 GB in `validate`, and a slanted line 100000 mm long ran for
  minutes. The check of the format length and the overlap check no longer draw
  a field that large either; it takes part by its rectangle.
- A length in a label is refused naming its field when it is not finite or
  past 100000 in the label's unit (`domain.label.MAX_LENGTH`), so `x=1e308`,
  `"inf"` and `y=2**63` no longer escape from `build_job` and `render` as
  `OverflowError` or a NaN `ValueError`, and `height="inf"` no longer passes
  `validate`. `calibrate print --width` and `--height` and the calibration
  label builders refuse the same sizes before working anything out from them.
- `status` and `verify` skip a whole line that is not a status answer
  before the eight `Y`/`N` flags arrive. Through a print server a status
  request can be answered first by a line the printer still owed an earlier
  memory query (`MODULE: A`, `AVAILABLE BYTES IN MODULE : 00048631`), which
  made `status` fail to parse; junk on the status line itself is still
  refused. Skipped lines count toward the 64 KiB answer bound, so a printer
  sending nothing else fails with `no complete answer within 65536 bytes`.
  `PrinterTransport.query` takes an `accept` test for this.
- A query that read not one byte now adds `the printer may still be printing
  an earlier job; try again or raise the timeout` to its timeout message.
- A barcode value with a character above U+00FF (a euro sign, CJK) is reported
  as `barcode_bad_character`, exit 1; `build_job` and `render` raised
  `UnicodeEncodeError` from inside the encoder and the CLI exited 2 for the
  whole batch. A value the check refused stays out of the job `build_job`
  returns.
- `<SOH>`, `<STX>` and `<ESC>` in barcode data are refused in every symbology
  (`barcode_control_character`, error,
  `BarcodeProblemCode.CONTROL_CHARACTER`). PDF417, DataMatrix, Telepen and
  MaxiCode sent them raw, so a value from a data file could reach the printer
  as a command. Binary data holding these bytes is not supported.
- A `counter` `fill` takes printable ASCII only: a `<CR>` split the counter
  record, `<SOH>` and `<STX>` went out raw, and a euro sign raised
  `UnicodeEncodeError`.
- `config` shows a password in the `[camera] url`
  (`http://USER:PASSWORD@HOST/...`) as `***REDACTED***`, in human and JSON
  output and for `--section camera` and `--section camera.url`; it printed the
  URL as written. The route and the redaction are in `CONFIG.md`.
- A CSV data file with a field over the `csv` limit (131072 characters) or a
  stray quote is refused as `is not valid CSV`, exit 2 with an envelope: the
  first exited 1 with no envelope, and the second merged every following row
  into one field and printed it.
- `explain` on a record cut short, as a truncated job holds, lists it in
  `problems`; a graphic record raised `IndexError`, exit 1 with no envelope.
- Under `--json`/`--json-bare` a usage error (a mistyped option or value, an
  unknown command, a missing argument) answers with an error envelope on
  stdout, `error` naming the click exception (`BadParameter`, `NoSuchOption`,
  ...), and keeps its usage text on stderr and exit 2; stdout was empty.
  `--json` typed after the subcommand does not parse, and is answered the same
  way. `ErrorEnvelope.command` may be null.
- `info`, `config`, `config-deploy` and `config-generate-examples` honour
  `--json` and `--json-bare`: they printed text. They keep their exit codes
  13, 22, 78 and 1, now with an error envelope. `config` ignores `--format`
  there.
- The wait hints of `status`, `memory`, `print` and `verify` name the section
  in effect (`wait longer with [printers.lager] status_wait or --timeout`);
  following the `[printer]` hint changed nothing for a named printer that sets
  its own. A named printer with no `uri` says so (`printer 'lager' has no uri:
  ...`) rather than `no printer given`.
- The sdist carries `tests/fixtures`, `scripts` and the root `conftest.py`:
  its test suite could not run from an unpacked sdist (43 failed, 49 errors).
- `fill_label` then `render` or `build_job` on a label using the `{{ }}`
  escape no longer warns `placeholders_unfilled` about the literal braces: the
  label carries that it was filled (`Label.filled`).
- `print_checked` and `send_checked` refuse a result and `job.renders` holding
  different numbers of labels with `ConfigurationError` before the printer is
  opened; `send_checked` raised a bare `ValueError` only after the labels went
  out.
- `status`, `memory` and `detect` on a one-way channel refuse without opening
  it: `--printer file:PATH` creates no empty file, and a missing folder gets
  the same refusal rather than "cannot open file".
- A reader that goes away (`datamax-dpl schema | head -1`) is a quiet exit
  with code 141 at any output size, with no "SystemExit: 1" line and no
  "Exception ignored" at exit; a command that failed keeps its own exit code.
- `[camera] timeout` and `[camera] settle_seconds` above 2147483 seconds are
  refused naming the key, where they gave an `OverflowError` with no envelope;
  `[printer] timeout` past that bound names its key too.
- `[camera] timeout` bounds the whole `http` capture: a camera dripping its
  image is stopped at the timeout, where it could hold `verify` for as long as
  it kept sending.
- The `file` camera refuses an image over 50 MiB, as the `http` camera does; a
  symlink in the folder is followed and counts by its target.
- A `file:`, `serial:` or `device:` URI holding `%00`, `%0A`, `%0D` or `%09`
  is refused as a control character: `%00` gave "embedded null character" and
  `%0A` created a file with a newline in its name.
- A refused printer URI or value is quoted up to 200 characters with its
  length: a 3000-digit baud rate gave a 6 KB message.
- A label with far more fields than its model takes is refused as soon as the
  count passes the limit, with no full encoding first: 258334 elements cost 8
  s and 381 MB before the refusal. Its message says `at least N fields`, and
  its findings stop at that element.
- The `command` camera drops the program's stdout and keeps only the end of
  its stderr, where it held both in memory; a helper the program left running
  no longer holds a finished capture open until the timeout. On a timeout on
  Windows it ends the whole process tree with `taskkill /T /F`.
- `calibrate set` writes the user configuration through a temporary file and a
  rename, keeping its mode and following a symlink, so a crash mid-write loses
  no other setting; a file it creates is owner-only.

## [0.4.0] - 2026-10-30

### Added

- `qr` element: a QR Code from `data` (with `{name}` placeholders), error
  level `L`/`M`/`Q`/`H` (default `M`), `module_dots` 1-9 printer dots per
  module (default 3) and a `quiet_zone` of 0-10 modules kept free on every
  side (default 4). It is drawn with segno, downloaded as an image of one dot
  per module and placed magnified, so it prints on every model. Refusals
  `qr_data_too_long` (no QR Code version holds the data at that level) and
  `qr_past_print_width` (the area, quiet zone included, ends past the
  printable width). Warning `qr_dot_size_not_one` when the label's
  `dot_size` is not 1x1, which prints the modules larger. `Qr` and
  `QrError` are exported from `datamax_dpl`, and segno (pure Python) is a
  new dependency. Unverified on paper.
- `image` element: `magnification` 1-9 (default 1), printer dots per image
  dot across and along, written as the image record's dot width and height.
  `preview` draws it, and `explain` rebuilds it from a square dot width and
  height (a non-square one stays named in the meaning, `image, magnified
  2x3`; a square one reads `image at magnification 3`). Unverified on paper.
- Image memory budget: the bytes of dot data one job's image downloads may
  hold (rows times bytes per row; an image the label only places costs
  nothing). The `allegro-2` profile ships 44535 (48631 free bytes of module A
  read on one Allegro 2 holding 11 stored graphics, less a 4096-byte margin);
  no other model has one. `[printer] image_memory_bytes` overrides it, and
  `RenderOptions.image_memory_bytes` carries it. Past the budget, `degrade`
  sends every image but a QR Code at half resolution (each 2x2 block of dots
  OR-ed into one, so a one-dot line survives) and places it at double
  magnification, with one `image_halved` warning naming the elements. A
  halved image, a label's own included, is sent under the name of its halved
  content, never under the label's `name`, so that name always holds the full
  bitmap on the printer; an `image` without `bitmap` naming the same image in
  that job is placed as the halved one, at double its own magnification. A
  QR Code is never halved. A job still past the budget after one halving,
  any job past it under `strict`, and an image whose doubled magnification
  would pass 9 are refused with `image_memory_exceeded`. `preview` and
  `verify` draw halved images at their printed size (a halved raster
  fallback can sit one dot off: the preview halves its own nominal drawing,
  the printer the calibrated one). `RenderResult` gains `image_bytes` and
  `halved_elements`. `scripts/image_ladder.py` writes labels of growing
  image size around a budget, for the test on paper. Unverified on paper.
- `memory` command: lists the printer's memory modules with the stored
  names and free bytes of each (`<STX>W`), for `--kind graphic` (default),
  `font`, `format` or `all`, in the usual JSON envelope or as text. The
  answer has no terminator, so it is read until the printer is silent for
  `--idle` seconds (default 1). An answer that is no directory is reported
  with `parsed: false` and its raw text (exit 0), as is a listing outside
  the manual's limits (module letter outside A-E, a module twice, a name
  over 16 characters or not printable ASCII, free bytes missing or not
  eight digits ending their line); no answer at all is exit 2. "No Modules
  Available", which the manual gives as a corrupt or worn-out module, is
  reported as `no_modules_available`. Both colons are read with blanks
  around them, as an Allegro 2 (firmware CA02 03.04) sends the free-bytes
  header. The printer `open_printer` returns gains
  `read_answer(request, timeout=, idle=)` behind it (tcp and serial;
  one-way channels refuse it), bounded at 1 MiB and at
  120 s for the whole answer (its `total` argument); XON/XOFF alone do not
  start it. Read on one Allegro 2 over a print server; the other models are
  unverified.
- Transparent labels on models without the `A` command (Allegro, Allegro 2,
  Prodigy, Prodigy Plus and their 152 variants, `generic`), which print every field XOR. A label
  with `attribute` 2 there has the fields that share printed dots drawn OR
  into one downloaded image per group, placed at the group's lower-left
  corner instead of the fields' own records: lines, boxes, circles,
  polygons, QR Codes and images with a bitmap. One `transparency_emulated`
  warning (error under `strict`) per group names its elements, the image's
  size and its bytes; the image counts against the image memory budget and
  is halved past it, unless it holds a QR Code. A text, barcode or stored
  image that overlaps another field cannot be merged and prints XOR
  (`transparency_not_emulated`, also a deviation). A label without overlaps
  prints exactly as before, with the info `transparency_not_needed`; none
  of these reports `attribute_unsupported`. Each member prints where its own
  record would put it, the printer's offset included. Merging adds image
  bytes, about a second per kilobyte at 9600 baud. `RenderResult` gains
  `emulated_groups` and `sent_as`, which `preview` and `verify` draw from
  (merged groups OR); `render_preview_png` takes `sent_as=`, and `SentAs`
  is exported. Unverified on paper.
- Per-model calibration defaults (`PrinterProfile.calibration`), used where
  neither the options nor the configuration set a value. The `allegro-2` profile carries the values
  measured on one Allegro 2 (firmware CA02 03.04, 2026-10-29): `scale_along`
  0.976, `offset_along` -0.25 mm and a tear-off stop position of 67.4 mm;
  every other model stays nominal (scale 1.0, offset 0.0, the printer's own
  stop position). The info finding `model_defaults_applied` names each model
  default a job used and where it was measured.
- Deviation `model_offset_clamped`: a field that only the model's default
  offset pushes past the label edge is placed on the edge (where the printer
  put it without the correction) instead of being refused; `strict` refuses
  it. An offset the caller set still refuses such a field.
- Configured stop positions: `[printer]` (and `[printers.<name>]`) keys
  `stop_position` (tear-off) and `cutter_stop_position` (a job that cuts),
  in mm, greater than 0 and at most 253.746 mm (the 999/100 in a three-digit
  `<STX>f` takes); a value outside is refused with its key named. They are
  distances found on the printer, so they are sent unscaled, rounded to
  1/100 in. The stop a job sends is the first of: the label's own
  `stop_position`; a `present_distance` the model can only send as a stop
  position; for a job that cuts, `cutter_stop_position` (never the tear-off
  value), otherwise `stop_position`, then the model's default; otherwise
  none. A model with `<STX>Kf` sends a label's `present_distance` as
  `<STX>Kf` beside that stop. On a model without a stop-position command a
  configured or model stop is not sent and the job still prints, under
  `strict` too, with the info `configured_stop_position_unsupported`. The
  README section "Where the label stops" explains the order, what each
  model sends, and what is unverified on hardware; docs/label-spec.md and
  CONFIG.md link to it.

### Changed

- Unset `scale_across`, `scale_along`, `offset_across` and `offset_along` now
  mean the model's default, and the shipped `config.d/50-printer.toml` no
  longer sets them. An `allegro-2` therefore prints with scale 0.976 along,
  offset -0.25 mm along and a 67.4 mm stop position unless configured
  otherwise, and `calibrate print` / `calibrate set` report and start from
  those values. A value you set, even 1.0 or 0.0, still wins. In the library
  the same four `RenderOptions` fields are now `float | None` and default to
  `None` (the model's value), so `render(label, RenderOptions(model="allegro-2"))`
  prints at scale 0.976 along where 0.3.0 printed at 1.0.
- `lib_layered_config` floor raised from 7.4.1 to 7.6.0.
- Migration: a configuration deployed by 0.3.0 (`config-deploy`, usually
  `--target user`) still holds `scale_across = 1.0`, `scale_along = 1.0`,
  `offset_across = 0.0` and `offset_along = 0.0` in its
  `config.d/50-printer.toml`, and those explicit values win over the model's.
  Comment the four keys out, or redeploy with `config-deploy --force` (the
  old file is kept as `.bak`), to get the model defaults. Earlier releases
  did not ship these keys.
- `preview` (and the expected picture of `verify`) combines overlapping
  fields the way they print instead of always showing both black: each
  element is drawn on its own layer and combined in the mode its attribute
  sets (1 XOR, 2 transparent, 3 opaque; a text field's own `attribute`
  first, then the label's). Models without the `An` command always XOR, as
  an Allegro 2 printed a box over an image of the same size as only their
  difference; a label without `attribute` shows XOR, the format default (a
  DMX 600/800 takes its default from the operator panel, which the preview
  cannot know). Inverse (5) has no such mode and shows as XOR. The one-dot
  frame is drawn last, so a field on the label edge does not erase it.
- Images the library draws (raster fallbacks, QR Codes) are named `D` plus
  7 hex digits of a sha256 of the image instead of `DPLR0001`, `DPLR0002`
  per job: the printer keeps images across jobs and answers BELL to a name
  it already holds, so a counter let a later job, or the next record of a
  batch, reuse a name for different bits. An image drawn twice in one job
  is downloaded once. When the name is already taken in the job (another
  drawn image with the same hash, or a user `image` of that name), the hash
  is salted until it is free; a user-named `image` keeps its own name.
  Across separate jobs two different images share a name only on a hash
  collision, about 1 in 2**28.
- A printer that sends no complete answer in time now reports the wait it
  was given, over tcp and serial alike: `no complete answer within 0.5 s
  (0 bytes read)` instead of `no complete answer in time`, so the message
  names the `--timeout` (or `[printer] timeout`) to raise.
- The skill (`skills/datamax-dpl/SKILL.md`) covers QR Codes, image
  magnification and the image memory budget, the `memory` command, the
  model calibration defaults, where the label stops, and transparency on
  models without the `A` command.
- The test suite now runs `print`, `status` and `memory` with the production
  wiring against a file and a printer server on 127.0.0.1, so the hand-over
  of `--printer`, the model's defaults and `--timeout` to the transport is
  tested, not only the commands against an in-memory printer.

### Fixed

- A `present_distance` on a model with `<STX>Kf` (PE42, PE43, the Ovation
  models, `permissive`) had no upper bound: 2540 mm sent `<STX>Kf10000`
  and 3000 mm `<STX>Kf11811`, five digits where the command takes four
  (manual page 53), without a finding. Past 9999/100 inch (about
  2539.75 mm at scale 1; the check is on the rounded hundredths) it is now
  the error `present_distance_too_large`, so `print` refuses the label, as
  `stop_position_too_large` does for `<STX>f`.
- A filled box on a model with polygons (DMX 400 from firmware 2.01, DMX
  430/600/800, Prodigy Max, PE42/PE43, Ovation, Titan) was sent as a fill
  polygon over the whole box plus its outline record. Those models print
  overlapping fields XOR unless the label sets another `attribute`, so the
  outline band printed white. A solid fill is now one solid polygon, which
  covers the outline; a patterned fill is a polygon inside the outline that
  keeps one dot clear of the edges and sides and two at its far corners (a
  polygon's line is drawn on its vertices), so the two records share no dot.
  Sides or edges that meet leave no room for the fill, and only the outline
  is sent. Unverified on paper.
- `preview` turned a rotated text field or barcode by swapping its width and
  height and letting it still run right and up from the reference point, and
  Pillow's lettering was never turned. It now follows the manual (record
  header field `a`, page 94, and the sample label of Figure 7-1): the field
  turns clockwise about its reference point, so 90 runs down, 180 left and
  270 up from it, characters advance in that direction, and the lettering is
  drawn upright and turned with the field. A label without `height` is now
  tall enough for text or a barcode turned to 270. Unverified until a test
  print.
- `preview` drew a horizontal or vertical line with its thickness centred on
  the line and running past both ends. It now draws the line record's
  rectangle, as the printer does: from the lower or left end, growing up (a
  horizontal line) or right (a vertical one) by the thickness. Slanted lines
  keep the centred stroke, as their polygon is centred.
- An image wider than one image row (2040 dots) made `build_job` (and
  `validate`, `render`, `print`) fail with a raw `ValueError`; a patterned
  box 20 in wide on an Allegro 2 rasterised to 4061 dots, for one. It is
  now refused with `image_too_wide` on the element that made it, for given
  images, raster fallbacks and QR Codes alike, and nothing is downloaded
  or placed for it.

## [0.3.0] - 2026-10-29

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
  measure a printer's scale factors with. The across label has a frame 2 mm
  in from every edge (`ACROSS_INSET_MM`; `across_frame_width` gives its
  width, the length to measure) and a bar filling its inside; the along label has a bar up to
  200 mm long along the feed. Both have a tick every 10 mm, print each text
  block in the largest bitmapped font that fits (font 3, then 2, then 1;
  font 3 on a 100 mm label), and no two of their fields share a dot, since printers without field attributes cancel
  overlapping dots.
- `datamax-dpl calibrate print --printer NAME`: renders both calibration
  labels with that printer's model and current scale factors and sends them in
  one job. The label size comes from `label_width` and `label_height` unless
  `--width` and `--height` are given; `--axis across|along|both` picks the
  labels, `--along-length` shortens the along bar. Each label names the printer
  and the factor it was printed with.
- `datamax-dpl calibrate set --printer NAME --across MM --along MM`: the new
  factor is the old one times measured over nominal length (the frame width
  across, the label width less 4 mm; 200 mm or `--along-length` along), rounded to 4 places and refused
  outside 0.5-1.5. It is written into the printer's section of the user
  configuration, in the last file of that layer that holds the section (else
  `config.toml`), keeping comments and key order; without `--printer` the
  `[printer]` section is calibrated. The configuration is then read again,
  and a value that a `.env` file, an environment variable or `--set` still
  overrides is reported on stderr.
- Position offsets: `offset_across` / `offset_along` in `[printer]` or
  `[printers.<name>]` (and `RenderOptions`), millimetres every field is moved,
  -10 to 10, independent of the tear-off position. A field moved past the left
  or bottom label edge is refused with the new finding `offset_past_label_edge`.
  Rasterised graphics move with it. The across calibration label states the
  border to expect (`BORDERS: 2 MM`) and shows the offsets it was printed with;
  `calibrate set --top/--bottom` and `--left/--right` turn measured borders
  into the offset (old + half the difference) and store it. The across label
  now needs 30 mm of height.
- Docs: README "Calibrating a printer", CONFIG.md named printers and the
  `scale_*` / `label_*` keys, a "Measured on hardware" section in
  `docs/dpl-reference.md`, and the skill section "Several printers, and
  calibrating one" (including that overlapping fields XOR on the Allegro 2).

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
  3 seconds before it is reported; no attempt starts after that (Windows
  itself takes about 2 s to report each refusal). Some print servers refuse a new
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
