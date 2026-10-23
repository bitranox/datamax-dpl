---
name: datamax-dpl
description: Use when printing on a Datamax label printer or producing DPL (Datamax Programming Language) - labels with text, lines, boxes, circles or barcodes (Code 128, EAN, UPC, Code 39, PDF417, DataMatrix, MaxiCode), one label per row of a CSV or JSON file, cutter or present settings - for an Allegro, Prodigy, DMX 400/430/600/800, Prodigy Max, PE42/PE43, Ovation or Titan 6200; or when about to hand-write DPL bytes or a raw port 9100 script for one.
---

# datamax-dpl

Never hand-write DPL. Describe the label as JSON; `datamax-dpl` checks it against the printer
model, applies or refuses fallbacks, renders the DPL and sends it. Every flag is in
`datamax-dpl <command> --help`; this page gives what `--help` cannot.

## Run it

```bash
uvx --from git+https://github.com/bitranox/datamax-dpl datamax-dpl --help   # once on PyPI: uvx datamax-dpl
```

Only `--json` / `--json-bare` (and `--set SECTION.KEY=VALUE`) go BEFORE the command; every
other option goes after it. Machine-readable output: `datamax-dpl --json validate label.json`. stdout is then one envelope
`{"ok", "command", "data", "skipped"}`; errors are `{"ok": false, "error", "message"}`.
Exit codes: 0 done, 1 refused (an error finding), 2 could not run (bad JSON, unknown model,
printer unreachable, broken config).

## The loop - labels cost money, so do every step

1. Write the label JSON (format: `datamax-dpl schema`; example below).
2. `datamax-dpl --json validate label.json --model MODEL [--data rows.csv] [--cutter yes]`
   (with `--data` every record is checked). Read EVERY finding in `data.findings` (`code`,
   `severity`, `message`, `element`, `record`, `fallback`): `error` will not print; `warning` with
   `fallback` says what prints instead; `info` notes an assumption. Fix and repeat.
3. `datamax-dpl preview label.json -o preview.png --model MODEL [--data rows.csv]` and LOOK at
   the PNG. It shows the FIRST record only, so also preview a one-row data file holding the
   longest values. There is no automatic text wrapping or shrinking: long text runs off the edge.
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
  (`capabilities MODEL` gives the dpi). Barcodes print their text line unless
  `"human_readable": false`.
- **y counts from the BOTTOM edge**, x from the left; a field's reference point is its
  lower-left corner (a circle's is its centre). "At the top" means a HIGH `y`.
- `{name}` placeholders take values from `--data` (CSV header names, a JSON array of objects or
  JSON lines; UTF-8, a BOM is fine), one label per record. `{{` and `}}` are literal braces.
- Fonts 0-8 are fixed bitmaps scaled by `width_multiplier`/`height_multiplier`; 3-6 print
  capitals only. Font 9 needs `points`. Umlauts work in fonts 1, 2 and 9; the euro sign in none.
- Element types: `text`, `scalable_text`, `line`, `box`, `circle`, `polygon`, `barcode`, `image`.

## Models and fallbacks

- Model ids: `allegro`, `allegro-2`, `prodigy`, `prodigy-152`, `prodigy-plus`, `prodigy-plus-152`,
  `dmx-400`, `dmx-430`, `dmx-600`, `dmx-800`, `prodigy-max-203`, `prodigy-max-300`, `pe42`, `pe43`,
  `ovation`, `ovation-300`, `ovation-2`, `titan-6200`, plus `generic` and `permissive`
  (`prodigy` is the plain Prodigy; the Plus and Max are separate profiles).
  `validate`, `render`, `print` and `verify` all take `--model`, `--firmware`, `--policy`,
  `--option` and `--cutter`; `preview` takes only `--model` and `--data`.
- `datamax-dpl models` lists the profiles; `datamax-dpl capabilities MODEL` shows what one can do,
  including features gated by firmware (`--firmware 2.01`) or an option (`--option datamatrix`).
- Unknown model: `datamax-dpl --json detect --printer tcp://HOST:9100` (needs tcp or serial; it
  only reads status and sends no label) names candidates; otherwise use `generic`, the subset every model has (it has no cutting at all).
- `--policy degrade` (default) applies fallbacks and reports them; `--policy strict` refuses
  instead. Some things have no fallback and are refused either way (mirror on old models, a field
  past the print width, an unsupported symbology, a bad check digit).
- Typical fallbacks: circles and polygons on old models become downloaded images; a missing font 9
  size becomes the nearest one; out-of-range speed and heat are clamped.

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

## What it cannot tell you

Everything comes from the 1998 Datamax DPL manual; nothing has been checked on a real printer
yet. No QR codes. If a printed label differs from the preview, believe the label.
