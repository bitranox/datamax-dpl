# SKILL.md review, 2026-11-20: text alignment, font 9 widths, test print and QR size

Type: reference skill. Test: retrieval by quote-back, pre-edit text (RED) against edited text
(GREEN), on opus and haiku.

## Change

- SKILL.md loop step 3: the preview and `validate` work from ESTIMATED text ends (font 9 is a
  stand-in width table), so a cut in the preview is no proof of overflow and a clean `validate`
  no proof of fit; only paper decides.
- SKILL.md loop step 4: test-print with the options production sends; the allegro-2 cutter bytes
  of no `--cutter`, `--cutter no` and `--cutter yes`.
- SKILL.md: a Label JSON bullet for `align`; the label-json.md routing row names font 9 widths,
  `align`, `text_past_left_edge` and QR Codes of one printed size.
- references/label-json.md Fonts: font 9 is CG Triumvirate Bold, measured with Helvetica-Bold
  widths at the printed point size, unverified on paper; `align` with its example, rounding and
  refusals (exit 1 for `text_past_left_edge`, exit 2 for an aligned field that is rotated or has
  a `counter`, a `font_id` or a font 9 `width_multiplier`).
- references/label-json.md QR Codes: the symbol size formula, the level-M version bands for byte
  data, four module sizes that print about 25 mm, one label file per module size, and why two QR
  elements in one label cannot take turns.

## Commands executed (allegro-2, file: and stdout targets)

- [x] `render` with no `--cutter`: no `<STX>V`, stop `<STX>f263`; `--cutter no`: `<STX>V0` and
      `f263`; `--cutter yes`: `<STX>V1` and `f302`.
- [x] `preview` of QR Codes with lower-case byte data of growing length at level `M`: version 1
      up to 14 bytes, 2 for 15-26, 3 for 27-42, 4 for 43-62; symbol widths at 9, 8, 7 and 6 dots
      match `(17 + 4 x version) x module_dots / 8` mm.
- [x] A QR `{name}` filled with an empty value: refused (`data` needs at least one character).
- [x] `validate` of a right-aligned line that would start left of the label: exit 1,
      `text_past_left_edge`; of a rotated right-aligned field: exit 2, "align 'right' needs an
      upright field (rotation 0)".
- [x] Frontmatter loads to `['description', 'name']` (description 706 characters, unchanged).

## Probe (5 questions: the test-print options and cutter bytes, what a preview cut proves, a
## right-aligned font 9 field, rotation and counter with `align`, a QR Code of one printed size)

- [x] RED (pre-edit text from `git show main`), opus and haiku: Q3, Q4 and Q5 answered NONE;
      Q1 and Q2 answered only in part (no cutter bytes, no word on estimated text ends).
- [x] GREEN (edited text), opus and haiku: all five answered with direct quotes.
- [x] GREEN gaps closed: the exit code of each refusal; that a left-aligned field still takes
      rotation, counter, `font_id` and `width_multiplier`; why two QR elements in one label cannot
      take turns; that the version bands are for byte data and level `M`.
- [x] Re-probe of Q3, Q4 and Q5 on the final label-json.md, haiku: all three answered with direct
      quotes. No new undecided gap.
- Re-probe gaps declined: that `x` and `y` are millimetres is in SKILL.md (`unit`), which the
  re-probe did not carry; how close the stand-in widths come to the printer is open until a paper
  test; DPL units of models other than the Allegro 2 are in docs/label-spec.md.
- Tiers: opus, haiku. Cells: 4 plus 1 re-probe.
- Gaps declined: what a printer without a cutter does with `<STX>V1` is not measured; the accuracy
  of the font 9 stand-in is stated as unverified rather than guessed.

Decisions: none - this skill is not mirrored into the central marketplace and keeps no decision ledger.
