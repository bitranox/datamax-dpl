# Cutting, stop position and the printer state each job sets

A page of this skill: `SKILL.md`, one folder up, has the workflow and the index of these pages.

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
clear before and after it (Image module, in `images-and-memory.md`). A 50 x 30 mm `allegro-2`
label without `cut`, with `--cutter yes` and no config, sends `<STX>n<STX>c0000<STX>O0220<STX>M1200<STX>SC<STX>V0<STX>f263<STX>qA`, its
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
