# Skill review, 2026-11-21: the preview's lettering font and printed text

Type: reference skill. Change: two factual corrections in The loop, step 3.

## Change

- The preview letters text in Liberation Sans Bold (shipped with the package), which draws
  umlauts, `ß` and every other code page 437/850 character, instead of "Pillow's font".
- The preview letters text as it prints: `?` for a character the font cannot print
  (`characters_unmappable`), capitals in fonts 3-6.
- The font 9 width stand-in is named "Helvetica-Bold widths, not the lettering", so it cannot be
  read as the lettering font.

## Commands executed (allegro-2 and dmx-600)

- [x] `preview` of a font 9 line with umlauts, `ß` and a degree sign before the font fix: the
      umlauts and `ß` drew as empty boxes; after it, every letter draws (picture read).
- [x] A font 2 and a font 9 line of `ÄÖÜ ß éè` render with their accents (picture read).
- [x] `tests/test_preview_masks.py::test_accented_letters_are_lettered_as_themselves_not_as_one_shared_box`
      goes RED with Pillow's built-in font restored (mutation_arm, KILLED by the intended assertion).
- [x] `preview` of font 2 `a€b Größe` and font 3 `abc` on allegro-2: before, `a€b` and `abc`;
      after, `a?b Größe` and `ABC`, matching the job (`validate` warns `characters_unmappable`,
      the rendered job holds `ABC`).
- [x] `test_a_text_field_is_lettered_as_the_printer_prints_it_not_as_typed[abc-ABC-3]` goes RED
      with `element.text` restored in the preview adapter (mutation_arm, KILLED).

## Probe

- RED (haiku, pre-edit text from `git show HEAD:`): Q1 "which font" quoted "Pillow's font"; Q2
  "will ö and ß letter correctly" answered NONE; it reported the Helvetica-Bold stand-in could be
  read as the lettering font.
- GREEN round 1 (haiku and opus, first edit): Q1 and Q3 quoted; both declined to count `ß` as
  covered by "umlauts", and opus repeated the Helvetica-Bold ambiguity.
- Fix: `ß` named explicitly; "Helvetica-Bold widths, not the lettering".
- GREEN round 2 (haiku, final font wording): Q1 and Q2 quoted the new sentence, Q3 ("is
  Helvetica-Bold the lettering font") answered NONE as intended. Gaps it reported, decided:
  - what the preview draws for a character outside code page 437/850: closed by the printed-text
    sentence (`?`, `characters_unmappable`).
  - the font file missing from an install: declined, a packaging defect, not usage; the wheel is
    checked for it by `tests/test_packaging.py` (REQUIRED_WHEEL_DATA, lines 58-59).
  - which code page the label uses: declined, covered elsewhere (SKILL.md step 3 names code page
    437/850; docs/dpl-reference.md has the font code pages).
- GREEN printed-text sentence (haiku): Q1 (euro sign in font 2), Q2 (font 4 "kg") and Q3 (as
  typed or as printed) all quoted. Its gap "might the euro sign fall under every code page 437/850
  character" declined: the euro sign is in neither (Python `encode("cp437")` and
  `encode("cp850")` both raise), so the `?` rule governs it.
- Tiers: haiku x4 runs, opus x1; 3 questions each.

Decisions: none - this skill is not mirrored into the central marketplace and keeps no decision ledger.
