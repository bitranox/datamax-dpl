# Reading DPL, the printer's own controls, several printers, calibration

A page of this skill: `SKILL.md`, one folder up, has the workflow and the index of these pages.

## Reading a DPL file, and the printer's own controls

- `datamax-dpl --json explain old.dpl` names every command of an existing DPL file and rebuilds
  its label as JSON in `data.label`; `data.problems` lists what it could not read. To see it,
  save `data.label` to a file and `preview` that. A file holding several label formats rebuilds
  no label and exits 1 (`data.commands` and `data.problems` are still filled).
- `status` (paper out, ribbon out, paused, busy; tcp or serial only), `feed` (one label: `<STX>V0`,
  which turns an installed cutter off, then the form feed `<STX>F`), `cut`
  (cut now; the printer's cutter must be installed and enabled), `pause` (toggles), `cancel`
  (drops the current job and pauses) and `reset` (clears the printer's buffers) each take
  `--printer` (default `[printer] uri`) and `--timeout`. To stop a job printing garbage:
  `cancel`, then `pause` to resume. `feed` takes no `--model` or `--cutter`, and with neither
  configured it sends the form feed alone (see "Cutting" in `cutting-stops-and-printer-state.md`).
- `feed --media gap` (or `reflective`) ends continuous mode (`<STX>c0000`), selects that sensor,
  turns an installed cutter off, sends the stop position a label that is not cut takes
  (`[printer] stop_position`, else the model's tear-off stop, `<STX>f263` on an Allegro 2; never
  the cutter stop; `"printer"` sends none) and feeds once: run it after labels printed on
  continuous media to bring the stock back to the tear-off position. A feed reports only what it
  sends: the plain `feed` names no model defaults, `feed --media` only the model stop position
  (`model_defaults_applied`). On an Allegro 2 with `cutter` set (`yes` or `no`) and no stop
  configured, `feed` sends `<STX>V0<STX>F` and `feed --media gap` sends
  `<STX>n<STX>c0000<STX>e<STX>V0<STX>f263<STX>F`.
- A label longer than one format (`allegro-2`: 1648 dot rows, about 200 mm) prints as segments:
  each segment its own label (or one label filled from records), `"media": "continuous"`,
  `"continuous_length": 0.25`, all in one `print` job, then `feed --media gap`. Measured on one
  Allegro 2: the printer feeds `continuous_length` AFTER each label's content (it is the blank
  paper between labels, not the label length), and at 0.25 mm the segments sit about 2 mm apart,
  so put the seams where no field crosses them. Separate `print` commands left the same gap.
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
  `stop_position` 66.8 mm, `cutter_stop_position` 76.8 mm, `joined_stop_position` 25.4 mm), the
  ones a job uses named in the info finding `model_defaults_applied`; every other model starts
  neutral. A value set in the config or with `--set printer.KEY=VALUE` (`scale_across`,
  `scale_along`, `offset_across`, `offset_along`, `stop_position`, `cutter_stop_position`,
  `joined_stop_position`) wins, even scale 1.0 or offset
  0.0; `stop_position = "printer"` (in the label too) sends no `<STX>f`: the printer's own stop
  (a label `present_distance` without `<STX>Kf` still goes out as `<STX>f`);
  `cutter_stop_position = "printer"` and `joined_stop_position = "printer"` do the same for a job
  that cuts and a `--data` record followed uncut, the model's 76.8 mm and 25.4 mm included (one
  of the two `"printer"` and the other in mm is refused, `stop_positions_mixed`; a joined stop of
  `"printer"` warns, `joined_stop_printer_backfeed`, since an Allegro 2 pulled the stock back).
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

## Checking where font 9 text ends, on paper

For a right-aligned or centred font 9 line that must end on a mark to a fraction of a millimetre,
at a size, character or model whose widths are not measured (label-json.md, Fonts):

- The grid comes first: a field's `x` moves in whole DPL units, and on an Allegro 2 one unit prints
  as 2 dots, 0.25 mm (`inch_unit_dots`, above). A line whose best position falls between two units
  never reads exactly 0; take the closer unit.
- One row per character: repeat the character as often as fits (N times), end the row in a capital
  `M`, and right-align every row (`"align": "right"`, also when the label centres the line: the
  widths are the same, and only a right-aligned row reads them at full size) at one shared `x`,
  N as many as fit between the label's left edge and `x`. Add a row of `M` alone at the same `x`:
  it measures the `M`'s own placement, and every other row is read against it. Only sizes the
  model prints count (an Allegro 2 has 6, 8, 10, 12, 14, 18, 24, 30, 36 and 48 pt; any
  other is replaced, `font_size_substituted`). Widths measured at one size do not carry to another,
  so measure each size the labels use.
- Print the set name, the label number and the point size as text on every label: a stack of cut
  labels cannot be matched to its rows otherwise.
- Read each row with V-lines: five thin vertical `line` elements 0.5 mm apart through the row's
  final `M`, the middle one where its two diagonals meet, at `x` minus about 0.123 mm per point
  (3.7 mm at 30 pt, 1.5 mm at 12 pt: half the `M`'s ink), each as
  `{"type": "line", "x": X, "y": Y1, "x2": X, "y2": Y2, "thickness": 0.25}` spanning the row's
  height; a V outside the outer lines reads past 2, so move the lines and print again. Fields XOR
  where they overlap (the default with no `attribute`, and always on a model without the `A`
  command), so each line prints white across the ink and shows where it meets the V. The reading
  `r` is how many lines (halves allowed) the `M` must move to put its V on the middle line,
  positive to the right. Ask whoever reads the paper for that line count per row, not for a ruler
  length: a reading taken from the last ink to a mark includes the glyph's side bearing, the V
  does not.
- One character's error is `(r_row - r_M) x 0.5 mm / N`: positive means it prints that much
  narrower than predicted, negative wider. In dots that is `(r_row - r_M) x p / N`, `p` being
  0.5 mm in the printer's dots (4 at 203 dpi). Long rows resolve it finer: 0.5 mm / N per character.
- Offset or scale: put the `M`-alone row at two `x` far apart (50 and 95 mm on a 100 mm label).
  The same reading at both is a constant offset of where the printer starts the line; a reading
  that grows with `x` is a scale (`scale_across`, calibrated above).
- To correct one field, move its `x` by `r x 0.5 mm`, print again and repeat until it reads 0.
  When a move overshoots (the reading changes sign), move back by half the last move, rounded to
  whole units; the smallest move is one unit (half a line on an Allegro 2). One row's reading
  varies by about half a line between prints, so a row that keeps alternating between 0 and half a
  line is done.
- Font 9 widths come from the tool's own tables, not from the config: a character that reads off
  on paper is a finding to report (https://github.com/bitranox/datamax-dpl/issues) with its label
  and readings, not a setting to change.
