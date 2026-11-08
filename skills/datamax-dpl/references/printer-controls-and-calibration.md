# Reading DPL, the printer's own controls, several printers, calibration

A page of this skill: `SKILL.md`, one folder up, has the workflow and the index of these pages.

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
