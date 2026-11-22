# Skill review, 2026-11-22: the public types the skill did not name; the cutter sentence

Type: reference skill. Change: a new section in `references/library.md`, "The types you pass,
match and catch"; the library.md row of the SKILL.md routing table names it; SKILL.md "The loop",
step 4, scopes the cutter sentence to a label without `cut`.

## Change

- library.md names the enums the label and element fields and `RenderOptions` take (`Unit`,
  `SpeedCode`, `MediaSensor`, `PrinterStop`, `Attribute`, `TextAlign`, `Rotation`, `Symbology`,
  `QrError`, `FillPattern`, `FallbackPolicy`, `HardwareOption`, `ImageModule`, `PrinterSetting`,
  `Severity`), the ones results carry (`FindingCode`, `Severity`, `DplUnit`, `CommandKind`), the
  profile types (`UnknownModelError`, `Feature`, `Support`, `Source`, `ModelFamily`,
  `CalibrationKey`, `Axis`), the calibration-label builders, the `TransportError` subclasses
  (`PrinterTimeoutError`, `AnswerNotEndedError`, `PrinterProtocolError`,
  `UnsupportedOperationError`), `pillow_available` and `get_config`.
- SKILL.md step 4: with no `--cutter`, a label WITH `cut` sends the cutter stop `f302`
  (`cutter_assumed`) and `--cutter no` refuses it (`no_cutter`); the old sentence (`f263`, `V0`)
  holds only for a label without `cut`, while the skill's own example label has `cut`.

## Commands executed (worktree package, Python 3.15)

- [x] Each named enum's members and values printed from `datamax_dpl`; every field the text names
      has that enum as its annotation (`model_fields` of Label, Text, ScalableText, Barcode, Qr,
      Box, Circle, Polygon, RenderOptions, Finding, DecodedCommand, RenderResult, BatchReport,
      PrinterProfile, Support).
- [x] Plain JSON values validate into the members (`RenderOptions(policy="strict")` gives
      `FallbackPolicy.STRICT`; `Text(align="right")`, `Barcode(symbology="code128")`,
      `Qr(error="H")`, `stop_position_mm="printer"` likewise).
- [x] `get_profile("nope")` and `render(..., RenderOptions(model="nope"))` raise
      `UnknownModelError`, an instance of `LookupError`.
- [x] `len(Feature)` is 22; `get_profile("allegro").features[Feature.PDF417]` is available with
      `min_firmware` 3.20, `Feature.DATAMATRIX` available with `option` datamatrix.
- [x] `CalibrationKey.scale(Axis.ACROSS)` is `SCALE_ACROSS`; `across_frame_width(100)` 96.0,
      `(60)` 56.0; `calibration_labels(width=100, height=55, title=...)` gives an along label 205
      mm high (bar 200 mm); `across_label(width=20, ...)`, `along_label(length=300, ...)` and a
      title too long raise `ValueError`.
- [x] `open_printer("file:...", timeout=5).query(b"\x01A", timeout=1)` raises
      `UnsupportedOperationError`, a `TransportError`; `AnswerNotEndedError` subclasses
      `PrinterTimeoutError`.
- [x] `get_config()` returns a `Config` with `.as_dict()` and `.get(key, default=...)`; nothing in
      `datamax_dpl.domain` imports it.
- [x] `render` of the SKILL.md example label (`cut` every 5) on allegro-2: no `--cutter` sends
      `<STX>f302` with `cutter_assumed` and `batch_cut_unverified`; `--cutter no` is refused with
      `no_cutter`; `--cutter yes` sends `<STX>V1<STX>f302`. A label without `cut`: no `--cutter`
      `<STX>f263` and no `<STX>V`, `--cutter no` `<STX>V0<STX>f263`, `--cutter yes`
      `<STX>V1<STX>f302`.

## Probe (retrieval, six questions: enums for align and policy under pyright strict, matching
## `image_halved`, timeout vs undescribed answer, PDF417 support, unknown model, calibration labels)

- RED (haiku, pre-edit library.md from `git show HEAD:`): Q1, Q2, Q4, Q5, Q6 answered NONE; Q3
  half (only `TransportError`, no subclass named).
- GREEN round 1 (haiku, first edit): all six answered by quote. Gaps reported, decided:
  - `UnknownModelError` a `DatamaxDplError` only implied: closed ("a `DatamaxDplError` and a
    `LookupError`").
  - meaning of `along_length`: closed (bar length in mm, at most 200, default per `options`).
  - the first paragraph's "a timeout is its subclass" names no class: closed (names
    `PrinterTimeoutError`).
  - member name vs value of `FindingCode`: closed (`FindingCode.IMAGE_HALVED` is
    `"image_halved"`).
  - other constructor fields of `Text`, `RenderOptions`: declined, covered elsewhere (SKILL.md
    "Library" example; `schema`; label-spec.md linked from SKILL.md "Label JSON").
- GREEN round 2 (opus, seven questions incl. "does `render` read my config"; haiku on the touched
  four): all answered by quote. Gaps reported, decided:
  - what `options` is in `calibration_labels`: closed ("`options` a `RenderOptions`").
  - whether `Support.available` already includes firmware and option: closed (checked against the
    `Support` docstring: `available` = the model has it at all).
  - frame width "2 mm on each side" read as ambiguous: closed (96 mm on a 100 mm label).
  - how a 200 mm along bar relates to a 55 mm label: declined, covered in
    printer-controls-and-calibration.md, "Calibrate once per printer", step 1 (the bar runs over
    several labels, measured on the liner).
  - where findings live on a result: declined, covered at library.md top ("`build_job` returns
    the result with `.findings`").
- GREEN round 3 (haiku, touched questions on the final text): all quoted; remaining gaps are the
  firmware comparison (declined, covered in SKILL.md "Models and fallbacks": `--firmware` /
  `capabilities`) and the allegro-2 bar figure (declined, printer-controls-and-calibration.md step
  1: 190 mm on an Allegro 2).
- Tiers: haiku x3 runs, opus x1.
- Width and fence check over the added lines: clean after reflow; no non-ASCII characters.

## Names deliberately not added

`QrDataTooLongError` (every public path turns it into the `qr_data_too_long` finding),
`BarcodeProblemCode` (only on `BarcodeProblem` from `check_barcode`, neither exported),
`StatusProblem` (only on `PrinterStatus`, not exported), `InvalidLoggingConfigError` (raised only
by `init_logging`, not exported), `print_info` (prints the `info` command's block).

Decisions: none - this skill is not mirrored into the central marketplace and keeps no decision ledger.
