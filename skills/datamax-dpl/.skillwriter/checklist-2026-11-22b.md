# Skill review, 2026-11-22: a label's stop_position wins over its present_distance

Type: reference skill. Change: `references/cutting-stops-and-printer-state.md` rule 2 and
`references/printer-controls-and-calibration.md` say that on a model with `<STX>f` but no
`<STX>Kf`, a label that sets `stop_position` (a number or `"printer"`) does not send its
`present_distance` (`present_distance_unsupported`, fallback `ignored`), so a job sends one
`<STX>f` at most. SKILL.md is unchanged.

## Commands executed

- [x] `build_job` on dmx-800 and allegro-2, label `stop_position` 60 + `present_distance` 30,
      degrade and strict: one `<STX>f`, equal to the job without `present_distance`;
      `present_distance_unsupported` with fallback `ignored`, a warning under degrade and an error
      under strict (`tests/test_render.py::test_a_label_with_both_stop_position_and_present_distance_sends_only_its_stop`).
- [x] `"stop_position": "printer"` + `present_distance` 30 on allegro-2: no `<STX>f`
      (`tests/test_render.py::test_a_labels_stop_position_of_printer_drops_a_present_distance_it_could_send_only_as_a_stop`).
- [x] A label with only `present_distance` on allegro-2 still sends it as `<STX>f123`
      (`test_a_present_distance_sent_as_the_stop_position_is_the_labels_own_stop`, unchanged).

## Probes (haiku, rule 2 excerpt, three questions: number stop + present, "printer" + present,
present only)

- [x] RED, pre-edit text: Q1 answered two `<STX>f`, quoting "with `stop_position` also set, both
      go out as `<STX>f`, present distance second".
- [x] GREEN, edited text: Q1 answered one `<STX>f`, quoting "with `stop_position` set (a number or
      `"printer"`) the present distance is not sent (`present_distance_unsupported`, fallback
      `ignored`), so a job sends one `<STX>f` at most."; Q3 one `<STX>f` carrying 20, quoting
      "only when the label sets no `stop_position`".
- [x] Skill gaps reported and decided:
  - What `"printer"` resolves to (Q2): declined, covered in the same file outside the excerpt,
    rule 3 "`"printer"` in either key sends none, that default included (a label's `"printer"`
    too)", and in printer-controls-and-calibration.md "`stop_position = "printer"` (in the label
    too) sends no `<STX>f`".
  - Whether `scale_along` corrects a present distance sent as a stop, what the finding codes
    are: declined, outside this change; finding codes are listed in docs/capabilities.md.

Decisions: none - the change states the code's behaviour after a user decision on the library
