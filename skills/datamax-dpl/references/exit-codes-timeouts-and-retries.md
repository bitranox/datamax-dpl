# Exit codes, timeouts and retries

A page of this skill: `SKILL.md`, one folder up, has the workflow and the index of these pages.

## Exit codes and waits

The general codes (0 done, 1 refused, 2 could not run, 130 interrupted, 141 reader gone) are in
`SKILL.md`, "Run it". `status` also exits 1 when the printer reports a problem, `explain` when it
could not read part of the job, and `detect` when no model fits; `config` also uses 22 (no such `--section`) and 78 (the config does
not load), `config-deploy` 13 (no permission), 78 (its permission settings refused) and 1 (any other
failure), and `config-generate-examples` 1. A printer that does not answer in time says how long it
was given (`no complete answer within 0.5 s`): raise `--timeout` (seconds) or `[printer] timeout`. A
tcp connect the printer or print server refuses (one does for a moment after closing the last job's
connection) is tried again, each time a connection is opened, every
`[printer] refused_connect_retry_interval` (0.25 s) for up to `refused_connect_wait` (3 s), never
past `timeout`. `status` and
`memory` instead keep trying for `[printer] status_wait` (30 s) while a print server is busy with an
earlier job and then say how long they waited (`no answer in 30.0 s (4 attempts)`; the attempts
are `[printer] answer_retry_interval`, 0.25 s, apart, as are those of the stored-name reads below):
raise `status_wait`, or pass `--timeout`, which replaces it. Before each label whose downloads no
clear precedes, and (unless `upload_check_rounds` is 0) after any downloads a label sends, `print`
may spend up to
`[printer] stored_names_wait` (30 s), plus `[printer] directory_idle` (1 s), the silence that ends
the answer, reading the printer's stored image names; `--timeout` does not change that wait.
`memory` ends each listing on `directory_idle` too (`--idle` overrides it); `status_wait` or
`--timeout` bounds the wait for an answer to begin, and once one has begun it may take at most
`[printer] directory_read_limit` (120 s) to end. Nothing about them is kept from one
label to the next: the printer may have been reset, or another program may have printed. So a
`--data` batch whose labels download images with no clear before them is refused from its second
record on (`batch_image_names_unread`) where no printer can be read before each label: `render`,
`validate` with no tcp or serial printer named, and `print` to a `file:`, stdout or a device -
unless `[printer] clear_graphics_after` (on by default) clears that module after each label, which
removes every name the label before downloaded; it reaches the model's built-in RAM module, and on
the plain Prodigy any module, but never another plug-in module. Then no record is refused for its
names: after a whole-module clear (`<STX>q`, the model's built-in RAM) only the first record is
warned, since the module is empty for the rest; the plain Prodigy, which removes only its own
names, warns on every record (`image_names_unread`), as names another program stored survive. Turn
`[printer] clear_graphics_before` on (it clears only the model's built-in RAM module, so leave
`image_module` unset or name that module; the plain Prodigy, which has none, removes each name it
downloads instead, in the module `image_module` names), or send to a two-way printer, the only way
for the Prodigy Plus and `generic`, which have no clear. A single label on a route that reads no
printer, and a batch's first record, only says its names were not read (`image_names_unread`, a
warning unless `[printer] held_image_severity` raises it; that key (`info`, `warning` or `error`)
also sets `image_name_held`, a download left out because the printer holds its name, and `error`
refuses either whatever `--policy` says). A job is sent in `[printer] send_chunk_bytes` pieces
(1024) and the timeout bounds each one; a job cut off part way says to power-cycle the printer,
which may be waiting for the rest.

Every key with its range, default and environment variable is in
<https://github.com/bitranox/datamax-dpl/blob/HEAD/CONFIG.md>.
