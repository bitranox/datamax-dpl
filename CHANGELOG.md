# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `[printer] reset_check` (default `true`, also in `[printers.<name>]`): `false` makes `print` ask no `<SOH>A` reset
  report before a job and act on none a names read carries, for a print server where the extra exchange costs. The
  first label after a power cycle may then print shifted with nothing said. The library already had it as
  `CheckedJob.reset=None`.
- `[printer] directory_idle` (default `1.0` s): the silence that ends a `<STX>W` directory answer, for `print`'s
  names reads (which no setting reached before) and for `memory`, whose `--idle` now defaults to it and still wins.
  `[printer] directory_read_limit` (default `120.0` s): the longest one `memory` answer may take, so a slow serial line
  or a long listing can be read; one no longer than `directory_idle` (or `memory --idle`) is refused naming both, and
  a `memory` answer that does not end within it names the key to raise. Library: `UploadCheck.idle`, `Link.read(idle=)`, `read_stored_names(idle=)` and
  `read_memory_directory(read_limit=)`.
- `[printer] max_input_bytes` (default 16 MiB, at most 1 GiB): the largest label or data file `render`, `validate`,
  `print`, `preview` and `explain` read, so a CSV past 16 MiB can be printed without splitting it. Raising it raises
  the peak memory by the file's size. The refusal names the key of the section in
  use. `max_batch_records` now takes up to 536870912, more records than a file of the largest `max_input_bytes`
  holds. Library (CLI adapter): `InputBounds`, and `bounds=` on `read_input`, `load_label` and `load_records`.
- `[printer] inch_unit_dots` (also in `[printers.<name>]`, unset by default): the whole dots one 1/100 in prints
  as, for a firmware or a printer that rounds the unit differently from the measured Allegro 2 (2). Unset keeps the
  model's value, so every job is as before. Library: `RenderOptions.inch_unit_dots`.
- Seven label settings as `[printer]` and `[printers.<name>]` keys, for every label that sets none of its own:
  `heat`, `print_speed`, `slew_speed`, `backup_speed`, `column_offset`, `row_offset` and `present_distance` (lengths
  in mm whatever the label's unit). The label's own value wins; unset, as shipped, sends nothing, so the printer
  keeps its own and every job is as before. A set key meets the label value's range checks and findings, which
  name the key. The calibration label after a reset and the `calibrate print` labels take no column or row
  offset. A configured `present_distance` goes out only on a model with `<STX>Kf`: on the others it would be
  sent as the stop position and override the label's own stop and the cutter stop, so it is not sent and the info
  `configured_present_distance_unsupported` names `stop_position` and `cutter_stop_position` instead (a label's own
  `present_distance` still goes out as `<STX>f` there). Library: `RenderOptions.heat`, `print_speed`,
  `slew_speed`, `backup_speed`, `column_offset_mm`, `row_offset_mm`, `present_distance_mm`; `ConfiguredLabelState`
  and `resolved_label_state` in `datamax_dpl.domain.render_context`, and `RenderContext` takes `configured=`.
- `[printer] max_polygon_points` (also in `[printers.<name>]`, default 60, at most 10000): the most points one
  polygon may have. The limit left the label schema, which now takes up to 10000; at the default a 61-point polygon
  is refused on reading exactly as before (exit 2, the same message), now naming the key that allows more. Library:
  `RenderOptions.max_polygon_points`, a job past it gets the error finding `polygon_too_many_points`;
  `MAX_POLYGON_POINTS`, `MAX_POLYGON_POINTS_LIMIT` and `long_polygons` in `datamax_dpl.domain.label`; `explain`
  now rebuilds a DPL polygon of more than 60 points instead of reporting the record as not understood.
- The `qr` element takes `micro` and `boost_error` (both `false` by default, so every label prints as before):
  `micro` allows a Micro QR where the data fits, `boost_error` raises the error level where the version has room.
  The job, the preview and the overlap check draw the same symbol. Library: `Qr.micro`, `Qr.boost_error`,
  `qr_bitmap(micro=, boost_error=)` and `element_symbol(qr)` in `datamax_dpl.domain.qr`.
- `[printer] upload_check_rounds` takes `0`: `print` uploads a label's images and sends its format with no read
  after the upload and no retry, as on a one-way link (`UploadCheck(rounds=0)` in the library;
  `MIN_UPLOAD_CHECK_ROUNDS` is 0). A lost download then goes unseen, and an Allegro 2 prints such a format without
  the image and with no error. The names read before downloads no clear precedes still runs, so a held name is
  still placed rather than downloaded again. As on a one-way link, the model's image memory budget then applies,
  since no round halves an image past it.

### Fixed

- A JSON array `--data` file is read one element at a time, as CSV and JSON lines already were, so the peak memory
  is the file's bytes, one text copy of them and one record. Up to 0.6.0 the array was parsed whole and held: 20000
  two-field records (1.9 MB) peaked at 68 MB resident and 40000 at 81 MB, now 61 MB and 65 MB, against 61 MB and
  62 MB for the same records as CSV. Records, their order and every refusal of a malformed file read word for word
  as before, except for an element nested deeper than Python's own JSON reader, which finds where each element
  ends, goes: about 995 levels on Python 3.10 and 3.11, several thousand on 3.12 and later. The file is then refused
  with `record N is nested deeper than this Python reads`; up to 0.6.0, 3.10 and 3.11 crashed with a traceback on
  a record whose value nested that deep.
- QR Code batches on a model whose format rows are limited (the Allegro family) take about 40 percent less time:
  each record encoded every QR Code twice, once for the job and once for the format length check, and now encodes
  it once (2000 records of a version 4 code: 8.1-8.2 s before, 4.6-5.0 s after, the same bytes). Nothing is kept
  from one record to the next.
- `print --printer NAME` with `calibrate_after_reset` off: `printer_reset_not_calibrated` names
  `[printers.<name>] calibrate_after_reset`, the key of the section in use, rather than always `[printer]`.

## [0.6.0] - 2026-11-03

### Added

- Every label's job sets the persistent printer state it relies on before its format, since another program or the
  front panel may have changed it: `<STX>c` and, where the model can choose its sensor, `<STX>e` or `<STX>r` for the
  media sensor (`<STX>c0000` when the label is not continuous), `<STX>O` start of print, `<STX>M` maximum label
  length, `<STX>S` feed rate and `<STX>V` software switches, beside the stop position (`<STX>f`, `<STX>Kf`) it already
  sent. `[printer]` and `[printers.<name>]` keys set them, under the names and bounds `RenderOptions` gives them:
  `send_settings` (a list of `media`, `stop`, `start_of_print`, `max_label_length`, `feed_rate` and
  `software_switches`; unset sends every one, `[]` none), `media` (`gap`, `reflective` or `continuous`; a label's own
  `media` wins), `start_of_print_mm` (12.7 to 2539.746 mm), `max_label_length_mm` (127 to 2539.746 mm), `feed_rate` (a
  speed letter `A` to `S`) and `label_present` (`yes`, `no` or `unknown`, default `unknown`). Unset, the last four
  take the model's default from the manual. `<STX>V` goes out only when `cutter` and, on a model with that bit,
  `label_present` are `yes` or `no`. New findings: `printer_settings_unknown` (the model has no known value: the
  Prodigy Max's start of print, the DMX 400's feed rate, `generic`, `permissive`; nothing sent), `software_switches_not_sent`, `max_label_length_too_short` (warning,
  error under strict), `feed_rate_out_of_range`, `label_setting_not_sent` and `configured_sensor_select_unsupported`,
  and for the image module clear `graphics_clear_skipped` and `graphics_clear_unsupported`, info otherwise; each that
  points at a setting names the render option (`RenderOptions.<field>`) and on the command line the key of the printer
  section in use. Library: `RenderOptions` gains `send_settings`, `media`, `start_of_print_mm`, `max_label_length_mm`,
  `feed_rate` and `label_present`; `PrinterProfile` gains `start_of_print_mm`, `max_label_length_mm`, `feed_rate` and
  `software_switches` (a `SoftwareSwitchBits`); `Feature` gains `clear_module` (`<STX>q`) and `remove_image`
  (`<STX>R`); `PrinterSetting`, the enum `send_settings` holds, imports from `datamax_dpl`. CONFIG.md ("The printer
  state each job sets") has the detail.
- `[printer] clear_graphics_before` and `clear_graphics_after` (both `true`): clear the image module before each label
  and again after it, on a model that can clear and a label without images included (`<STX>q` to the model's built-in
  RAM, the plain Prodigy's `<STX>R` for each name the label downloads; a plug-in module, which may be flash, is never
  cleared). `false` keeps what the printer stores; a `--data` batch whose label places a stored image (no bitmap)
  needs both `false`, since the clear after a label removes it for the next, and is otherwise refused from its second
  record on (`stored_image_cleared`). `RenderOptions.clear_graphics_before_label` and `clear_graphics_after_label`
  carry them. Upgrading from 0.4.0, which never cleared: `<STX>q` empties the whole module, every image, font and
  format stored there and not only the job's own images (manual p.35), so turn both keys off where the printer keeps
  files another program stored; a label that places an image the printer stores (an `image` with no `bitmap`) is
  refused on the defaults (`stored_image_cleared`) until the clear before is turned off, and a `--data` batch of them
  until both are.
- `[printer] max_format_rows` (unset: the model's limit) and the finding `format_past_max_length` (error under both
  policies): a field reaching past the highest dot row one format may reach, at dot height 1 and as the job commands
  it (scale and offset included), is refused, since the printer may cut the format there; a label `dot_size` of height
  2 doubles it. Only the `allegro-2` has a limit: 1648 rows (about 201 mm at its `scale_along` 0.976), the lowest row
  any test format was cut at. Where the printer cuts depends on the format's content (a tall element from the bottom
  was cut at 1648 to 1652, a format with nothing tall printed to 1675), so the limit is conservative and the key
  raises it. `RenderOptions.max_format_rows` and `PrinterProfile.max_format_rows` carry it.
- `image_past_print_width` (error under both policies): an image with a bitmap, a raster fallback or a merged
  transparency image that ends past the model's printable width, its width times its magnification counted from the
  column it starts at, is refused; 0.4.0 sent it.
- `"printer"` as a stop position: a label's `"stop_position": "printer"` and `[printer] stop_position = "printer"`
  (also per named printer) send no `<STX>f`, so the printer keeps the stop set on it, the model's default included.
  `PrinterStop` holds the value.
- `[printer] refused_connect_wait` (default 3 s) and `refused_connect_retry_interval` (default 0.25 s), each above 0
  and at most 2147483 s, also per named printer: how long one opening of a tcp connection retries a refused connect,
  never longer than `timeout`, and the pause between two tries. Both were fixed at these values. `TransportTuning`
  carries them.
- The package root exports every type an exported signature or field names, so a caller can import what it
  annotates: `Axis`, `CalibrationDefaults`, `CalibrationKey`, `CheckedPrint`, `Clock`, `DecodedCommand`, `Feature`,
  `Firmware`, `ModelFamily`, `OpenPrinter`, `RecordRender`, `Sleep`, `SoftwareSwitchBits`, `Source`, `SpeedRange`,
  `Support` and `UndescribedBit`. `CalibrationKey.scale` and `.offset` take an `Axis` or its value.
- `docs/label-spec.md` lists the QR Code sizes and how they scanned on an Allegro 2.
- A calibration label after a printer reset. Before the first label of a
  two-way `print`, `print --data` or `print_checked`, the job reads `<SOH>A`
  once within `[printer] reset_check_wait` (default 3 s); no answer means
  nothing. On `R` or `T` in front of the answer, with
  `[printer] calibrate_after_reset` (default true), one label goes first: the
  job's printer settings and size, `[printer] calibration_label_text` (default
  `Printer Reset - Calibration Feed`) in the middle (`printer_reset_calibrated`,
  info). With the key off the reset is only reported
  (`printer_reset_not_calibrated`). A `status` or `memory` run before the job
  takes the report, and a one-way link cannot see a reset; CONFIG.md has the
  detail. Library: `ResetCheck`, `CheckedJob.reset` (None asks nothing),
  `RecordRender.calibration`.
- `print_checked(job, checked, endpoint=PrinterEndpoint(uri=..., timeout=...,
  open_printer=...))`, `CheckedJob`, `UploadCheck`, `AnswerWait`, `PrinterEndpoint`,
  `record_renders`, `PrinterTransport`, `TransportTuning`, `HeldImages`,
  `Explanation` and `DplUnit` import from the package root. README and the
  skill say what `send(job.data)` skips and spell the checked route.
- Library: `iter_batch` yields one `RenderResult` at a time, `check_batch` returns a `BatchReport` holding no job
  bytes, and `print_checked` takes the report with `report.renders()`, building each label when its turn comes.
  `render_batch` stays as the way to hold a batch whole. `check_batch(spool=...)` keeps each record in a `JobSpool` as
  it is checked, with the values it was checked with (`BatchEntry.record`), and the report sends from it alone, so its
  records may be a generator; `JobSpool` (the protocol with `add`, `seal` and `entries`), `BatchReport`, `BatchEntry`,
  `ReportLimits` and `ReportedFinding` import from `datamax_dpl`. Without a spool `check_batch` refuses a generator,
  and the send reads the records again: one now in error is refused when its turn comes (`LabelRejectedError`), as is
  another number of records (`ConfigurationError`); one that changed and still renders goes out as it now reads.
  `entries()`, `record_jobs()` and `renders()` of a report refused at its check raise `LabelRejectedError` naming its
  errors before giving any record.
- `[printer] max_reported_findings` and `max_reported_downloads` (default 1000 each): a batch's answer keeps that many
  findings and image names in full, past that the first finding of each code, and always its first error. The JSON
  envelope gains `findings_total`, `findings_by_code`, `downloads_total` and `report_note`, which names the key when
  something was cut; human mode then prints the counts per code. An error still refuses the job whether or not its
  text was kept.
- `[printer] max_batch_job_bytes` (default 64 MiB): `render --data` with no `-o`, the one command that holds the whole
  job, stops with exit 2 and `BatchTooLargeError` naming the key and `-o FILE`, before the memory goes. `render_batch`
  takes `max_job_bytes`, no limit when it is left out: a library caller rendering records it did not write sets one.
- `[printer] max_batch_records` (default 100000): a `--data` batch with more
  records is refused with exit 2 before anything is rendered. A million
  records in a 2 MB file took 96 s and 4.3 GB.
- `DatamaxDplError`, exported from the package root, is the base of every exception the package raises;
  `UnknownModelError`, `MissingFieldError`, `QrDataTooLongError` and `PreviewTooLongError` have `LookupError` or
  `ValueError` as a second base. `AnswerNotEndedError`, `InvalidLoggingConfigError`,
  `PrinterProtocolError`, `UnsupportedOperationError` and `UploadRefusedError` can now be imported from the root as
  well. The README states the contract.
- `[printer] status_wait` (default 30 s, at most 2147483 s) and `status_retry_interval` (0.25 s), also per named
  printer: `status` and `memory` keep trying for the printer's first answer while a print server is still busy with an
  earlier job. A connection that took no request - refused, timed out while connecting, or closed or reset before the
  request was sent or before a byte of answer - is tried again on a new one (a refused connect is first retried within
  the opening, see `refused_connect_wait`); an open but silent connection is read until the wait ends and never asked
  a second time, so no second request is left outstanding. A malformed answer is reported, not asked again. When the
  wait runs out the error gives the seconds actually waited (for `memory` at most 120 s, the cap on one answer),
  counts the attempts (each connect the tcp transport tried within an opening included, `TransportError.attempts`),
  quotes the last failure and names `status_wait` and `--timeout`. `TransportError.unanswered` marks the failures that
  may be retried, and `TransportError.reason` is the message without the transport's advice;
  `application.use_cases.AnswerWait` carries the wait for library callers.
- Upload check on a two-way link: `print` sends each label that downloads an
  image in two parts on one connection, its settings, clear and downloads
  first, then its format, cut and clear after only once the
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
  sent. Each label of a `--data` batch is checked on its own, and nothing
  passes from one label to the next. A job whose images go to module
  `C` is refused on a two-way link before anything is sent
  (`image_module_unlisted`, error): no directory lists the default module by
  that letter. A one-way link (a file, stdout, a device file) and
  `render`/`validate` never check. As the check reads what the printer stored,
  `print` on a two-way link leaves the model's image memory budget out and
  sends images at full size (`image_budget_skipped`, info, says when
  the budget would have halved them); a value set in `image_memory_bytes`
  still applies. The longest a label waits before `image_upload_refused`, with
  W for `stored_names_wait` and R for `reset_check_wait`, is
  3 + R + 2 x `upload_check_rounds` x W, 186 s with the defaults, for the first
  label on built-in RAM or on a plug-in module; 216 s on built-in RAM where the
  names are read before the label's downloads; for a later label of a batch
  180 s (210 s on built-in RAM with no clear before the downloads), so about
  3 minutes for a printer that never answers. A lower `stored_names_wait`
  shortens it (15 s gives 96 s for the first label), at the cost of counting a
  slower answer as none. CONFIG.md breaks these down.
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
  s): how long `print` keeps trying for each read of the printer's stored
  image names, before each label's downloads no clear precedes and in
  every round of the upload check, pausing `status_retry_interval` between two connections (a refused connect
  within one opening is retried by `refused_connect_wait` and
  `refused_connect_retry_interval`). The
  answer's last byte must arrive within it; the 1 s of silence that ends the
  answer comes on top. `--timeout` leaves it.
- `[printer] held_image_severity` (`info`, `warning` or `error`, default
  `warning`). Where no clear precedes a label's downloads (a module that is
  not the model's built-in RAM, a model that can clear nothing), `print` reads
  the printer's stored graphic names right before each such label's
  downloads, on the job's own connection, and leaves out the download of each
  name it holds (`image_name_held`); where the names cannot be read a
  single label downloads every image (`image_names_unread`). Nothing is kept
  from one label of a batch to the next: the printer may have been reset, or
  another program may have printed, in between, so a label whose image an
  earlier one stored places it with `image_name_held` too. The key sets the
  severity of both findings, and `error` refuses such a label whatever
  `policy` says. `RenderOptions.held_image_severity` carries it.
- `batch_image_names_unread` (error): a `--data` batch whose labels download images with no clear before them is
  refused before anything is sent or written where no printer is read before each label: `render`, `validate` with no
  tcp or serial printer named, and `print` to a file, stdout or a device file. The finding goes on the second record
  and each later one; the first, which no label of the batch precedes, gets `image_names_unread` as a single label
  does, so a one-record batch is not refused. `validate --data` with a tcp or serial printer named is not refused. Nor
  is a batch whose clear after each label (`clear_graphics_after`, on by default) reaches the module: it removes every
  name the label before downloaded, so each later record is judged as the first is. Library: `render_batch`,
  `iter_batch` and `check_batch` take `read_names_at_send` (True where a two-way sender reads the names before each
  label). Its message names the ways out: a tcp or serial printer, the clear before each label, or on a plug-in
  module, which takes no clear, leaving `image_module` unset where the model's built-in module takes one; a model that
  can clear neither (`generic`, the Prodigy Plus) is told to use one that can. A message that names a clear key names
  the key of the section in use, `[printers.NAME]` under `--printer NAME`, beside the render option.
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
- Every `[printer]` key this release adds is read from a `[printers.<name>]` section and its environment variables as
  well. A bad value is refused naming its key, `printer.<key>: ...` or `printers.<name>.<key>: ...` (for example
  `printer.upload_check_rounds: Input should be less than or equal to 3`), with exit 2, by every command that reads
  the printer settings. 0.4.0 silently ignored these keys, so a value it accepted may now be refused.
- Library: `domain.geometry` (`DotPoint`, `Rect`, `Size`, `Span`),
  `domain.frozen_mapping.FrozenMapping`, `domain.status.parse_serial_test`
  (reads the `<STX>k` answer), the enums `domain.enums.StatusFlag`,
  `CounterSign` and `ValidationErrorType`, `domain.profiles.CalibrationKey`
  and `CalibrationDefaults.value(key)`, `BatchResult.joined(results, *,
  batch_findings=(), cut_after=())`, and `adapters.config.sections`
  (`ConfigSection`, `SectionPath`, `printer_section`, `PRINTER_SECTION`).
- Library: the enums `ImageModule` (`A` to `E`) and `PrinterStop` (its one
  member `printer`, which `Label.stop_position` and
  `RenderOptions.stop_position_mm` hold after validation) import from
  `datamax_dpl`; `PrinterProfile.family` (a `ModelFamily`, None for `generic`
  and `permissive`), `PrinterProfile.image_module` and
  `PrinterProfile.ram_modules` (a frozenset of `ImageModule`) are new.
- Library: `pillow_available()` imports from `datamax_dpl`: True when the
  `preview` extra (Pillow) is installed.
- Library: `preview_findings(label)` imports from `datamax_dpl`: the findings
  for what the preview PNG of a label cannot show: one
  `preview_text_approximated` (info) when the label has text and Pillow is
  not installed, else none.
- `[preview] max_length_mm` (default 2000.0, above 0, at most 100000): `preview` refuses a label whose picture would
  be longer than this either way, exit 2, before drawing it; `render_preview_png` takes `max_length_mm` and raises
  `PreviewTooLongError`, exported from `datamax_dpl`, and `check_preview_length` raises `ValueError` for a limit that
  is not a finite length. A height of 100000 mm took 118 s and 1.47 GB, and 1e9 raised `MemoryError`.
- `UnknownModelError`, `MissingFieldError`, `ConfigurationError` and
  `PrinterTimeoutError` can be imported from `datamax_dpl`.

### Changed

- Every job is longer: each label's job sends the printer state listed under Added before its format, and on a model
  that can clear its built-in RAM `<STX>q` before and after the label. A job for an Allegro 2 that 0.4.0 started
  `<STX>n<STX>f265<STX>L` now starts `<STX>n<STX>c0000<STX>O0220<STX>M1200<STX>SC<STX>f263<STX>qA<STX>L` and ends
  `E<CR><STX>qA`. Upgrading from 0.4.0: these settings override what the front panel or another program set, so set
  `send_settings` to what the job may send, and the clear empties the whole module (see `clear_graphics_before` under
  Added).
- An image is downloaded under its content name: `D` plus 7 characters from the 62 symbols `0-9 A-Z a-z` (the leading
  8 bytes of a sha256 of its size and bits, in base 62), the same name for the same picture in every job. 0.4.0
  downloaded an `image` with a `bitmap` under its own `name`, and a drawn image (a raster fallback, a QR Code) as `D`
  plus 7 hex digits. Two different images share a name about 1 in 62**7 (3.5e12) per pair instead of 1 in 2**28. Names
  compare by exact letter case; lower case was measured kept and matched exactly on one Allegro 2 (firmware CA02
  03.04) only, unverified on other models. The label schema (`datamax-dpl schema`) says so. Upgrading from 0.4.0: a
  label that downloads `LOGO` no longer stores anything under `LOGO`, so a later label or another program that places
  `LOGO` without a bitmap no longer finds that picture; give such a label the bitmap. A result's `downloads` and
  `datamax-dpl memory` list the content names. An image a 0.4.0 job left on a module no clear empties is not
  recognised by its old name and is downloaded once more: it costs module memory, never a wrong picture. On a nearly
  full module that is never cleared that download can be lost with no error, and the label then prints without the
  image; on a two-way link the upload check of `print` catches that before the format goes out.
- The `allegro-2` model's tear-off `stop_position` is 66.8 mm, sent as `<STX>f263`; 0.4.0 used 67.4 mm (`<STX>f265`).
- `--timeout` on `status` and `memory` is now the whole wait for the printer, retries included, in place of
  `status_wait`; before, it bounded one connect and one answer wait. Without `wait`, `query_status` and
  `read_memory_directory` keep trying for the endpoint's timeout in all.
- A QR Code with text outside ASCII now holds UTF-8. Before, segno chose Latin-1 or Shift JIS X 0213 with no marker
  and the reader guessed: the zxing-cpp decoder read a degree sign as a half-width katakana and a run of `ä` as CJK
  characters. Such text now takes more room, 2 bytes for `ä` and 3 for a CJK character that went in kanji mode at 13
  bits, so the same data can need a larger version or be refused with `qr_data_too_long`. Digits and capitals are not
  affected.
- Dependencies: `lib_layered_config>=7.7.0` (was 7.6.0) and a new direct dependency `pydantic-core>=2.50.1`; the `dev`
  extra pins `certifi>=2026.10.23` (was 2026.10.19).
- `R` (power-on) and `T` (software reset) in front of a status or directory
  answer are no longer a failure: one Allegro 2 sends `R` in front of its first
  answer after a power cycle, and `status` then exited 2 while a directory read
  reported its names as unreadable. `parse_ascii_status`, `parse_status_byte`
  and `parse_module_directory` return the answer with `reset_reported` set;
  `status --json` shows it as a field with exit 0, `memory --json` per
  directory, and the human output names it. The other error characters still
  refuse, and `<SOH>E` keeps refusing a reset report.
- A number in the `[printer]`, `[printers.<name>]` or `[preview]` section
  refuses a boolean (`printer.timeout: needs a number, got true`), which used
  to read as 1 or 0: `timeout = true` was a 1 second timeout, and
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
- A key the `[printer]`, `[printers.<name>]` or `[preview]` section does not declare is now refused naming it, a
  `name` key included (`printer.timout: Extra inputs are not permitted`, exit 2), and so is a section that is no
  table (`printer = 5`). Both were dropped silently, so a typo left the setting at its default. A named printer's
  section is checked when that printer is chosen.
- A printer that refuses every connection, or that cannot be connected to in
  time, now makes `status` and `memory` fail only when their wait runs out:
  a mistyped port, or a printer switched off behind a print server that
  refuses, takes `status_wait` (30 s) to report, where it took about 3 s
  before, and is reported as a `PrinterTimeoutError` instead of a
  `TransportError`. An unknown or unreachable host still fails at once.
- `read_memory_directory` with no kinds returns at once without opening the
  printer.
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
  `domain.symbology.SYMBOLOGY_CODES` and `SYMBOLOGY_BY_IDENTIFIER` are
  read-only.
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
- The module-level tables `FONT_BYTES`, `SYMBOL_SET_CODECS`, `MODEL_PROFILES`
  and `PROFILES` are read-only `Mapping`s: a caller that wrote to one changed
  it for the whole process.
- Library: `send_bytes`, `print_result`, `send_command`, `query_status`, `read_memory_directory` and `detect_printer`
  in `application.use_cases` take the printer as one value,
  `endpoint=PrinterEndpoint(uri=..., timeout=..., open_printer=...)` (exported from `datamax_dpl`), in place of their
  `uri`, `timeout` and `open_printer` keywords.
- Library: `render_preview_png`, `domain.preview.preview_bitmap` and `canvas_for` take the job's `RenderOptions` as
  `options=` (refusing options for another model), so a configured scale or offset reaches the picture;
  `preview_bitmap` takes `masks`. `domain.preview.Canvas` holds the geometry, and `Canvas.dots(value)` became
  `Canvas.length(value, axis=...)`.
- Library: `BatchResult.data` is joined when it is first read, so `print_result` holds one copy of the job. It is no
  longer a model field: build a `BatchResult` from `results`, and its JSON holds the jobs in `results` only.
  `BatchResult.job_bytes` gives the size without joining.
- Library: `adapters.config.deploy_configuration` and the
  `DeployConfiguration` port take the permission values as one
  `permissions=DeployPermissionRequest(...)` (from
  `datamax_dpl.application.ports`) in place of the `set_permissions`,
  `dir_mode`, `file_mode` and `permission_overrides` keywords.

### Fixed

- `explain` names every command a job sends around its format: `<STX>S` (feed rate), `<STX>V` (software switches),
  `<STX>q` (clear memory module), the plain Prodigy's `<STX>R<name>` (remove stored image) and `<STX>RY`/`RN`
  (ribbon saver) read as a bare `system command` letter.
- `explain` exits 1 for a format line it cannot read, as the README says: a line no format command starts
  (`format command not understood`), an empty line in a format, and a record line shorter than its 15-character
  header are problems now; all three passed as an `unknown format command` with exit 0. A job whose lines end in
  CR LF therefore exits 1 too: every line after `<STX>L` then starts with the LF. The recall of a stored format
  (`rNAME`) reads as `recall stored format`; it was an `unknown format command`.
- A serial port that cannot be opened, written or read is named once with its reason (`cannot open serial /dev/ttyS9:
  No such file or directory`): pyserial's own text repeated the path and its errno. On Windows a port that cannot be
  opened still reads as pyserial words it, which it builds from an error it never raises. A user configuration file a
  calibration cannot read, a configuration that cannot be loaded, and an answer that could not be written say the
  reason and the file as given.
- `--json render -o -` is refused at once, as `ConfigurationError` like `print --printer -`, before the label or a
  record is read; it was refused only after the label was rendered, a `--data` batch's every record included, as
  `ValueError`, and a bad record was named in its place.
- An error naming a file shows the path as it was typed: on Windows every backslash in it read doubled
  (`C:\\Users\\...`), as Python's `repr` spells it. The envelope and the `Error:` line now say the reason and the
  path, and a second path after `->`.
- Human output reaches the console in pieces of 64 KiB: `render --data` with no `-o` held one more copy of its
  readable job on Windows, where the stream turns each newline into two characters.
- `explain` lists what is wrong in words (`record cut short`, `setting not understood`), not as a Python class name.
- `docs/label-spec.md` states its limits for filled values per kind, and names the one example `validate` refuses.
- `device:` and `file:` printers keep to `timeout` on POSIX: a stalled device or a FIFO nobody reads hung for ever,
  and so did a device whose write keeps returning 0. Each piece of a job waits under `select`, and a FIFO with no
  reader is refused at once. A write that fails says how much of the job went out. On Windows, and on `-` everywhere,
  no timeout applies to a write.
- `print` and `render -o -` now say why in human mode: errors on a refusal, warnings and errors on success, on stderr
  where the job owns stdout. Before, a refusal printed `REFUSED` with no reason, and `render -o -` nothing.
- Control characters from a label file or a printer no longer reach the terminal raw: C0, DEL and C1 print as
  `\xNN`, line separators and bidi controls as `\uNNNN`, in the error line and every human view (a `memory` name, an
  unknown label key, a reason phrase). The error line also escapes newline and tab, so that a label key or a file
  name cannot forge a line after it; so do the `config` command's own error lines (a `--section` name, a
  problem loading the configuration).
- A refused `render` with no `-o` answers with no job: the envelope's `dpl` and `dpl_base64` are null.
- A record's own failure names its record, counted from 0 as a finding's `record` is: `data record 7 has no field 'v'`,
  and `data record 7` first in a `ValidationError`'s location. A JSON line still goes by its line in the file.
- A printer that keeps sending no longer holds up a request: what is dropped before one stops at 1 MiB.
- `LabelRejectedError` from `print_result` carries the job's error findings; it carried none.
- `preview` refuses more fields than the model's format takes, as `render` does, before it letters any, and letters
  only what lands on the canvas: 399 text fields of 255 characters at 24 times the size took more than 6.7 GB and 281
  s, and now take about a second. `render_preview_png` names the field limit under `Raises`.
- `preview` places each field at the dots the job sends (the DPL units and their rounding, the Allegro 2's 2 dots per
  1/100 in, the scale and offset undone): a `generic` 10 mm field drew at dot 80 where its 39/100 in prints at 79. It
  combines a text field by its glyph pixels, so a line crossing text gaps only under the strokes, as an Allegro 2
  prints it, and draws an image without a bitmap whose name another element downloads as that bitmap.
- Filling a placeholder stops one character past the field's limit (a QR repeating a 3 MiB value took 4.46 GB to be
  refused), and `explain` stops at 100000 commands and reports the rest as not explained (16 MiB of CR took 15 GB).
- A `--data` batch goes one record at a time: the memory of `print`, `validate` and `render -o` no longer grows with
  the records. A 399-field label with 8000 records took 2.9 GB in `print`, and now takes 59 MB. Every record is still
  checked before any byte goes out, so a batch with a bad record is refused as before, and each record is rendered
  once for it: `print`, `render -o -` and an in-place `render -o` keep each checked record, with its values, in a
  spool file in the system's temp directory, readable by this user alone (on Windows from Python 3.12.4) and removed
  however the command ends (SIGTERM, SIGHUP and closing a Windows console window leave it), and send from it, never
  reading the records again. Per record the spool holds the job, the values and about 0.5 KB besides; of the findings
  only those the answer keeps in full, the rest as counts. A spool that fills the disk ends a batch that passed with
  an error naming it; a batch with a bad record is still refused for that record. A label is rendered again only for
  what the printer answers: its names read, round 2's clear, or halved images. 4000 records of a 399-field label took
  34 s in `print` and `render -o -`, and now take 17 s. On a two-way link each label with an image costs a directory
  read after its downloads, a reused logo included, about 1 s plus the printer's answer (about 17 minutes per 1000
  labels); with no clear before the downloads a label with a new image costs one more read before them (about 35
  minutes per 1000). `render -o FILE` writes a file beside the target and replaces the target only when every record
  passed, and `render -o -` checks all of them, then streams. A target that would not stay what it was if replaced (a
  device, a pipe, a directory taking no new file, a file another user owns, with another hard link, or on Linux with
  an extended attribute, such as a POSIX ACL of its own, that a new file beside it does not get with the same value)
  is written in place after the check; a directory's default ACL or SELinux's label, which a new file gets alike, does
  not count.
- A `--json` envelope goes to stdout in 64 KiB pieces, so `render --data` with no `-o` holds about 8 times its job
  while it answers (about 10 times on Windows; 224 MB resident for a 27 MB job on Linux; written whole, the same
  envelope took 357 MB).
- `offset_along` and `offset_across` move a label in dots by millimetres, as in every other unit: `offset_along = 10`
  moved a field of a dots label by 10 dots.
- `model_offset_clamped` and `offset_past_label_edge` give the distance at the label unit's precision, whole dots and
  at most two decimals in mm or in; they printed `1.99803 dots`.
- A serial query that times out with part of a multi-line answer raises `PrinterTimeoutError`, as tcp does; it
  returned the lines that had come, so `detect` over serial took half a memory report as evidence.
- An answer that cannot be written (a full disk behind a redirect) is one line
  on stderr and exit 2, never a traceback. `calibrate` alone and `--json` with
  no command are usage errors with an envelope where one was asked for.
  `config --section ''` is "not found" in every mode. An undecodable
  `--destination` or `--section` no longer prints logging stacks.
- `calibrate set` reads a config file that starts with a byte order mark, and
  a file it cannot read or write is a `ConfigurationError` naming that file.
- Ctrl-C exits 130 with one line on stderr and, under `--json`, an error envelope naming the command; for a command
  that sends (`print`, `calibrate print`, `reset`, `cancel`, `pause`, `feed`, `cut`) the line says part of a
  job may already have been sent. It exited 1, which reads as a refusal with nothing printed. Ctrl-Break on Windows
  does the same; it ended the process at once with no message. An interrupted `--data` batch writes no job, on every
  route, also where a library swallows the Ctrl-C while a record is filled.
- The printer URI `-` keeps standard output to the job: `--json` and
  `--json-bare` refuse it on `print`, `calibrate print` and the single
  printer commands, as `render -o -` does, and the human text goes to
  stderr. A malformed printer URI is a `ConfigurationError` on the command
  line too (it was a plain `ValueError` there). `preview -o -` is refused; it
  wrote a file named `-`.
- A label holds at most 2048 elements, refused when it is read; `attribute` 2
  on a model without the `A` command no longer holds every pair of overlapping
  fields in memory, and skips the comparison when more printer-drawn fields
  than the model's field limit make the refusal certain. A 1 MB label took a
  minute and ended in `MemoryError`.
- `explain` reports a record whose width or height byte is no multiplier as a
  problem; a byte such as `0xDF` raised `TypeError` out of it.
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
  `ConfigurationError` when the printer is opened, exit 2. It escaped as
  `UnicodeEncodeError`.
- Any failure of a label or printer command now exits 2 with an error envelope
  under `--json`/`--json-bare`, and `--traceback` shows where it came from. A
  failure the code did not foresee used to exit 1, which documents a refusal,
  with nothing on stdout to parse.
- A graphic drawn as an image is refused before any bitmap is made for it when
  it could not be sent: wider than one image row (`image_too_wide`, now worded
  `element 3 (polygon) would be 2100 dots wide`, with no image name) or taller
  than a DPL row reaches (`distance_too_large`). A circle of radius 10000 mm
  took 2.2 GB in `validate`, and a slanted line 100000 mm long ran for
  minutes. The overlap check no longer draws a field that large either; it takes
  part by its rectangle.
- A length in a label is refused naming its field when it is not finite or
  past 100000 in the label's unit (`domain.label.MAX_LENGTH`), so `x=1e308`,
  `"inf"` and `y=2**63` no longer escape from `build_job` and `render` as
  `OverflowError` or a NaN `ValueError`, and `height="inf"` no longer passes
  `validate`. `calibrate print --width` and `--height` and the calibration
  label builders refuse the same sizes before working anything out from them.
- `status` skips a whole line that is not a status answer before the eight
  `Y`/`N` flags arrive. Through a print server a status
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
- A named printer with no `uri` says so (`printer 'lager' has no uri: ...`) rather than `no printer given`.
- The sdist carries `tests/fixtures`, `scripts` and the root `conftest.py`:
  its test suite could not run from an unpacked sdist (43 failed, 49 errors).
- `fill_label` then `render` or `build_job` on a label using the `{{ }}`
  escape no longer warns `placeholders_unfilled` about the literal braces: the
  label carries that it was filled (`Label.filled`).
- `status`, `memory` and `detect` on a one-way channel refuse without opening
  it: `--printer file:PATH` creates no empty file, and a missing folder gets
  the same refusal rather than "cannot open file".
- A reader that goes away (`datamax-dpl schema | head -1`) is a quiet exit with code 141 at any output size, `render
  -o -` and `print --printer -` included, with no "SystemExit: 1" line and no "Exception ignored" at exit; a command
  that failed keeps its own exit code.
- `[printer] timeout` above 2147483 seconds is refused naming the key.
- A `file:`, `serial:` or `device:` URI holding `%00`, `%0A`, `%0D` or `%09`
  is refused as a control character: `%00` gave "embedded null character" and
  `%0A` created a file with a newline in its name.
- A refused printer URI or value is quoted up to 200 characters with its
  length: a 3000-digit baud rate gave a 6 KB message.
- A label with far more fields than its model takes is refused as soon as the
  count passes the limit, with no full encoding first: 258334 elements cost 8
  s and 381 MB before the refusal. Its message says `at least N fields`, and
  its findings stop at that element.
- `calibrate set` writes the user configuration through a temporary file and a
  rename, keeping its mode and following a symlink, so a crash mid-write loses
  no other setting; a file it creates is owner-only.

### Removed

- The camera verification, which was never tested with a real camera: the `verify` and `capture` commands, the camera
  adapters (`command`, `http`, `file`) and the `[camera]` settings with their `config.d/60-camera.toml`, which
  `config-deploy` no longer writes.
- `[printer]` and `[printers.<name>]` keys `verify_poll_interval` and `one_way_settle` (and their
  `DATAMAX_DPL___PRINTER__...` variables), which only `verify` read. A configuration that still sets one is refused
  naming the key; delete it.
- A leftover `[camera]` section, a deployed `config.d/60-camera.toml` or a `DATAMAX_DPL___CAMERA__...` variable is
  ignored and can be deleted. `config` no longer masks a password or token inside a leftover `camera.url`; only a key
  named like a secret, such as `camera.password`, stays redacted.
- Library: `datamax_dpl.CameraError` and `datamax_dpl.MediaType` (also `domain.errors.CameraError` and
  `domain.enums.MediaType`); `application.verify` (`verify_print`, `VerifyReport`, `IdleWait`, `check_idle_wait`,
  `wait_idle`, `POLL_SECONDS`, `ONE_WAY_SETTLE_SECONDS`); `domain.camera` (`CameraSettings`, `CameraKind`,
  `CapturedImage`, `OUTPUT_PLACEHOLDER`, `refuse_boolean`); `adapters.camera` (`capture_image`, `require_camera`,
  `capture_with_command`, `capture_with_http`, `capture_from_directory`, `CAPTURE_STAMP_FORMAT`, and in its submodules
  `MEDIA_TYPE_BY_SUFFIX`, `MEDIA_TYPE_BY_CONTENT_TYPE`, `EXTENSION_BY_MEDIA_TYPE`, `MAX_IMAGE_BYTES`, `StoredImage`,
  `DEFAULT_EXTENSION`, `STDERR_TAIL_CHARS`, `MTIME_GRACE_SECONDS`, `POLL_INTERVAL_SECONDS`);
  `adapters.memory.FakeCamera` (and `adapters.memory.camera` with `JPEG_SIGNATURE`, `PNG_SIGNATURE`, `TINY_PNG`);
  `application.ports.CaptureImage`, `AppServices.capture_image` and the `camera` argument of `build_testing`;
  `adapters.cli.settings.camera_settings`; `adapters.config.sections.CAMERA_SECTION` and `ConfigSection.CAMERA`;
  `adapters.config.display.redact_url_secrets`; `adapters.config.overrides.with_data`;
  `domain.timing.TimeoutName.POLL_INTERVAL`; `PrinterSettings.verify_poll_interval` and
  `PrinterSettings.one_way_settle`.
- Library: `BatchResult.findings()` (each of `BatchResult.results` carries its
  `findings`); `cut_across_records` from `domain.batch` and
  `application.use_cases` (`domain.batch.batch_cuts` returns `BatchCuts` with
  the records a cut follows, and `BatchResult.joined` joins the jobs);
  `ImageBudget.source` (`ImageBudget.model`);
  `adapters.cli.commands.printer.ALL_KINDS` (`KindChoice.ALL`).
- Library: `build_job(filled=)`, `build_jobs(filled=)` and
  `build_record(filled=)`; a label from `fill_label` carries it.
- Library: `adapters.transport.tcp.REFUSED_RETRY_SECONDS`; the value is `domain.timing.REFUSED_CONNECT_WAIT_SECONDS`,
  the default of `[printer] refused_connect_wait`.

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
