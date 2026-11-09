# Cut after every print when a cutter is installed (design, 2026-11-09)

Approved by the user on 2026-11-09. With a cutter installed the Allegro 2 has no tear-off position: the stock stops
at the cutter, and a label that is not cut cannot be taken off by hand. So the last label of every print must be cut.

## What a print is

One `print` (or `render`) of one label, or of a whole `--data` series. A print can hold several labels that stay
joined; it is cut once after its last label.

## Behaviour

- Scope: only when the cutter is installed (`--cutter yes`, `[printer] cutter = yes`) on a model whose job can set
  the cutter (it sends `<STX>V`). `generic`, and a cutter that is unknown or absent, behave as today.
- Every job uses the cutter stop position (`cutter_stop_position_mm`, 76.8 mm on the Allegro 2), never the tear-off
  stop position, whether or not it cuts.
- The end of every print is cut. `cut` and `cut_every` still decide the cuts between labels; a label without `cut`
  is not cut between labels, only at the end. A cut's `"at_end": false` is overridden, with the info finding
  `cut_at_end_kept` (user, 2026-11-09).
  - One label with quantity N and no cut: `<STX>V1` plus cut-by `:N` (measured: `:0003` with `Q0003` cut once,
    after the third copy).
  - A `--data` series: `<STX>V0` on every record no cut follows, `<STX>V1` on the last record (and on those `cut`
    says), `<STX>f302` on all (measured 2026-11-09: cut once after each such record, no `<STX>o`).
  - The cutter is then on when the print ends, so a panel FEED cuts too.
- `feed`, the calibration label and the reset label still turn the cutter off first (`<STX>V0`).
- Settings key `[printer] cut_after_print`, default on (the user's rule: every behaviour switch gets a key). Turned
  off, a job behaves as before this change.

## Memory

No new state: whether a record is the last of the series is already known to the batch cut plan, which walks the
records one by one.

## Paper check (Allegro 2, outcomes written down before printing)

1. A plain label: one cut, in the gap.
2. A 3-record series without `cut`: the three labels stay joined, one cut after the 3rd.
3. Panel FEED afterwards: an empty label is fed and cut.
4. Another plain label: its leading `<STX>V0` turns the cutter off with no stray cut and no extra feed (the user's
   open point), then it is cut at its end.

## Release

0.9.0 is released without this; it goes into 0.10.0 (it changes what every job on a cutter printer does).
