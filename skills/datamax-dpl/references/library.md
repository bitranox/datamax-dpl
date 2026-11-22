# Library

A page of this skill: `SKILL.md`, one folder up, has the workflow and the index of these pages.

## Beyond the first example

`render` raises `LabelRejectedError` on an error finding; `build_job` returns the result with
`.findings` instead. `render_batch(label, records, options)` does the `--data` case and never raises
on an error finding: it returns a `BatchResult`, so check `.ok`. A record lacking a field a
placeholder names is no finding: `render_batch`, `iter_batch` and `check_batch` raise
`MissingFieldError` for it. `render_batch` holds every label: pass
`max_job_bytes=` to bound its job (past it, `BatchTooLargeError`). For a large batch use
`iter_batch(label, records, options)`, which takes a generator and yields one `RenderResult` at a
time (the job's bytes in `.data`, the verdict in `.ok`: a record in error is yielded too, so check
each before writing it), or `check_batch(label, records, options)`, which returns a `BatchReport`
holding no job and needs records it can read again (a generator raises `ValueError`), unless
`spool=` is given (below): with a spool it reads the records once and takes a generator. Check the
report's `.ok` before anything goes out, then pass it with `CheckedJob(renders=report.renders(),
...)` to `print_checked`, or write `report.record_jobs()`. The report has `records`, `job_bytes`,
`findings` (`ReportedFinding` pairs of `record` and `finding`), `downloads` and their totals;
`limits=ReportLimits(findings=..., downloads=...)` says how much of those it keeps. Without a spool
(below) the send reads the records again, so they must read the same: a record now in error raises
`LabelRejectedError` when its turn comes (a missing field `MissingFieldError`, a broken field limit
pydantic's `ValidationError`), another number of records `ConfigurationError`, each after the
records before it went out; a record that changed and still renders goes out as it now reads. Memory
does not grow with the records. Time does: each record is rendered once to check and once more to
send, unless `check_batch(..., spool=)` gets a `JobSpool` (any object with `add`, `seal` and
`entries`: `help(datamax_dpl.JobSpool)`; the package ships none for library use) to keep each
checked record in, with its values: the send reads it back and never reads the records again
(`print`, `render -o -` and `render -o` to a target that would not stay what it was if replaced - a
device or pipe, a file this user may not write or does not own, one with another hard link or with
an extended attribute a new file would not get alike (an ACL of its own, not just the directory's
default one), one in a directory that takes no new file - keep theirs in a file in the system
temp directory and send or write the job only once every record passed, a file target in place:
per record the spool holds its job's bytes and its values, plus about 0.5 KB; any other
`render -o FILE` writes the job into a new file beside the target while it checks the records,
which replaces the target once all passed, so the target's disk needs the room); a label is rendered
again, from the values kept, only for
what the printer answers. On a two-way link each label with an image costs a directory read, a
reused logo included, about 1 s plus the printer's answer: about 17 minutes per 1000 labels; with no
clear before the downloads, a label that downloads a new image costs a second one (about 35 minutes
per 1000 in all). Pass `check_batch(..., read_names_at_send=True)` for a batch `print_checked` sends
to a two-way printer: without it each record after the first that downloads images with no clear
before them, and no clear after the label before that reaches the module, gets the error finding
`batch_image_names_unread`, so the report is not `.ok` and nothing is sent.
`[printer] max_batch_job_bytes` (64 MiB) applies only to `render --data` with no
`-o`. `send(data)` writes the job as it is: it does not check what the printer stored, so an image
download a full module lost gives a label with that image missing, and no error. To print the way
the `print` command does, call `print_label(label, PrintRequest(options=options,
endpoint=PrinterEndpoint(uri=uri, timeout=10, open_printer=open_printer),
check=UploadCheck(wait=AnswerWait(seconds=30)), one_way=printer_uri_is_one_way(uri)))`, with
`records=rows` inside the `PrintRequest` for a batch (it also takes `reset=`, `limits=` and
`spool=`; `print_label` itself takes only the label and the request): it decides `options.model_image_budget` and the name reads for the link, and
a label with an error finding raises `PrintRefusedError` with no printer opened. To send a job you
rendered yourself over a channel that can read answers (tcp, serial), use the step it ends with
(render with `model_image_budget=False` for images past the model's budget at full size, as `print`
does): `print_checked(job, CheckedJob(renders=record_renders(label, None,
options), check=UploadCheck(wait=AnswerWait(seconds=30))), endpoint=PrinterEndpoint(uri=uri,
timeout=10, open_printer=open_printer))` (`PrinterEndpoint` takes the
URI, timeout and opener together): it reads the printer's directory before a label's downloads where
no clear precedes them and after any downloads it sends, and raises `UploadRefusedError` before a
format would print without its image. It also reads the status once first: on a reported reset one
calibration label goes out before the job, and `CheckedJob(reset=None)` asks nothing. Every name in
that call, `UploadRefusedError` and `TransportError` import from `datamax_dpl`; `record_renders`
takes the records as its second argument (`None` for a single label), and `print_checked` returns a
`CheckedPrint` (`help(datamax_dpl.print_checked)`). `UploadRefusedError` is not a `TransportError`:
catch both.

The element types are the classes `Text`, `ScalableText`, `Barcode`, `Qr`, `Line`, `Box`, `Circle`,
`Polygon` and `Image`, with `Cut`, `Counter`, `Pdf417Options` and `DataMatrixOptions` for their
settings. `timeout` is in seconds. A printer that refuses, is unreachable or stops answering raises
`TransportError` from `open_printer` or `send` (a timeout is its subclass `PrinterTimeoutError`):
catch that.
`open_printer(uri, timeout=10, tuning=TransportTuning(...))` takes the `[printer]` keys
`send_chunk_bytes`, `refused_connect_wait`, `refused_connect_retry_interval`,
`link_bytes_per_second` (960) and `link_drain_margin` (0.25) as fields of the same names; with the
defaults a tcp close waits for a serial line behind a print server as the CLI does (see
`exit-codes-timeouts-and-retries.md`), and `TransportTuning(link_bytes_per_second=0)` waits for
none. For `print_label` or `print_checked` pass
`open_printer=functools.partial(open_printer, tuning=...)` in the `PrinterEndpoint`. A bad URI
raises `ConfigurationError`; `DatamaxDplError` is the base of both, and of every other type the
package raises, except pydantic's `ValidationError` and a plain `ValueError` for an argument.

`list_profiles()` and `get_profile("dmx-600")` return the model profiles (a profile holds what
`capabilities` shows); `fill_label(label, record)` fills the placeholders for one record;
`render_preview_png(label, get_profile(model), sent_as=job.sent_as, options=options)`, with
`job = build_job(label, options)`, returns the preview PNG bytes (without `sent_as` it draws no
halving or merging; without `options` it places fields as a job with the model defaults would,
not as your calibrated job does; a label sending more fields than the model takes raises
`PreviewTooLongError`, and `sent_as` counts a merged group once), and `preview_findings(label)`
lists what that picture
approximates (`preview_text_approximated` when Pillow is missing);
`explain(data)` reads DPL bytes back (`.commands`, `.label`, `.problems`). Every name on this page
imports from `datamax_dpl`.

## The types you pass, match and catch

Every enum below also accepts its JSON value (`"right"`, `"strict"`, `90`); a strict type checker
wants the member.

- Label and elements: `Label.unit` is a `Unit` (`MM`, `INCH`, `DOTS`). `print_speed`, `slew_speed`
  and `backup_speed` (on `Label` and `RenderOptions`, plus `RenderOptions.feed_rate`) are a
  `SpeedCode`, the DPL letter as value and the speed in inches per second in the name
  (`SpeedCode.IPS_4_0` is `"G"`). `media` (on both) is a `MediaSensor` (`GAP`, `REFLECTIVE`,
  `CONTINUOUS`); `stop_position` takes a length or `PrinterStop.PRINTER` (the `"printer"` word), as
  do the three `RenderOptions` `*_stop_position_mm`. `attribute` (on `Label`, `Text` and
  `ScalableText`) is an `Attribute` (`XOR` 1, `TRANSPARENT` 2, `OPAQUE` 3, `INVERSE` 5).
  `Text.align` is a `TextAlign` (`LEFT`, `CENTER`, `RIGHT`); `rotation` on `Text`, `ScalableText`
  and `Barcode` a `Rotation` (`DEG_0`, `DEG_90`, `DEG_180`, `DEG_270`); `Barcode.symbology` a
  `Symbology` (values are the JSON names, `Symbology.CODE128` is `"code128"`); `Qr.error` a
  `QrError` (`L`, `M`, `Q`, `H`); `fill` on `Box`, `Circle` and `Polygon` a `FillPattern` (the DPL
  pattern number: `NONE` 0, `SOLID` 1, shades and patterns up to `GRID` 11).
- `RenderOptions`: `policy` is a `FallbackPolicy` (`DEGRADE`, `STRICT`); `options` a set of
  `HardwareOption` (`DATAMATRIX`, the CLI's `--option datamatrix`); `image_module` an `ImageModule`
  (`A`-`E`); `send_settings` a set of `PrinterSetting` (`MEDIA`, `STOP`, `START_OF_PRINT`,
  `MAX_LABEL_LENGTH`, `FEED_RATE`, `SOFTWARE_SWITCHES`, as the `[printer] send_settings` key);
  `held_image_severity` a `Severity`.
- Results: a `Finding`'s `code` is a `FindingCode` whose values are the codes `capabilities.md`
  lists (`FindingCode.IMAGE_HALVED` is `"image_halved"`; match with
  `finding.code is FindingCode.IMAGE_HALVED`), its `severity` a `Severity` (`INFO`, `WARNING`,
  `ERROR`). `RenderResult.dpl_unit` and `BatchReport.dpl_unit` are a `DplUnit` (`INCH_HUNDREDTHS`,
  value `"inch"`, or `MM_TENTHS`, `"metric"`). Each of `explain(data).commands` is a
  `DecodedCommand` (`kind`, `raw`, `meaning`), `kind` a `CommandKind` (`IMMEDIATE`, `SYSTEM`,
  `DOWNLOAD`, `FORMAT`, `RECORD`, `COUNTER`, `END`, `UNKNOWN`).
- Profiles: `get_profile(model)` raises `UnknownModelError` (a `DatamaxDplError` and a
  `LookupError`) for an id with no profile, as `render`, `build_job` and the batch functions do for
  such a `RenderOptions.model`. A profile's `features` maps every `Feature` (22, such as
  `Feature.PDF417` or `Feature.CIRCLE_POLYGON`) to a `Support` (`available`, `min_firmware`,
  `option` a `HardwareOption`, `source` a `Source`: `MANUAL` or `MEASURED`; `available` says the
  model has it at all, `min_firmware` and `option` what it further needs, as `capabilities` shows);
  `family` is a `ModelFamily`, `image_module` and `ram_modules` hold `ImageModule`s, and
  `calibration` holds the shipped scales, offsets and stops: `.value(CalibrationKey.SCALE_ALONG)`
  reads one, and `CalibrationKey.scale(axis)` / `CalibrationKey.offset(axis)` pick the key for an
  `Axis` (`ACROSS`, `ALONG`).
- Calibration labels: `calibration_labels(width=, height=, title=, along_length=None, options=None)`
  (`options` a `RenderOptions`) returns the two labels `calibrate print` prints as `.across` and
  `.along`, plain `Label`s in mm to render or print (`along_length` is the along bar in mm, at most
  200; `None` gives the longest whole 10 mm one format holds for `options`, 200 without them);
  `across_label(...)` and `along_label(...)` build one each, and `across_frame_width(width)` is the
  frame width to measure (the width less `ACROSS_INSET_MM`, 2 mm, on each side: 96 mm on a 100 mm
  label). A size out of range or a title that does not fit raises `ValueError`.
- Errors: `TransportError` catches every channel failure; its subclasses tell them apart:
  `PrinterTimeoutError` (no answer, or not finished, within the timeout), `AnswerNotEndedError` (a
  `PrinterTimeoutError`: an answer was still arriving when the wait ended, so asking again would
  start a second answer behind it), `PrinterProtocolError` (an answer the DPL manual does not
  describe for that request) and `UnsupportedOperationError` (the channel cannot do it, such as
  `query` on a `file:`, stdout or device printer).
- `pillow_available()` says whether the `preview` extra is installed: without it
  `render_preview_png` draws text as grey cells and `preview_findings` reports
  `preview_text_approximated`. `get_config(profile=None, start_dir=None, dotenv_path=None)` returns
  the merged layered configuration the CLI reads (lib_layered_config's `Config`, with `.as_dict()`
  and `.get(key, default=...)`); nothing else in the library reads it: `RenderOptions` and
  `open_printer` take only what you pass.
