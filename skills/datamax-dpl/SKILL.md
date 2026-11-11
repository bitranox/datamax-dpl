---
name: datamax-dpl
description: Use when printing on a Datamax label printer or producing DPL (Datamax Programming Language) - labels with text, lines, boxes, circles, images, QR codes or barcodes (Code 128, EAN, UPC, Code 39, PDF417, DataMatrix, MaxiCode), one label per row of a CSV or JSON file, cutter, stop or present settings, the printer's image memory - for an Allegro, Prodigy, DMX 400/430/600/800, Prodigy Max, PE42/PE43, Ovation or Titan 6200; or when about to hand-write DPL bytes or a raw port 9100 script for one; or when reading an existing .dpl file back as JSON, previewing a label as a PNG, correcting labels that print too long or short (calibration), or asking a Datamax printer for its status, model or stored images.
---

# datamax-dpl

Never hand-write DPL. Describe the label as JSON; `datamax-dpl` checks it against the printer
model, applies or refuses fallbacks, renders the DPL and sends it. Every flag is in
`datamax-dpl <command> --help`; these pages give what `--help` cannot.

## Run it

```bash
uvx datamax-dpl --help   # or the main branch: uvx --from git+https://github.com/bitranox/datamax-dpl datamax-dpl
```

Global options (`--json` / `--json-bare`, `--set SECTION.KEY=VALUE`, `--profile`, `--env-file`,
`--traceback`) go BEFORE the command; every other option goes after it. Machine-readable output:
`datamax-dpl --json validate label.json`. stdout is then one envelope `{"ok", "command", "data",
"skipped"}`; errors are `{"ok": false, "command", "error", "message"}` (`command` null when none was
named). `render` with no `-o` puts the job in `data.dpl` (control bytes as `<STX>`) and
`data.dpl_base64`; `render -o FILE` writes the job to the file, and only when it passed, and still
prints the envelope; `render -o -` writes the raw bytes to stdout and is refused with `--json`
(exit 2).
Exit codes: 0 done, 1 refused (an error finding), 2 could not run (a usage error, bad JSON,
a data record without a field a placeholder names, unknown model, printer unreachable or silent,
broken config), 130 interrupted (Ctrl-C, or Ctrl-Break on Windows; for a command that sends, part
of a job may already have gone), 141 the reader of the output went away. Every other exit code (a
few commands have their own: `config`, which shows the configuration, exits 78 when it does not
load), the waits (`timeout`, `status_wait`, `stored_names_wait`) and the batch image-name rules
are in [exit-codes-timeouts-and-retries.md](references/exit-codes-timeouts-and-retries.md).

## The loop - labels cost money, so do every step

1. Write the label JSON (format: `datamax-dpl schema`; example below).
2. `datamax-dpl --json validate label.json --model MODEL [--data rows.csv] [--cutter yes]` (with
   `--data` every record is checked; `render`, `validate` and `print` refuse a batch of more than
   `[printer] max_batch_records` (100000) records, exit 2, before anything renders; a label or data
   file past `[printer] max_input_bytes` (16 MiB) is refused when read). Read EVERY finding in
   `data.findings` (`code`, `severity`, `message`, `element`, `record`, `fallback`): `error` will
   not print; `warning` with `fallback` says what prints instead; `info` notes an assumption. Fix
   and repeat. Codes are stable; every code with its fallback is in
   <https://github.com/bitranox/datamax-dpl/blob/HEAD/docs/capabilities.md>. A batch keeps the
   first 1000 findings, past those the first finding of each code not yet kept, and always its
   first error: `data.findings_total` and `data.findings_by_code` count all of them, and
   `data.report_note` says what was cut and which key (`[printer] max_reported_findings`) keeps
   more; `[printer] max_reported_downloads` (1000) caps `data.downloads` the same way. A `record`
   counts the data records from 0 (a CSV header is no record), as `data record 7` in a message
   does; only an error reading a JSON Lines file names its line in the file, from 1.
3. `datamax-dpl preview label.json -o preview.png --model MODEL [--data rows.csv]` and LOOK at
   the PNG. It shows the FIRST record only, so also preview a one-row data file holding the
   longest values. There is no automatic text wrapping or shrinking: long text runs off the edge.
   It draws overlapping fields as they print (see "Overlapping fields" in
   [label-json.md](references/label-json.md)), rotation, and line
   records exactly; text is grey cells, or Pillow's font (not the printer's) with the `preview`
   extra (`uvx --from "datamax-dpl[preview]" datamax-dpl`, Python 3.11+). A label longer than
   `[preview] max_length_mm` (2000) is refused.
4. Print ONE label first: a data file with the header and one row, `datamax-dpl print label.json
   --data one.csv --model MODEL --printer tcp://HOST:9100`. Scan the barcode and compare the label
   with the preview yourself: the tool has no camera and never inspects the printed label. Then
   print the remaining rows (leave out the one already printed). `--printer` takes
   `tcp://HOST[:PORT]` (port 9100 when left out),
   `serial:///dev/ttyS0?baud=9600&parity=N&bytesize=8&stopbits=1&rtscts=0&xonxoff=0`
   (`serial://COM3?...` on Windows; every parameter is optional, and these values are the
   defaults), `device:///dev/usb/lp0`,
   `file:PATH` or `file:///abs/path`, or `-` (stdout). Only tcp and serial read answers (`status`,
   `memory`, `detect`, the upload check).

## Label JSON

```json
{
  "unit": "mm", "width": 50, "height": 30,
  "cut": {"every": 5},
  "elements": [
    {"type": "text", "x": 3, "y": 22, "text": "{name}", "font": 9, "points": 10},
    {"type": "barcode", "x": 3, "y": 4, "symbology": "code128", "data": "{sku}", "height": 12, "narrow_dots": 2}
  ]
}
```

- Lengths are in `unit` (mm by default); `narrow_dots`/`wide_dots` are printer dots
  (`capabilities MODEL` gives the dpi); one label dot is one printhead dot, because the
  format sends `D11` unless the label sets `dot_size`. Barcodes print their text line unless
  `"human_readable": false`. Barcode data never takes `<SOH>`, `<STX>`, `<ESC>` or `<CR>`
  (`barcode_control_character`, `barcode_record_terminator`): binary data holding them is not supported.
  Barcode `data` is at most 255 characters, 3000 for PDF417, counting the PDF417 and DataMatrix
  parameter header (8 and 10 characters): 2992 and 245 characters of data there.
- **y counts from the BOTTOM edge**, x from the left; a field's reference point is its
  lower-left corner (a circle's is its centre). "At the top" means a HIGH `y`.
- `{name}` placeholders take values from `--data` (CSV header names, a JSON array of objects or
  JSON lines), one label per record. The file is UTF-8; only a CSV file may start with a BOM (it is
  dropped before the header is read), and a JSON array or JSON lines file that starts with one is
  refused ("UTF-8 byte order mark (BOM) is not supported"), so save it without. Every format is
  read one record at a time, so memory does not grow with the record count: a large JSON array
  needs no conversion. `{{` and `}}` are literal braces.
- Element types: `text`, `scalable_text`, `line`, `box`, `circle`, `polygon`, `barcode`, `image`,
  `qr`. An `image` takes `magnification` 1-9 (printer dots per image dot). A `polygon` takes at
  most `[printer] max_polygon_points` (60) points.
- Every element field (line `x2`/`y2`/`thickness`, box and circle `fill`, `radius`, counters,
  `font_id`, PDF417 options, `symbol_set`, `count_by`, `pause_each_label`, `plain_zero`) is in
  <https://github.com/bitranox/datamax-dpl/blob/HEAD/docs/label-spec.md>; `schema` gives the
  shape but few descriptions.

## Models and fallbacks

- Model ids: `allegro`, `allegro-2`, `prodigy`, `prodigy-152`, `prodigy-plus`, `prodigy-plus-152`,
  `dmx-400`, `dmx-430`, `dmx-600`, `dmx-800`, `prodigy-max-203`, `prodigy-max-300`, `pe42`, `pe43`,
  `ovation`, `ovation-300`, `ovation-2`, `titan-6200`, plus `generic` and `permissive`
  (`prodigy` is the plain Prodigy; the Plus and Max are separate profiles).
  `validate`, `render` and `print` all take `--model`, `--firmware`, `--policy`,
  `--option` and `--cutter`; `preview` takes only `-o` (required), `--model` and `--data`; it has
  no `--printer`, so a `[printers.<name>]` section is not used (the `[printer]` section, scales
  included, is).
- `datamax-dpl models` lists the profiles; `datamax-dpl capabilities MODEL` shows what one can do,
  including features gated by firmware or an option (pass those to `validate`/`render` as
  `--firmware 2.01` / `--option datamatrix`; the minor has two digits, so `3.2` is `3.20`, not
  `3.02`).
- Unknown model: `datamax-dpl --json detect --printer tcp://HOST:9100` (needs tcp or serial; it
  only reads status and sends no label) names candidates; otherwise use `generic`, the subset every model has (it has no cutting at all).
- `--policy degrade` (default) applies fallbacks and reports them; `--policy strict` refuses
  instead. Some things have no fallback and are refused either way (mirror on old models, a field
  past the print width, an unsupported symbology, a bad check digit).
- Typical fallbacks: circles and polygons on old models become downloaded images
  (`circle_rasterized`, `polygon_rasterized`); a missing font 9 size becomes the nearest one
  (`font_size_substituted`); out-of-range speed and heat are clamped (`speed_out_of_range`,
  `heat_out_of_range`).

## Where the detail is

Use the Read tool to load the page for the topic before acting on it: each holds rules this page
leaves out.

| Topic                                                                                                                                                                                                                                                                                                              | Page                                                                                  |
|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------|
| Exit codes per command (`config` 22 and 78, `config-deploy` 13), `timeout`, refused connects, `status_wait`, stored-name reads, `directory_idle`, `batch_image_names_unread` and what to do on a route that reads no printer (`render`, `print` to `file:`), `held_image_severity`, `send_chunk_bytes`             | [exit-codes-timeouts-and-retries.md](references/exit-codes-timeouts-and-retries.md)   |
| Fonts 0-9 and umlauts, QR Codes (`module_dots`, `quiet_zone`, versions, sizes in mm, `micro`), overlapping fields (`attribute`, XOR, `transparency_emulated`)                                                                                                                                                      | [label-json.md](references/label-json.md)                                             |
| The longest format a model prints (`format_past_max_length`), image memory budget (`image_memory_bytes`, `image_halved`), image module and its clears, the upload check on a two-way `print` (`upload_check_rounds`, `image_upload_refused`), printer reset, image names, `memory`                                 | [images-and-memory.md](references/images-and-memory.md)                               |
| `cut` and `cut_every`, the cut after every print (`cut_after_print`), `:nnnn`, cutter on Prodigy, where the label stops or is cut (`stop_position`, `cutter_stop_position`, `joined_stop_position`, `present_distance`), the settings each job sends (`send_settings`, media, `start_of_print_mm`, `<STX>V`, heat) | [cutting-stops-and-printer-state.md](references/cutting-stops-and-printer-state.md)   |
| `explain` a DPL file, `status`/`feed`/`cut`/`pause`/`cancel`/`reset`, `feed --media gap`, a label longer than one format (past about 200 mm on an Allegro 2) printed as segments, `[printers.<name>]`, `calibrate print` / `calibrate set`, `scale_across`/`scale_along`, offsets                                  | [printer-controls-and-calibration.md](references/printer-controls-and-calibration.md) |
| `render_batch`, a large batch one label at a time (`iter_batch`), `check_batch` and spools, `print_checked` and `CheckedJob`, errors to catch, element classes, `render_preview_png`, `explain(data)`                                                                                                              | [library.md](references/library.md)                                                   |

## Library

```python
from datamax_dpl import Label, RenderOptions, Text, open_printer, render

job = render(Label(elements=[Text(x=5, y=5, text="Hello")]), RenderOptions(model="dmx-600"))
with open_printer("tcp://192.0.2.60:9100", timeout=10) as printer:
    printer.send(job.data)
```

Settings live in a layered config: `datamax-dpl config` shows the merged result,
`datamax-dpl config-deploy --target user` writes an editable user copy of
the settings.

Everything else the library offers (batches, the upload-checked send, the error types to catch) is
in [library.md](references/library.md).

## What it cannot tell you

The printer commands come from the 1998 Datamax DPL manual, and only an Allegro 2 has been checked
on paper (text, lines, boxes, images, rotation, four barcode types, lengths, QR Codes). Not yet
printed: halved images, merged transparent fields, box fills on models with polygons,
and which stop wins when `<STX>f` and `<STX>Kf` are both sent. The preview shows the design at
nominal size; if a printed label differs from it, believe the label.
