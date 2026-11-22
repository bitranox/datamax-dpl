# Skill review, 2026-11-22: the preview's lettering font is Nimbus Sans Bold

Type: reference skill. Change: one factual correction in The loop, step 3.

## Change

- The preview letters text in Nimbus Sans Bold (shipped with the package), not Liberation Sans
  Bold: the package ships `adapters/preview/fonts/NimbusSans-Bold.otf` and no Liberation file.

## Commands executed (allegro-2)

- [x] `preview` of a font 9 line "BAT001 Ch. 1" at 14 pt, picture read before and after: before,
      the '1' is a thin stem (3 dots on the middle row against 5 for each '0' stroke); after, 5
      dots against 6 and 5.
- [x] `tests/test_preview_masks.py::test_a_one_is_lettered_as_heavy_as_a_zero` RED with the old
      font (3 against 5), GREEN with the new one.
- [x] `test_the_accent_of_a_capital_is_lettered_inside_its_field` KILLED by mutation_arm with
      the lettering drawn from the font's own ascent (anchor "la", no baseline row).
- [x] The built wheel lists `fonts/NimbusSans-Bold.otf` and `fonts/LICENSE-NimbusSans.txt`.

## Probe

- RED (haiku, pre-edit sentence): Q1 "which font file would you expect" quoted "lettered in
  Liberation Sans Bold" and inferred `LiberationSans-Bold.ttf`, a file the package does not ship.
- GREEN (haiku, edited sentence): Q1 quoted "lettered in Nimbus Sans Bold"; Q2 quoted "(not the
  printer's font)" in both arms.
- Skill gaps reported (both arms) and decided:
  - The printer's own font is not named in this sentence: declined, the font 9 width stand-in is
    named in the same step ("Helvetica-Bold widths, not the lettering").
  - What grey cells look like: declined, the sentence states the alternative only; the preview
    picture is what the reader looks at in this step.
  - Scope of "Python 3.11+": declined, the sentence attaches it to the `preview` extra's uvx line,
    and INSTALL.md states it for the extra.
  - A git-status conflict about deleted font files: void, it read the session's own work tree,
    not the skill.

Tiers: haiku x2 runs (RED, GREEN). The width and fence check over the added line printed nothing.

Decisions: none - a factual rename of the shipped font, no rule added or removed
