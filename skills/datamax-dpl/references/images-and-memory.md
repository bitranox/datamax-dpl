# Format length, images and the printer's memory

A page of this skill: `SKILL.md`, one folder up, has the workflow and the index of these pages.

## Format length, image memory, image module, upload check, printer reset

- Format length: a field reaching past `[printer] max_format_rows` dot rows along the feed, as the
  job commands it (scale and offset included), is refused under both policies
  (`format_past_max_length`); a label `dot_size` of height 2 doubles it. Unset it is the model's
  limit: 1648 on `allegro-2` (the lowest row one printer was seen to cut off), none elsewhere.
  A longer label prints as segments: see `printer-controls-and-calibration.md`.
- Image memory: a job's downloaded images (given bitmaps, raster fallbacks, QR Codes) must fit
  `[printer] image_memory_bytes` (`allegro-2` default 49088, read from one printer's empty module A
  cartridge: on another one, set it from `memory --kind graphic`; other models have no budget). Past
  it, `degrade` sends every image but a QR Code at half resolution and double magnification
  (`image_halved`; `preview` shows it). Refused (`image_memory_exceeded`): still past it after
  halving, any job past it under `strict`, a halved image at `magnification` 5 or more. On a
  two-way `print` the model's budget is left out (the upload check decides,
  `image_budget_skipped`) unless `upload_check_rounds` is 0; a configured `image_memory_bytes`
  still applies.
- Image module: downloads go to the model's built-in module (`image_module` in `capabilities`); a Prodigy/Prodigy Plus has none and refuses them (`image_module_unknown`) until `[printer] image_module` (A-E) names the module fitted. The module is cleared before each label's downloads and again after the label's format and any cut, on a label that downloads nothing too (`[printer] clear_graphics_before` and `clear_graphics_after`, both `true`; the library's `RenderOptions.clear_graphics_before_label` and `clear_graphics_after_label`); the clear (`<STX>q`) goes only to the model's built-in RAM module, never to a plug-in one, whatever the keys say, and the plain Prodigy removes each name a label downloads instead. `<STX>q` empties the whole module, the images the printer stores and any fonts and formats in it too, so a label that places a stored image (an `image` without `bitmap`) is refused (`stored_image_cleared`) while `clear_graphics_before` is on, and a `--data` batch of it from its second record while only `clear_graphics_after` is: keep a stored image usable with both keys `false`. The plain Prodigy's removes take only the label's own downloads.
- Upload check (`print`, two-way link): a download into a full module is lost silently, so a label's
  downloads go first, the printer's directory (`<STX>W`) is read within `stored_names_wait` (plus
  the `directory_idle` silence that ends the answer), and its format follows only once every
  downloaded name is listed. Else round 2 clears (`q` on built-in RAM, the Prodigy's `R`, never a
  plug-in module) and uploads again (with no clear, only the names the directory did not list; after
  a read with no answer it reads the names again first and uploads none of the same names unless
  that read answers), round 3 uploads halved (`image_upload_retried`). With no clear before a label,
  its names are read right before its downloads and only those not listed go; if that read gets no
  answer the job's first label uploads every image (`image_names_unread`), and a later label skips
  round 1 and uploads nothing until round 2's read answers. Still missing, the label is refused and
  nothing more is sent (`image_upload_refused`), as when it is in error once rendered again for a
  round (`image_upload_render_refused`). `[printer] upload_check_rounds` (0-3, default 3); 0 reads
  nothing back after the downloads and retries nothing, so a lost download goes unseen (one Allegro
  2 then printed the rest of the label with no error); the names read before a label's downloads,
  where no clear precedes them, still runs. An `image_module` of `C` (no directory lists it) is
  refused before anything is sent (`image_module_unlisted`); set the real letter (A on the Allegro
  2) or use a one-way link.
- Printer reset (`print`, two-way link): before the first label the job reads `<SOH>A` once within `reset_check_wait` (3 s; no answer means nothing), after that label's names read where one goes, whose answer can carry the report itself. On `R` or `T` in front of the answer (`reset_reported`) one calibration label goes first, because one Allegro 2 printed the first label after a power cycle about 12 mm shifted: the job's printer settings and size, `calibration_label_text` in the middle (`printer_reset_calibrated`: one extra label came out). `calibrate_after_reset = false` only reports it (`printer_reset_not_calibrated`); `[printer] reset_check = false` skips that read and also ignores a reset report a names read returns; `reset_check_wait`, `calibrate_after_reset` and `calibration_label_text` are `[printer]` keys too. Two limits: a `status` or `memory` run before the job takes the report, so that job's first label can be shifted; a one-way link cannot see a reset.
- Every downloaded image goes under its content name (`D` + 7 characters from `0-9 A-Z a-z`, from
  its hash; letter case counts, measured kept and matched exactly on an Allegro 2 CA02 03.04 only),
  never the `image` element's `name`; only an `image` without `bitmap` that no element of the label
  downloads names a printer image by its `name`. An image a 0.4.0 job left on a never-cleared
  module (`D` + 7 hex) is downloaded once more under its new name; on a nearly full module that
  download can be lost silently, which the upload check catches on a two-way link.
- `datamax-dpl memory --kind all --printer tcp://HOST:9100` lists the names the printer stores
  and the free bytes of each memory module (read-only; tcp or serial). The `allegro-2` budget is
  the 49088 free bytes one printer reported for an empty module A; set yours the same way.
