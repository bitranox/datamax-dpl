# SKILL.md review, 2026-11-21: font 9 widths measured on paper

Type: reference skill. Test: retrieval by quote-back, pre-edit text (RED) against edited text
(GREEN), on haiku.

## Change

- SKILL.md loop step 3: font 9 line ends are estimated from widths measured on an Allegro 2 for
  the capitals, digits, `,-./` and space, and from an unverified Helvetica-Bold stand-in for the
  rest.
- references/label-json.md Fonts: which characters are measured (at 12 and 30 pt, Allegro 2), how
  close a right-aligned line of them ends (about 0.35 mm), and that lower case, other punctuation,
  umlauts, 14-24 pt and other models need a check on paper. The closing sentence of the `align`
  bullet says the same.

## Commands executed (allegro-2)

- [x] `tests/test_font_9_paper_widths.py`: every line whose characters are all measured ends
      within 0.35 mm of where it printed (56 lines, largest residual 0.32 mm); the four lines that
      ran off the label are predicted past its 100 mm edge.
- [x] Frontmatter unchanged.

## Probe (3 questions: how close "12345 STK" at 30 pt ends and whether its widths are measured, a
## lower-case umlaut line at 18 pt, which characters and sizes are measured)

- [x] RED (pre-edit text from `git show HEAD`), haiku: Q1 answered only "can end millimetres from
      `x`" and "estimated"; Q3 answered NONE.
- [x] GREEN (edited text), haiku: all three answered with direct quotes (within about 0.35 mm,
      measured; no, lower case and umlauts are unverified and 18 pt is unmeasured; capitals,
      digits, `,-./` and space at 12 and 30 pt).
- Tiers: haiku. Cells: 2.
- Gaps declined: whether the measured widths hold on models other than the Allegro 2 is unknown,
  and the text says to check on paper there.

Decisions: none - this skill is not mirrored into the central marketplace and keeps no decision ledger.
