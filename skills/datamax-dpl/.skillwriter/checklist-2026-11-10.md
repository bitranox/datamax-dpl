# SKILL.md review, 2026-11-10: facts the page left out

Type: reference skill. Test: retrieval by quote-back.

## Change

Eight additions to SKILL.md, each checked against the 0.10.0 code first:

1. Description: triggers for reading a .dpl back as JSON (`explain`), PNG preview, calibration,
   status/model/stored images. Measured length 706 characters (cap 1024); YAML loads to
   `['description', 'name']`.
2. Exit 2 names a data record without a field a placeholder names (run: `validate` with a CSV
   lacking the column exits 2, "data record 0 has no field 'name'").
3. Finding codes are stable and listed in docs/capabilities.md (linked by URL).
4. `[printer] max_reported_downloads` (1000) caps `data.downloads` (settings.py field, CONFIG.md
   row).
5. The `--printer` URI forms and their defaults, and which channels read answers (uri.py
   docstring and `DEFAULT_BAUD = 9600`, model field defaults N/8/1/off).
6. A pointer to docs/label-spec.md for every element field (each named field found there).
7. The fallback codes `circle_rasterized`, `polygon_rasterized`, `font_size_substituted`,
   `speed_out_of_range`, `heat_out_of_range` (each in domain/enums.py and docs/capabilities.md).
8. `preview` options: `-o` required, no `--printer`, `[printer]` scales apply (`preview --help`;
   `cli_preview` builds `render_options(settings)` from the `[printer]` section).

Plus the routing row for the cutting page names `cut_after_print` and `joined_stop_position`,
which that page covers.

## Evidence

- [x] RED: the pre-edit SKILL.md (`git show HEAD:`) contains none of the eight facts (grep for
      each returned 0). A behavioural RED arm was not run: the installed plugin ships the old
      text, so a probe could load it; the text check against the old file is the evidence.
- [x] GREEN: nine retrieval questions, answer by direct quote or NONE, given the edited text.
      `opus` 9/9 quoted the governing line; `haiku` 9/9. Tiers: opus, haiku; 18 cells.
- [x] Width and fence check over the added lines prints nothing after the reflow.
- [x] `tests/test_skill_matches_cli.py`: 5 passed (no `datamax-dpl <word>` that is not a command).
- [x] Skill gaps reported by both tiers, each decided:
  - Serial defaults not stated: closed, the example values are now named as the defaults.
  - Exit 2 not tied to `validate` by name: declined, the exit-code list applies to every command,
    as its sentence says.
  - What `status` over `device:` does: declined, the sentence says only tcp and serial read
    answers; the per-command exit codes are on exit-codes-timeouts-and-retries.md.
  - The `fill` field's type: declined, the line points to docs/label-spec.md for field details on
    purpose.
  - How to leave out the printed row: declined, editing the data file is the method the step
    names.
- Decisions: none - factual corrections against the current CLI, no rule chosen.
