---
name: datamax-dpl
description: Use when printing on a Datamax label printer or producing DPL (Datamax Programming Language) - labels with text, lines, boxes, circles, images, QR codes or barcodes (Code 128, EAN, UPC, Code 39, PDF417, DataMatrix, MaxiCode), one label per row of a CSV or JSON file, cutter, stop or present settings, the printer's image memory - for an Allegro, Prodigy, DMX 400/430/600/800, Prodigy Max, PE42/PE43, Ovation or Titan 6200; or when about to hand-write DPL bytes or a raw port 9100 script for one.
---

# datamax-dpl

Never hand-write DPL. Describe the label as JSON; `datamax-dpl` checks it against the printer
model, applies or refuses fallbacks, renders the DPL and sends it. Every flag is in
`datamax-dpl <command> --help`; this page gives what `--help` cannot.

## Run it

```bash
uvx datamax-dpl --help   # or the main branch: uvx --from git+https://github.com/bitranox/datamax-dpl datamax-dpl
```

Global options (`--json` / `--json-bare`, `--set SECTION.KEY=VALUE`, `--profile`, `--env-file`,
`--traceback`) go BEFORE the command; every other option goes after it. Machine-readable output:
`datamax-dpl --json validate label.json`. stdout is then one envelope `{"ok", "command", "data",
"skipped"}`; errors are `{"ok": false, "command", "error", "message"}` (`command` null when none was
named). Exit codes: 0 done, 1 refused (an error finding), 2 could not run (a usage error, bad JSON,
unknown model, printer unreachable or silent, broken config), 130 interrupted (Ctrl-C, or Ctrl-Break
on Windows; for a command that sends, part of a job may already have gone), 141 the reader of the
output went away. `status`
also exits 1 when the printer reports a problem, `explain` when it could not read part of the job,
and `detect` when no model fits; `config` also uses 22 (no such `--section`) and 78 (the config does
not load), `config-deploy` 13 (no permission), 78 (its permission settings refused) and 1 (any other
failure), and `config-generate-examples` 1. A printer that does not answer in time says how long it
was given (`no complete answer within 0.5 s`): raise `--timeout` (seconds) or `[printer] timeout`. A
tcp connect the printer or print server refuses (one does for a moment after closing the last job's
connection) is tried again, each time a connection is opened, every
`[printer] refused_connect_retry_interval` (0.25 s) for up to `refused_connect_wait` (3 s), never
past `timeout`. `status` and
`memory` instead keep trying for `[printer] status_wait` (30 s) while a print server is busy with an
earlier job and then say how long they waited (`no answer in 30.0 s (4 attempts)`): raise
`status_wait`, or pass `--timeout`, which replaces it. Before each label whose downloads no
clear precedes, and (unless `upload_check_rounds` is 0) after any downloads a label sends, `print`
may spend up to
`[printer] stored_names_wait` (30 s), plus `[printer] directory_idle` (1 s), the silence that ends
the answer, reading the printer's stored image names; `--timeout` does not change that wait.
`memory` ends each listing on `directory_idle` too (`--idle` overrides it); `status_wait` or
`--timeout` bounds the wait for an answer to begin, and once one has begun it may take at most
`[printer] directory_read_limit` (120 s) to end. Nothing about them is kept from one
label to the next: the printer may have been reset, or another program may have printed. So a
`--data` batch whose labels download images with no clear before them is refused from its second
record on (`batch_image_names_unread`) where no printer can be read before each label: `render`,
`validate` with no tcp or serial printer named, and `print` to a `file:`, stdout or a device -
unless `[printer] clear_graphics_after` (on by default) clears that module after each label, which
removes every name the label before downloaded; it reaches the model's built-in RAM module, and on
the plain Prodigy any module, but never another plug-in module. Then no record is refused for its
names: after a whole-module clear (`<STX>q`, the model's built-in RAM) only the first record is
warned, since the module is empty for the rest; the plain Prodigy, which removes only its own
names, warns on every record (`image_names_unread`), as names another program stored survive. Turn
`[printer] clear_graphics_before` on (it clears only the model's built-in RAM module, so leave
`image_module` unset or name that module; the plain Prodigy, which has none, removes each name it
downloads instead, in the module `image_module` names), or send to a two-way printer, the only way
for the Prodigy Plus and `generic`, which have no clear. A single label on a route that reads no
printer, and a batch's first record, only says its names were not read (`image_names_unread`, a
warning unless `[printer] held_image_severity` raises it; that key (`info`, `warning` or `error`)
also sets `image_name_held`, a download left out because the printer holds its name, and `error`
refuses either whatever `--policy` says). A job is sent in `[printer] send_chunk_bytes` pieces
(1024) and the timeout bounds each one; a job cut off part way says to power-cycle the printer,
which may be waiting for the rest.

## The loop - labels cost money, so do every step

1. Write the label JSON (format: `datamax-dpl schema`; example below).
2. `datamax-dpl --json validate label.json --model MODEL [--data rows.csv] [--cutter yes]` (with
   `--data` every record is checked; `render`, `validate` and `print` refuse a batch of more than
   `[printer] max_batch_records` (100000) records, exit 2, before anything renders; a label or data
   file past `[printer] max_input_bytes` (16 MiB) is refused when read). Read EVERY
   finding in `data.findings` (`code`, `severity`, `message`, `element`, `record`, `fallback`):
   `error` will not print; `warning` with
   `fallback` says what prints instead; `info` notes an assumption. Fix and repeat. A batch keeps
   the first 1000 findings, past those the first finding of each code not yet kept, and always its
   first error: `data.findings_total` and `data.findings_by_code` count all of them, and
   `data.report_note` says what was cut and which key (`[printer] max_reported_findings`) keeps
   more. A `record` counts the data records from 0 (a CSV header is no record), as `data record 7`
   in a message does; only an error reading a JSON Lines file names its line in the file, from 1.
3. `datamax-dpl preview label.json -o preview.png --model MODEL [--data rows.csv]` and LOOK at
   the PNG. It shows the FIRST record only, so also preview a one-row data file holding the
   longest values. There is no automatic text wrapping or shrinking: long text runs off the edge.
   It draws overlapping fields as they print (see "Overlapping fields"), rotation, and line
   records exactly; text is grey cells, or Pillow's font (not the printer's) with the `preview`
   extra (`uvx --from "datamax-dpl[preview]" datamax-dpl`, Python 3.11+). A label longer than
   `[preview] max_length_mm` (2000) is refused.
4. Print ONE label first: a data file with the header and one row, `datamax-dpl print label.json
   --data one.csv --model MODEL --printer tcp://HOST:9100`. Scan the barcode and compare the label
   with the preview yourself: the tool has no camera and never inspects the printed label. Then
   print the remaining rows (leave out the one already printed).

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
- Fonts 0-8 are fixed bitmaps scaled by `width_multiplier`/`height_multiplier`; 3-6 print
  capitals only. Font 9 needs `points`. Umlauts work in fonts 1, 2 and 9, capital umlauts and
  sharp s also in 3-6; the euro sign in none.
- Element types: `text`, `scalable_text`, `line`, `box`, `circle`, `polygon`, `barcode`, `image`,
  `qr`. An `image` takes `magnification` 1-9 (printer dots per image dot). A `polygon` takes at
  most `[printer] max_polygon_points` (60) points.
- `{"type": "qr", "x": 3, "y": 3, "data": "{url}", "error": "M", "module_dots": 3, "quiet_zone": 4}`
  (defaults shown; `x`/`y` is the quiet zone's lower-left corner; no rotation). No model has a QR
  record, so it is downloaded as an image; the Prodigy and Prodigy Plus refuse it with
  `image_module_unknown` until `image_module` names the module fitted. Refused:
  `qr_data_too_long`, `qr_past_print_width`; warning `qr_dot_size_not_one` (a `dot_size` other than
  1x1). `module_dots` is printer dots per module, `quiet_zone` modules on every side. Versions 1-40
  all work and the smallest that holds the data is used; there is no version key. At 203 dpi (8
  dots per mm) and level `M`, symbol alone: V10 holds 213 bytes, 21.5 mm at 3 dots or 28.5 mm at 4;
  V20 holds 666 bytes, 36.5 mm or 48.5 mm; V40 holds 2331 bytes, 66.5 mm or 88.5 mm. The default
  quiet zone adds 3 mm in total to the width and to the height at 3 dots, 4 mm at 4 dots. Text is
  UTF-8, so `ä` takes 2 bytes and `€` 3. On an Allegro 2 one phone read V13 at 3 dots and codes up
  to V20 at 4 dots fine and struggled with V29 at 3 dots and V40 at 2 dots; that is one phone, not
  a limit, so test denser codes with the real reader. Nothing checks a QR Code against the label
  height: a code too tall for the label gets no finding. `"micro": true` allows a Micro QR where the
  data fits one (else a full code), `"boost_error": true` raises the level where the version has
  room; both default `false`. More sizes:
  <https://github.com/bitranox/datamax-dpl/blob/HEAD/docs/label-spec.md>.

## Models and fallbacks

- Model ids: `allegro`, `allegro-2`, `prodigy`, `prodigy-152`, `prodigy-plus`, `prodigy-plus-152`,
  `dmx-400`, `dmx-430`, `dmx-600`, `dmx-800`, `prodigy-max-203`, `prodigy-max-300`, `pe42`, `pe43`,
  `ovation`, `ovation-300`, `ovation-2`, `titan-6200`, plus `generic` and `permissive`
  (`prodigy` is the plain Prodigy; the Plus and Max are separate profiles).
  `validate`, `render` and `print` all take `--model`, `--firmware`, `--policy`,
  `--option` and `--cutter`; `preview` takes only `--model` and `--data`.
- `datamax-dpl models` lists the profiles; `datamax-dpl capabilities MODEL` shows what one can do,
  including features gated by firmware or an option (pass those to `validate`/`render` as
  `--firmware 2.01` / `--option datamatrix`; the minor has two digits, so `3.2` is `3.20`, not
  `3.02`).
- Unknown model: `datamax-dpl --json detect --printer tcp://HOST:9100` (needs tcp or serial; it
  only reads status and sends no label) names candidates; otherwise use `generic`, the subset every model has (it has no cutting at all).
- `--policy degrade` (default) applies fallbacks and reports them; `--policy strict` refuses
  instead. Some things have no fallback and are refused either way (mirror on old models, a field
  past the print width, an unsupported symbology, a bad check digit).
- Typical fallbacks: circles and polygons on old models become downloaded images; a missing font 9
  size becomes the nearest one; out-of-range speed and heat are clamped.
- Format length: a field reaching past `[printer] max_format_rows` dot rows along the feed, as the
  job commands it (scale and offset included), is refused under both policies
  (`format_past_max_length`); a label `dot_size` of height 2 doubles it. Unset it is the model's
  limit: 1648 on `allegro-2` (the lowest row one printer was seen to cut off), none elsewhere.
- Image memory: a job's downloaded images (given bitmaps, raster fallbacks, QR Codes) must fit
  `[printer] image_memory_bytes` (`allegro-2` default 49088, read from one printer's empty module A
  cartridge: on another one, set it from `memory --kind graphic`; other models have no budget). Past
  it, `degrade` sends every image but a QR Code at half resolution and double magnification
  (`image_halved`; `preview` shows it). Refused (`image_memory_exceeded`): still past it after
  halving, any job past it under `strict`, a halved image at `magnification` 5 or more. On a
  two-way `print` the model's budget is left out (the upload check decides,
  `image_budget_skipped`) unless `upload_check_rounds` is 0; a configured `image_memory_bytes`
  still applies.
- Image module: downloads go to the model's built-in module (`image_module` in `capabilities`); a Prodigy/Prodigy Plus has none and refuses them (`image_module_unknown`) until `[printer] image_module` (A-E) names the module fitted. The module is cleared before each label's downloads and again after the label's format and any cut, on a label that downloads nothing too (`[printer] clear_graphics_before` and `clear_graphics_after`, both `true`; the library's `RenderOptions.clear_graphics_before_label` and `clear_graphics_after_label`); the clear (`<STX>q`) goes only to the model's built-in RAM module, never to a plug-in one, whatever the keys say, and the plain Prodigy removes each name a label downloads instead. `<STX>q` empties the whole module, the images the printer stores and any fonts and formats in it too, so a label that places a stored image (an `image` without `bitmap`) is refused (`stored_image_cleared`) while `clear_graphics_before` is on, and a `--data` batch of it from its second record while only `clear_graphics_after` is: keep a stored image usable with both keys `false`. The plain Prodigy's removes take only the label's own downloads.
- Upload check (`print`, two-way link): a download into a full module is lost silently, so a label's
  downloads go first, the printer's directory (`<STX>W`) is read within `stored_names_wait` (plus
  the `directory_idle` silence that ends the answer), and its format follows only once every
  downloaded name is listed. Else round 2 clears (`q` on built-in RAM, the Prodigy's `R`, never a
  plug-in module) and uploads again (with no clear, only the names the directory did not list; after
  a read with no answer it reads the names again first and uploads none of the same names unless
  that read answers), round 3 uploads halved (`image_upload_retried`). With no clear before a label,
  its names are read right before its downloads and only those not listed go; if that read gets no
  answer the job's first label uploads every image (`image_names_unread`), and a later label skips
  round 1 and uploads nothing until round 2's read answers. Still missing, the label is refused and
  nothing more is sent (`image_upload_refused`), as when it is in error once rendered again for a
  round (`image_upload_render_refused`). `[printer] upload_check_rounds` (0-3, default 3); 0 reads
  nothing back after the downloads and retries nothing, so a lost download goes unseen (one Allegro
  2 then printed the rest of the label with no error); the names read before a label's downloads,
  where no clear precedes them, still runs. An `image_module` of `C` (no directory lists it) is
  refused before anything is sent (`image_module_unlisted`); set the real letter (A on the Allegro
  2) or use a one-way link.
- Printer reset (`print`, two-way link): before the first label the job reads `<SOH>A` once within `reset_check_wait` (3 s; no answer means nothing), after that label's names read where one goes, whose answer can carry the report itself. On `R` or `T` in front of the answer (`reset_reported`) one calibration label goes first, because one Allegro 2 printed the first label after a power cycle about 12 mm shifted: the job's printer settings and size, `calibration_label_text` in the middle (`printer_reset_calibrated`: one extra label came out). `calibrate_after_reset = false` only reports it (`printer_reset_not_calibrated`); `[printer] reset_check = false` skips that read and also ignores a reset report a names read returns; `reset_check_wait`, `calibrate_after_reset` and `calibration_label_text` are `[printer]` keys too. Two limits: a `status` or `memory` run before the job takes the report, so that job's first label can be shifted; a one-way link cannot see a reset.
- Every downloaded image goes under its content name (`D` + 7 characters from `0-9 A-Z a-z`, from
  its hash; letter case counts, measured kept and matched exactly on an Allegro 2 CA02 03.04 only),
  never the `image` element's `name`; only an `image` without `bitmap` that no element of the label
  downloads names a printer image by its `name`. An image a 0.4.0 job left on a never-cleared
  module (`D` + 7 hex) is downloaded once more under its new name; on a nearly full module that
  download can be lost silently, which the upload check catches on a two-way link.
- `datamax-dpl memory --kind all --printer tcp://HOST:9100` lists the names the printer stores
  and the free bytes of each memory module (read-only; tcp or serial). The `allegro-2` budget is
  the 49088 free bytes one printer reported for an empty module A; set yours the same way.

## Cutting

- `"cut": {"every": N, "at_end": true}` (`at_end` defaults to true: the last short group is cut
  too). Tell the tool the cutter exists with `--cutter yes` (or `[printer] cutter = "yes"`);
  `--cutter no` refuses any cutting label.
- Only a label with `cut` is cut: with `--cutter yes` a label without it switches the cutter off for
  its job (`<STX>V0`). `[printer] cut_every = N` gives every label without its own `cut` one; the
  label's own `cut` wins. A label cannot opt out of it: print that job with
  `--set printer.cut_every=null`.
- With `quantity` > N the printer counts copies itself (`:nnnn`); N at or past the quantity cuts once,
  after the last copy (the format asks for the whole quantity, `:0003` for three copies: the manual
  wants less, one Allegro 2 cut once anyway, other models unverified). With `--data`, each record is
  its own format, so the job sends a cut command after every N-th record instead
  (`batch_cut_unverified`: the printer must not also cut each label by itself).
- The Prodigy has neither command. The labels still print, with the warning `cut_by_unsupported`
  (copies of one label) or `batch_cut_unsupported` (a `--data` batch); under `--policy strict`
  that warning is an error and nothing prints. The printer then cuts as its own
  cutter switch (DIP or front panel) is set; the manual's default is a cut after every label. To get groups of N there,
  ask the user: one job per N records, or cut by hand.

## Where the label stops

The stop position (`<STX>f`) comes from the first of these that applies:
1. the label's `stop_position` (corrected by `scale_along`), even in a job that cuts;
2. on a model with `<STX>f` but no `<STX>Kf`: the label's `present_distance`
   (`present_distance_unsupported`); with `stop_position` also set, both go out as `<STX>f`,
   present distance second (which one the printer keeps is unverified). A configured
   `present_distance` is never a stop: it is not sent there at all (below);
3. `[printer] cutter_stop_position` for a label with `cut`, else `[printer] stop_position` (mm, at
   most 253.746), both sent unscaled; unset, each takes the model default, which only allegro-2
   has (tear-off 66.8 mm, `<STX>f263`; cutter 76.8 mm, `<STX>f302`, measured with a cutter);
4. otherwise the printer's own setting.

A model with `<STX>Kf` (PE42, PE43, the Ovations, `permissive`) also sends `present_distance` as
`<STX>Kf` beside that stop; which one the printer follows is unverified. Past 9999/100 in it is
refused (`present_distance_too_large`). Models without `<STX>f` (`generic`, `prodigy`,
`prodigy-152`) send neither: a label's own `stop_position` or `present_distance` is a deviation
(`stop_position_unsupported`, `present_distance_unsupported`; an error under `strict`), a
configured stop only the info `configured_stop_position_unsupported` (prints under `strict` too).

## The printer state each job sets

Another program or the front panel may have changed the printer since the last job, so each label's
job sets the persistent settings the label relies on before its format, besides the image module
clear before and after it (Image module, above). A 50 x 30 mm `allegro-2` label without `cut`, with
`--cutter yes` and no config, sends `<STX>n<STX>c0000<STX>O0220<STX>M1200<STX>SC<STX>V0<STX>f263<STX>qA`, its
format, then `<STX>qA` (the clear of module A): media sensor (`<STX>c`; `<STX>e`/`<STX>r` where the
model picks its sensor), start of print (`<STX>O`), maximum label length (`<STX>M`), feed rate
(`<STX>S`), software switches (`<STX>V`) and stop position (`<STX>f`, above). Each value comes from
the label (`media`, `stop_position`), else the `[printers.<name>]` section, else `[printer]`, else
the model's default from the manual; a library caller sets the same fields in `RenderOptions`.

- `[printer] send_settings` (unset: all) lists which go out, of `media`, `stop`, `start_of_print`,
  `max_label_length`, `feed_rate` and `software_switches`; one left out keeps what the printer
  holds, `[]` sends none, and a label's own media or stop it leaves out is reported
  (`label_setting_not_sent`). A site that keeps the printer's own start of print leaves
  `start_of_print` out of the list.
- `media` (`gap`, `reflective`, `continuous`), `start_of_print_mm` (12.7 to 2539.746, sent as set,
  unscaled), `max_label_length_mm` (127 to 2539.746) and `feed_rate` (`A` to `S`; outside the
  model's slew range its default goes, `feed_rate_out_of_range`), e.g.
  `datamax-dpl --set printer.start_of_print_mm=20 print ...`; a value set in range is sent on every
  model. Where the model knows no default (`generic`, `permissive`, the Prodigy Max's start of
  print, the DMX 400's feed rate) and none is set, nothing goes and `printer_settings_unknown` names
  the key that sets it; a `feed_rate` outside the slew range then sends none either
  (`feed_rate_out_of_range`).
- `<STX>V` overrides every switch until the next power-up, so it goes out only once `cutter` and, on
  a model with a label-present bit, `[printer] label_present` (default `unknown`) are both `yes` or
  `no`; else `software_switches_not_sent` (info), whose message names the one missing. The Allegro
  and Allegro 2 have no such bit, so `--cutter` (or `[printer] cutter`) alone decides there. The
  cutter bit is on only for a label that cuts (a label with `cut` sends `<STX>V1<STX>f302` on the
  Allegro 2); `feed --media` sends it off.
- `heat` (0-30; above the model's maximum it is clamped, `heat_out_of_range`), `print_speed`,
  `slew_speed`, `backup_speed`, `column_offset`, `row_offset` and `present_distance` go out only
  when the label sets them (at the top level of its JSON, beside `quantity`) or the same-named key
  in `[printers.<name>]` or `[printer]` does (lengths in mm whatever the label's unit); the label's
  own value wins and passes the same range checks. Unset, nothing is sent and the printer keeps its
  own value. A configured `present_distance` goes out only on a model with `<STX>Kf`; elsewhere it
  is not sent (`configured_present_distance_unsupported`, info, on every label; prints under
  `strict` too), so it never replaces the label's stop or the cutter stop: set `stop_position` or
  `cutter_stop_position` there.

## Reading a DPL file, and the printer's own controls

- `datamax-dpl --json explain old.dpl` names every command of an existing DPL file and rebuilds
  its label as JSON in `data.label`; `data.problems` lists what it could not read. To see it,
  save `data.label` to a file and `preview` that. A file holding several label formats rebuilds
  no label and exits 1 (`data.commands` and `data.problems` are still filled).
- `status` (paper out, ribbon out, paused, busy; tcp or serial only), `feed` (one label), `cut`
  (cut now; the printer's cutter must be installed and enabled), `pause` (toggles), `cancel`
  (drops the current job and pauses) and `reset` (clears the printer's buffers) each take
  `--printer` (default `[printer] uri`) and `--timeout`. To stop a job printing garbage:
  `cancel`, then `pause` to resume.
- `feed --media gap` (or `reflective`) ends continuous mode (`<STX>c0000`), selects that sensor,
  sends the configured stop position and feeds once: run it after labels printed on continuous
  media to bring the stock back to the tear-off position.
- A label longer than one format (`allegro-2`: 1648 dot rows, about 200 mm) prints as segments:
  each segment its own label (or one label filled from records), `"media": "continuous"`,
  `"continuous_length": 0.25`, all in one `print` job, then `feed --media gap`. Measured on one
  Allegro 2: the printer feeds `continuous_length` AFTER each label's content (it is the blank
  paper between labels, not the label length), and at 0.25 mm the segments sit about 2 mm apart,
  so put the seams where no field crosses them. Separate `print` jobs are unverified.
- `config-deploy --target user` writes the user's config holding every key, each documented;
  `config-generate-examples` only shows where each layer reads from, with a generic sample.

## Several printers, and calibrating one

- Name each printer in the config: a `[printers.<name>]` section takes the keys of `[printer]`
  (and inherits the ones it leaves out) plus `label_width` and `label_height` in mm. Then
  `--printer NAME` picks its model, address, size and scale factors; `--printer` also still takes
  a URI. `render` and `validate` take it too.
- Lengths print slightly off nominal, and differently per printer (a scale is printed / nominal
  length). A printer that rounds 1/100 in to whole dots prints `[printer] inch_unit_dots` dots per
  unit (a whole number above 0; unset: the model's, 2 on the Allegro 2); set it for another
  firmware (`--set printer.inch_unit_dots=N` or the config), and let the scales
  absorb what remains. The `allegro-2` model ships values
  calibrated on one Allegro 2 (`scale_along` 0.976, `offset_along` -0.25 mm, tear-off
  `stop_position` 66.8 mm), named in the info finding `model_defaults_applied`; every other model
  starts neutral. A value set in the config or with `--set printer.KEY=VALUE` (`scale_across`,
  `scale_along`, `offset_across`, `offset_along`, `stop_position`) wins, even scale 1.0 or offset
  0.0; `stop_position = "printer"` (in the label too) sends no `<STX>f`: the printer's own stop
  (a label `present_distance` without `<STX>Kf` still goes out as `<STX>f`).
  Calibrate once per printer:
  1. `datamax-dpl calibrate print --printer NAME` prints two labels with the printer's current
     factors: the ACROSS label (a thin frame 2 mm in from every edge, so 96 mm on a 100 mm
     label, with a bar inside it) and the ALONG label (a bar running over several labels, the
     longest whole 10 mm one format holds, at most 200 mm: 190 mm on an Allegro 2 at 0.976,
     where 200 mm needs row 1653, past its limit of 1648; measure it on the liner; the label
     names its length). Each title shows the factor it was printed with. Without
     `label_width`/`label_height` configured, pass `--width` and `--height`.
  2. Measure the frame from outside edge to outside edge (not to the last tick), and the bar end
     to end. `calibrate set` takes the frame width as nominal for `--across`. If the print sits
     off centre, also measure the borders between the frame and the label edges (the label says
     `BORDERS: 2 MM`).
  3. `datamax-dpl calibrate set --printer NAME --across MM --along MM` stores
     `scale_across`/`scale_along` (old factor x measured / nominal) in the user config, in the
     file that already holds that printer's section, keeping comments. The along nominal is the
     bar length print defaulted to (worked out from `label_width` or `--width` on a model with a
     format limit); after `print --along-length N` pass the same `--along-length N` to `set`.
     `print` names the bar it drew (`along_length_mm`) and, when `set` would measure against
     another length (a `--model` override on print), the `--along-length` to pass to `set`.
     It warns on stderr when a `.env` file, an environment variable or `--set` still overrides
     the value. Borders go in pairs: `--top T --bottom B` stores `offset_along` (old + (T - B) /
     2 mm, positive moves the print up) and `--left L --right R` stores `offset_across` (old +
     (R - L) / 2, positive moves right). The offset moves every field, independent of the tear-off position; a field it
     pushes past the left or bottom label edge is refused (`offset_past_label_edge`).
  4. Print again: both should now measure nominal.
- Fonts and images sized in printer dots are NOT scaled: on a printer whose feed prints short they
  stay the size the dots give. Positions, lines, boxes, barcode heights and shapes drawn as images
  are corrected.

## Overlapping fields

- Overlapping fields combine by the label's `attribute` (1 XOR, 2 transparent, 3 opaque; a text
  field's own `attribute` overrides it; XOR when unset). Models without the `A` command
  (`allegro`, `allegro-2`, `prodigy`, `prodigy-152`, `prodigy-plus`, `prodigy-plus-152`,
  `generic`) always XOR: dots two fields share print WHITE, so a box over an image of the same
  size shows only what differs. `preview` draws whichever applies; `validate` reports an overlap
  only for `"attribute": 2` on a model without `A` (below), so look at the preview.
- There, `"attribute": 2` merges overlapping lines, boxes, circles, polygons, QR Codes and images
  with a `bitmap` into one downloaded image (`transparency_emulated`; it counts against the image
  memory budget); so does a stored image (an `image` without `bitmap`) whose name another element
  downloads, as that bitmap. Text, barcodes and other stored images cannot be merged and still XOR
  (`transparency_not_emulated`). `strict` refuses both.
  Unverified on paper; otherwise keep fields apart.

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

`render` raises `LabelRejectedError` on an error finding; `build_job` returns the result with
`.findings` instead. `render_batch(label, records, options)` does the `--data` case and never raises
on an error finding: it returns a `BatchResult`, so check `.ok`. It holds every label: pass
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
download a full module lost gives a label with that image missing, and no error. On a channel that
can read answers (tcp, serial), `print_checked(job, CheckedJob(renders=record_renders(label, None,
options), check=UploadCheck(wait=AnswerWait(seconds=30))), endpoint=PrinterEndpoint(uri=uri,
timeout=10, open_printer=open_printer))` is the route `print` takes (`PrinterEndpoint` takes the
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
`TransportError` from `open_printer` or `send` (a timeout is its subclass): catch that. A bad URI
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

## What it cannot tell you

The printer commands come from the 1998 Datamax DPL manual, and only an Allegro 2 has been checked
on paper (text, lines, boxes, images, rotation, four barcode types, lengths, QR Codes). Not yet
printed: halved images, merged transparent fields, box fills on models with polygons,
and which stop wins when `<STX>f` and `<STX>Kf` are both sent. The preview shows the design at
nominal size; if a printed label differs from it, believe the label.
