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
`--traceback`) go BEFORE the command; every other option goes after it. Machine-readable output: `datamax-dpl --json validate label.json`. stdout is then one envelope
`{"ok", "command", "data", "skipped"}`; errors are `{"ok": false, "error", "message"}`.
Exit codes: 0 done, 1 refused (an error finding), 2 could not run (bad JSON, unknown model,
printer unreachable or silent, broken config). A printer that does not answer in time says how
long it was given (`no complete answer within 0.5 s`): raise `--timeout` (seconds) or
`[printer] timeout`.

## The loop - labels cost money, so do every step

1. Write the label JSON (format: `datamax-dpl schema`; example below).
2. `datamax-dpl --json validate label.json --model MODEL [--data rows.csv] [--cutter yes]`
   (with `--data` every record is checked). Read EVERY finding in `data.findings` (`code`,
   `severity`, `message`, `element`, `record`, `fallback`): `error` will not print; `warning` with
   `fallback` says what prints instead; `info` notes an assumption. Fix and repeat.
3. `datamax-dpl preview label.json -o preview.png --model MODEL [--data rows.csv]` and LOOK at
   the PNG. It shows the FIRST record only, so also preview a one-row data file holding the
   longest values. There is no automatic text wrapping or shrinking: long text runs off the edge.
   It draws overlapping fields as they print (see "Overlapping fields"), rotation, and line
   records exactly; text is grey cells, or Pillow's font (not the printer's) with the `preview`
   extra (`uvx --from "datamax-dpl[preview]" datamax-dpl`, Python 3.11+).
4. Print ONE label first: a data file with the header and one row, `datamax-dpl print label.json
   --data one.csv --model MODEL --printer tcp://HOST:9100`. Scan the barcode. Then print the
   remaining rows (leave out the one already printed).
5. With a camera configured (`[camera]` in the config), `datamax-dpl --json verify label.json
   --printer ... --output-dir out` prints, waits, photographs, and returns `photo` and
   `expected` PNG paths: open both and compare.

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
  `"human_readable": false`.
- **y counts from the BOTTOM edge**, x from the left; a field's reference point is its
  lower-left corner (a circle's is its centre). "At the top" means a HIGH `y`.
- `{name}` placeholders take values from `--data` (CSV header names, a JSON array of objects or
  JSON lines; UTF-8, a BOM is fine), one label per record. `{{` and `}}` are literal braces.
- Fonts 0-8 are fixed bitmaps scaled by `width_multiplier`/`height_multiplier`; 3-6 print
  capitals only. Font 9 needs `points`. Umlauts work in fonts 1, 2 and 9; the euro sign in none.
- Element types: `text`, `scalable_text`, `line`, `box`, `circle`, `polygon`, `barcode`, `image`,
  `qr`. An `image` takes `magnification` 1-9 (printer dots per image dot).
- `{"type": "qr", "x": 3, "y": 3, "data": "{url}", "error": "M", "module_dots": 3, "quiet_zone": 4}`
  (defaults shown; `x`/`y` is the quiet zone's lower-left corner; no rotation). No model has a QR
  record, so it is downloaded as an image and prints on every model. Refused: `qr_data_too_long`,
  `qr_past_print_width`; warning `qr_dot_size_not_one` (a `dot_size` other than 1x1).

## Models and fallbacks

- Model ids: `allegro`, `allegro-2`, `prodigy`, `prodigy-152`, `prodigy-plus`, `prodigy-plus-152`,
  `dmx-400`, `dmx-430`, `dmx-600`, `dmx-800`, `prodigy-max-203`, `prodigy-max-300`, `pe42`, `pe43`,
  `ovation`, `ovation-300`, `ovation-2`, `titan-6200`, plus `generic` and `permissive`
  (`prodigy` is the plain Prodigy; the Plus and Max are separate profiles).
  `validate`, `render`, `print` and `verify` all take `--model`, `--firmware`, `--policy`,
  `--option` and `--cutter`; `preview` takes only `--model` and `--data`.
- `datamax-dpl models` lists the profiles; `datamax-dpl capabilities MODEL` shows what one can do,
  including features gated by firmware or an option (pass those to `validate`/`render` as
  `--firmware 2.01` / `--option datamatrix`).
- Unknown model: `datamax-dpl --json detect --printer tcp://HOST:9100` (needs tcp or serial; it
  only reads status and sends no label) names candidates; otherwise use `generic`, the subset every model has (it has no cutting at all).
- `--policy degrade` (default) applies fallbacks and reports them; `--policy strict` refuses
  instead. Some things have no fallback and are refused either way (mirror on old models, a field
  past the print width, an unsupported symbology, a bad check digit).
- Typical fallbacks: circles and polygons on old models become downloaded images; a missing font 9
  size becomes the nearest one; out-of-range speed and heat are clamped.
- Image memory: a job's downloaded images (given bitmaps, raster fallbacks, QR Codes) must fit
  `[printer] image_memory_bytes` (`allegro-2` default 44535, read from one printer's module A
  cartridge: on another one, set it from `memory --kind graphic`; other models have no budget). Past
  it, `degrade` sends every image but a QR Code at half resolution and double magnification
  (`image_halved`; `preview` shows it). Refused (`image_memory_exceeded`): still past it after
  halving, any job past it under `strict`, a halved image at `magnification` 5 or more.
- Image module: downloads go to the model's built-in module (`image_module` in `capabilities`); a Prodigy/Prodigy Plus has none and refuses them (`image_module_unknown`) until `[printer] image_module` (A-E) names the module fitted.
- `datamax-dpl memory --kind all --printer tcp://HOST:9100` lists the names the printer stores
  and the free bytes of each memory module (read-only; tcp or serial). The `allegro-2` budget is
  the 48631 free bytes one printer reported, less a 4096-byte margin; set yours the same way.

## Cutting

- `"cut": {"every": N, "at_end": true}` (`at_end` defaults to true: the last short group is cut
  too). Tell the tool the cutter exists with `--cutter yes` (or `[printer] cutter = "yes"`);
  `--cutter no` refuses any cutting label.
- With `quantity` > N the printer counts copies itself (`:nnnn`). With `--data`, each record is
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
   present distance second (which one the printer keeps is unverified);
3. `[printer] cutter_stop_position` for a label with `cut`, else `[printer] stop_position` (mm, at
   most 253.746), both sent unscaled; unset `stop_position` takes the model default, which only
   allegro-2 has (66.8 mm, `<STX>f263`);
4. otherwise the printer's own setting.

A model with `<STX>Kf` (PE42, PE43, the Ovations, `permissive`) also sends `present_distance` as
`<STX>Kf` beside that stop; which one the printer follows is unverified. Past 9999/100 in it is
refused (`present_distance_too_large`). Models without `<STX>f` (`generic`, `prodigy`,
`prodigy-152`) send neither: a label's own `stop_position` or `present_distance` is a deviation
(`stop_position_unsupported`, `present_distance_unsupported`; an error under `strict`), a
configured stop only the info `configured_stop_position_unsupported` (prints under `strict` too).

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
- `capture --output-dir DIR` takes one photo, to check the `[camera]` settings before `verify`.
  `config-generate-examples --destination DIR` writes example config files listing every key.

## Several printers, and calibrating one

- Name each printer in the config: a `[printers.<name>]` section takes the keys of `[printer]`
  (and inherits the ones it leaves out) plus `label_width` and `label_height` in mm. Then
  `--printer NAME` picks its model, address, size and scale factors; `--printer` also still takes
  a URI. `render` and `validate` take it too.
- Lengths print slightly off nominal, and differently per printer (a scale is printed / nominal
  length). The `allegro-2` model ships values
  calibrated on one Allegro 2 (`scale_along` 0.976, `offset_along` -0.25 mm, tear-off
  `stop_position` 66.8 mm), named in the info finding `model_defaults_applied`; every other model
  starts neutral. A value set in the config or with `--set printer.KEY=VALUE` (`scale_across`,
  `scale_along`, `offset_across`, `offset_along`, `stop_position`) wins, even scale 1.0 or offset
  0.0; `stop_position = "printer"` (in the label too) sends no `<STX>f`: the printer's own stop
  (a label `present_distance` without `<STX>Kf` still goes out as `<STX>f`).
  Calibrate once per printer:
  1. `datamax-dpl calibrate print --printer NAME` prints two labels with the printer's current
     factors: the ACROSS label (a thin frame 2 mm in from every edge, so 96 mm on a 100 mm
     label, with a bar inside it) and the
     ALONG label (a bar running over several labels, the longest whole 10 mm one format holds,
     at most 200 mm: 190 mm on an Allegro 2 at 0.976; measure it on the liner; the label names
     its length). Each title shows the factor it was printed with. Without `label_width`/`label_height` configured, pass
     `--width` and `--height`.
  2. Measure the frame from outside edge to outside edge (not to the last tick), and the bar end
     to end. `calibrate set` takes the frame width as nominal for `--across`. If the print sits
     off centre, also measure the borders between the frame and the label edges (the label says
     `BORDERS: 2 MM`).
  3. `datamax-dpl calibrate set --printer NAME --across MM --along MM` stores
     `scale_across`/`scale_along` (old factor x measured / nominal) in the user config, in the
     file that already holds that printer's section, keeping comments. The along nominal is the
     bar length print defaulted to (worked out from `label_width` or `--width` on a model with a
     format limit); after `print --along-length N` pass the same `--along-length N` to `set`.
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
`datamax-dpl config-deploy --target user` writes an editable user copy with `[printer]` and
`[camera]` sections.

`render` raises `LabelRejectedError` on an error finding; `build_job` returns the result with
`.findings` instead. `render_batch(label, records, options)` does the `--data` case.
`timeout` is in seconds. A printer that refuses, is unreachable or stops answering raises
`TransportError` from `open_printer` or `send` (a timeout is its subclass): catch that.

`list_profiles()` and `get_profile("dmx-600")` return the model profiles (a profile holds what
`capabilities` shows); `fill_label(label, record)` fills the placeholders for one record;
`render_preview_png(label, get_profile(model), sent_as=job.sent_as, options=options)`, with
`job = build_job(label, options)`, returns the preview PNG bytes (without `sent_as` it draws no
halving or merging; without `options` it places fields as a job with the model defaults would,
not as your calibrated job does), and `preview_findings(label)` lists what that picture
approximates (`preview_text_approximated` when Pillow is missing);
`explain(data)` reads DPL bytes back (`.commands`, `.label`, `.problems`). Every name on this page
imports from `datamax_dpl`.

## What it cannot tell you

Everything comes from the 1998 Datamax DPL manual, and only an Allegro 2 has been checked on
paper (text, lines, boxes, images, rotation, four barcode types, lengths). Not yet printed: QR
Codes, magnified and halved images, merged transparent fields, box fills on models with polygons,
and which stop wins when `<STX>f` and `<STX>Kf` are both sent. The preview shows the design at
nominal size; if a printed label differs from it, believe the label.
