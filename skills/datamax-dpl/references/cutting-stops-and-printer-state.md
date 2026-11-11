# Cutting, stop position and the printer state each job sets

A page of this skill: `SKILL.md`, one folder up, has the workflow and the index of these pages.

## Cutting

- `"cut": {"every": N, "at_end": true}` (`at_end` defaults to true: the last short group is cut
  too). Tell the tool the cutter exists with `--cutter yes` (or `[printer] cutter = "yes"`);
  `--cutter no` refuses any cutting label.
- With `--cutter yes` on a model whose job sets the cutter (`<STX>V`), the end of every print is cut
  (`[printer] cut_after_print`, on by default): such a printer stops the stock at the cutter, where
  it cannot be torn off. A label without `cut` is cut once, after its last copy (`<STX>V1`, plus
  `:nnnn` when it has more than one copy); a `--data` series without `cut` stays joined and is cut
  after its last record (the records before it go out with `<STX>V0`). A cut's `"at_end": false`
  is overridden there (`cut_at_end_kept`). The cutter is then on when the print ends, so the
  printer's FEED key cuts. With `cut_after_print = false`, only a label with `cut` is cut.
- Which jobs send `<STX>V`: on the Allegro and Allegro 2 `--cutter yes` is enough; on the DMX
  400/600/800, Prodigy Max, PE42/PE43, Ovations and Titan 6200 also set `[printer] label_present`
  (`yes` or `no`), else no `<STX>V` goes, nothing is cut at the end of the print, and only the info
  `software_switches_not_sent` says so; the Prodigy family, DMX 430, `generic` and `permissive`
  never send it. `"cut": {"every": 1}` sends no cut command of its own either: without `<STX>V` the
  printer cuts as its own cutter switch is set.
- `[printer] cut_every = N` gives every label without its own `cut` one; the label's own `cut` wins.
  A label cannot opt out of it: print that job with `--set printer.cut_every=null`.
- With `quantity` > N the printer counts copies itself (`:nnnn`); N at or past the quantity cuts once,
  after the last copy (the format asks for the whole quantity, `:0003` for three copies: the manual
  wants less, one Allegro 2 cut once anyway, other models unverified). With `--data`, each record is
  its own format: where the job sets the cutter (`--cutter yes` on a model with a known `<STX>V`
  layout) only every N-th record and the last turn it on, which one Allegro 2 cut right on paper.
  Otherwise the job sends a cut command after every N-th record (`batch_cut_unverified`: the
  printer must not also cut each label by itself).
- `feed`, and the calibration label after a reset, turn an installed cutter off (`<STX>V0`), so
  they are not cut through a label's adhesive. Where no `<STX>V` can be built (the model `generic`,
  `cutter` neither `yes` nor `no`, or `label_present` unset on a model with that bit) `feed` sends
  the form feed alone, the printer keeps its last cutter state, and the info
  `software_switches_not_sent` says why. `feed` takes no `--model` or `--cutter`: give it
  `--set printer.model=ID --set printer.cutter=yes` (or `no`: either value lets `<STX>V0` go out;
  plus `--set printer.label_present=yes` or `no` there) before the command, or a
  `--printer NAME` whose `[printers.<name>]` section sets them.
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
3. `[printer] cutter_stop_position` for a job that is cut (a label with `cut`, or any label
   under `cut_after_print` whose job sends `<STX>V`, above), else `[printer] stop_position` (mm,
   at most 253.746), both sent unscaled; unset, each takes the model default, which only allegro-2
   has (tear-off 66.8 mm, `<STX>f263`; cutter 76.8 mm, `<STX>f302`, measured with a cutter); a
   `--data` record the next record follows uncut sends `[printer] joined_stop_position` instead
   (allegro-2: 25.4 mm, `<STX>f100`), below the start of print, since a stop past it makes the
   printer pull the stock back before the next record; unset on a model with no default, such a
   record keeps the cutter stop. The last record and the calibration label take the cutter stop;
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
label without `cut`, with `--cutter yes` and no config, sends
`<STX>n<STX>c0000<STX>O0220<STX>M1200<STX>SC<STX>V1<STX>f302<STX>qA` (cut after its last copy, at
the cutter stop; with `--cutter no` or `cut_after_print = false` it is `...<STX>V0<STX>f263<STX>qA`), its
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
  cutter bit is on only for a label that is cut (a label with `cut`, or the last label of a print
  under `cut_after_print`: both send `<STX>V1<STX>f302` on the Allegro 2); `feed` and
  `feed --media` send it off.
- `heat` (0-30; above the model's maximum it is clamped, `heat_out_of_range`), `print_speed`,
  `slew_speed`, `backup_speed`, `column_offset`, `row_offset` and `present_distance` go out only
  when the label sets them (at the top level of its JSON, beside `quantity`) or the same-named key
  in `[printers.<name>]` or `[printer]` does (lengths in mm whatever the label's unit); the label's
  own value wins and passes the same range checks. Unset, nothing is sent and the printer keeps its
  own value. A configured `present_distance` goes out only on a model with `<STX>Kf`; elsewhere it
  is not sent (`configured_present_distance_unsupported`, info, on every label; prints under
  `strict` too), so it never replaces the label's stop or the cutter stop: set `stop_position` or
  `cutter_stop_position` there.
