# Skill review, 2026-11-21: checking where font 9 text ends, on paper

Type: reference skill. Test: retrieval by quote-back, pre-edit text (RED) against edited text
(GREEN), on haiku and opus.

## Change

- references/printer-controls-and-calibration.md: new section "Checking where font 9 text ends, on
  paper": the position grid (one DPL unit, 2 dots = 0.25 mm on an Allegro 2), fill rows of one
  character ending in a right-aligned `M` read against an `M`-alone row, an identifier printed on
  every label, V-lines through the final `M` read via XOR, the per-character error formula,
  offset against scale from two `x`, the correction loop, and where to report an off width.
- references/label-json.md: the Fonts bullet links the new section; the left-edge threshold reads
  0.1251 mm (one dot); `preview` reports no findings for a refused aligned line.
- SKILL.md: the routing row of printer-controls-and-calibration.md names the new section.

## Commands executed (allegro-2)

- [x] A recipe label built from the section (an 11-character fill row plus `M`, an `M`-alone row,
      five V-lines each from the quoted `line` JSON at `x` - 0.123 mm x 30 pt): `validate` exit 0,
      13 fields. An over-long fill row (18 `A` at 30 pt) is refused with `text_past_left_edge`.
- [x] Preview of a right-aligned 30 pt `M` at x 95 mm with a line at 91.32 mm: the `M` ink spans
      dots 704-759 and the line sits on dots 730-731, the middle of the V, drawn white across the
      ink.
- [x] Records for x 10.0 / 10.1 / 10.2 mm: columns 0040 / 0040 / 0041, so one unit is 0.25 mm.
- [x] `preview` of a refused right-aligned line: exit 0, no finding, ink from dot 4.
- [x] `tests/test_skill_matches_cli.py` passes.

## Probe (Q1 fill-row layout, Q2 reading without side bearing, Q3 grid and correction loop, Q4 offset
## against scale, Q5 identifier per label, Q6 arithmetic of one reading)

- [x] RED (pre-edit text), haiku: Q1, Q2, Q4 and Q5 NONE; Q3 only the 2-dot rounding.
- [x] GREEN (edited text), haiku and opus: all six answered by direct quote; Q6 0.2 dots narrower
      per `A` in both.
- [x] Gaps fixed after GREEN: overshoot rule and smallest move; right-aligned rows also for a
      centred line; a V outside the five lines; the `line` JSON; where to report.
- [x] Re-probe of the touched questions (haiku): all five answered by direct quote.
- Tiers: haiku, opus. Cells: 4.
- Gaps declined: coordinate units (the label's unit, label-json.md and SKILL.md); where exactly to
  move the lines when a V falls outside (by the row's reading, as for the field); an error threshold
  worth reporting (the reader's tolerance, not the tool's); the source file of the width tables (a
  report goes to the issue tracker, which needs no path).

Decisions: none - this skill is not mirrored into the central marketplace and keeps no decision ledger.
