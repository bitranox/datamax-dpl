# Printer models and capabilities

datamax-dpl renders every label for one printer model profile. The profile decides how lengths
are written, which DPL commands may appear in the job, and what happens to a label element the
model cannot print. The facts come from the Datamax DPL Programmer's Manual 88-2051-01 Rev D2
(1998), summarised in [dpl-reference.md](dpl-reference.md); none of them has been checked on a
real printer yet.

Pick the profile with `--model` (or `[printer] model` in the configuration). To see one profile on
the command line:

```bash
datamax-dpl models
datamax-dpl capabilities dmx-400
```

Two profiles are not real models:

- `generic` (the default) is the common subset of all 18 models: the smallest limits and only the
  features every model has without a firmware or option condition. Use it when the model is
  unknown.
- `permissive` has no model limits and every feature. Only the syntax checks of the label format
  apply. Use it to see the DPL a label produces without any fallback.

## Firmware and options

A cell reading `fw 2.01` means the feature needs that firmware or newer. Tell datamax-dpl the
printer's firmware with `--firmware 2.01` (or `[printer] firmware`); without it the oldest
firmware is assumed and the feature counts as missing. The version is compared as two numbers,
major and minor: `fw 3.02` is major 3, minor 2, which the manual writes as 3.2, and
`--firmware 3.2` unlocks it.

`option datamatrix` means the printer needs the DataMatrix hardware option. Declare it with
`--option datamatrix` (or `[printer] options = ["datamatrix"]`). Without the declaration a
DataMatrix barcode is still sent, with a warning.

## The fallback policy

When a label asks for something the model lacks, the result depends on the policy
(`--policy`, `[printer] policy`):

- `degrade` (the default) applies the fallback listed below and reports it as a warning. The job
  is built and can be printed.
- `strict` reports the same finding as an error. Nothing is printed and the command exits 1.

Some problems have no fallback. They are errors under both policies; the table says "refused".
Every finding carries a stable `code`, shown in the `findings` list of the JSON output.

| Label asks for                                                    | Feature              | Without it (finding code)                                                                                                                       |
|-------------------------------------------------------------------|----------------------|-------------------------------------------------------------------------------------------------------------------------------------------------|
| `cut.every` above 1                                               | `cut_by`             | The printer cuts after every label (`cut_by_unsupported`).                                                                                      |
| `cut.at_end` when the quantity is not a multiple of `cut.every`   | `cut_now`            | The last group is cut when the next job starts (`cut_now_unsupported`).                                                                         |
| `stop_position`                                                   | `stop_position`      | Labels stop at the printer's default position (`stop_position_unsupported`).                                                                    |
| `present_distance`                                                | `present_distance`   | Sent as a stop position, measured from the sensor (`present_distance_unsupported`); ignored on a model without `stop_position` either.          |
| `pause_each_label`                                                | `pause_each_label`   | The batch prints without pausing (`pause_each_label_unsupported`).                                                                              |
| `media` `gap` or `reflective`                                     | `sensor_select`      | The printer keeps its configured sensor (`sensor_select_unsupported`).                                                                          |
| `backup_speed`                                                    | `backup_speed`       | The printer's default is kept (`speed_unsupported`).                                                                                            |
| Label `attribute`, or an element's `attribute` other than inverse | `attribute`          | Printed with the default attribute (`attribute_unsupported`).                                                                                   |
| `attribute` 5 (inverse) on a text element                         | `attribute`          | Refused (`attribute_unsupported`).                                                                                                              |
| `mirror`                                                          | `mirror`             | Refused (`mirror_unsupported`).                                                                                                                 |
| Lengths in `mm`                                                   | `format_metric`      | Written in hundredths of an inch and rounded (info `inch_units`).                                                                               |
| `circle`, `polygon`, slanted `line`, `box` with a patterned fill  | `circle_polygon`     | Drawn as a bitmap and downloaded as an image before the label (`circle_rasterized`, `polygon_rasterized`, `line_rasterized`, `box_rasterized`). |
| `box` with `fill` 1 (solid)                                       | `circle_polygon`     | Drawn as a box whose edges meet in the middle (info `solid_fill_as_edges`).                                                                     |
| `scalable_text`                                                   | `scalable_fonts`     | Printed in font 9 at the nearest point size the model has (`scalable_font_unsupported`).                                                        |
| `symbol_set`                                                      | `scalable_fonts`     | Ignored (`symbol_set_unsupported`).                                                                                                             |
| Barcodes `ucc_ean128_random_weight`, `telepen`, `maxicode`, `fim` | `extended_barcodes`  | Refused (`symbology_unsupported`).                                                                                                              |
| Barcode `pdf417`                                                  | `pdf417`             | Refused (`symbology_unsupported`).                                                                                                              |
| Barcode `datamatrix`                                              | `datamatrix`         | Sent anyway; declare the option to confirm it (`datamatrix_option_unconfirmed`).                                                                |
| Barcode with `narrow_dots` and `wide_dots` both 0                 | `default_bar_widths` | Narrow bar 2 dots, ratio 3:1 (`default_bar_widths_unsupported`).                                                                                |

Other fallbacks do not depend on a feature column:

| Label asks for                                  | What happens (finding code)                                           |
|-------------------------------------------------|-----------------------------------------------------------------------|
| A font 9 point size the model does not have     | The nearest size it has (`font_size_substituted`).                    |
| A character the font or symbol set cannot print | Replaced by `?` (`characters_unmappable`).                            |
| A speed outside the model's range               | The nearest speed in the range (`speed_out_of_range`).                |
| `heat` above the model's maximum                | The maximum (`heat_out_of_range`).                                    |
| Lower-case text in fonts 3 to 6                 | Printed in capitals (info `text_uppercased`).                         |
| A field past the printable width                | Refused (`outside_print_width`).                                      |
| More fields than the model takes                | Refused (`too_many_fields`).                                          |
| Field data longer than the model takes          | Refused (`field_too_long`).                                           |
| A distance above 9999 DPL units                 | Refused (`distance_too_large`).                                       |
| Barcode data the symbology does not accept      | Refused (`barcode_<problem>`, for example `barcode_bad_check_digit`). |
| `cut` while `--cutter no` is set                | Refused (`no_cutter`).                                                |
| `media` `continuous` without a length           | Refused (`continuous_length_missing`).                                |

The features `cutter_pulse`, `field_terminator`, `stored_formats` and `time_date` are listed for
reference. The label format has no field that uses them, so they never cause a finding.

The speed tables show the ranges the manual gives. A backup speed is only sent where the model
also has the `backup_speed` feature.

## Model tables

Everything between the two marker lines below is generated from
`src/datamax_dpl/domain/profiles.py`. Do not edit it by hand: change the profiles, then run

```bash
.venv/bin/python scripts/gen_capabilities.py --write
```

`tests/test_capabilities_doc.py` fails when the tables and the profiles disagree. Speeds are
DPL letters, A = 1.0 inch per second up to S = 10.0, in steps of 0.5.

<!-- BEGIN GENERATED: scripts/gen_capabilities.py -->

### Limits

| Model              | Name                      | dpi    | Print width (in) | Max column (in/100) | Max fields | Chars/field | Heat |
|--------------------|---------------------------|--------|------------------|---------------------|------------|-------------|------|
| `generic`          | Any model (common subset) | 203    | 3.98             | 398                 | 100        | 3000        | 0-20 |
| `permissive`       | No model checks           | 203    | 99.99            | 9999                | 9999       | 10000       | 0-30 |
| `allegro`          | Allegro                   | 203    | 4.1              | 410                 | 200        | 5000        | 0-20 |
| `allegro-2`        | Allegro 2                 | 203    | 4.1              | 410                 | 200        | 5000        | 0-20 |
| `prodigy`          | Prodigy                   | 203    | 4.1              | 410                 | 100        | 3000        | 0-20 |
| `prodigy-152`      | Prodigy 152               | 152    | 4.15             | 410                 | 100        | 3000        | 0-20 |
| `prodigy-plus`     | Prodigy Plus              | 203    | 4.1              | 410                 | 200        | 5000        | 0-20 |
| `prodigy-plus-152` | Prodigy Plus 152          | 152    | 4.21             | 410                 | 200        | 5000        | 0-20 |
| `dmx-400`          | DMX 400                   | 203    | 4.1              | 410                 | 199        | 5000        | 0-20 |
| `dmx-430`          | DMX 430                   | 289.56 | 3.98             | 398                 | 399        | 10000       | 0-20 |
| `dmx-600`          | DMX 600                   | 300    | 6.4              | 640                 | 399        | 10000       | 0-20 |
| `dmx-800`          | DMX 800                   | 300    | 8.53             | 860                 | 399        | 10000       | 0-20 |
| `prodigy-max-203`  | Prodigy Max 203           | 203    | 4.1              | 410                 | 319        | 7000        | 0-30 |
| `prodigy-max-300`  | Prodigy Max 300           | 300    | 4.16             | 410                 | 319        | 7000        | 0-30 |
| `pe42`             | PE42                      | 203    | 4.1              | 410                 | 319        | 7000        | 0-30 |
| `pe43`             | PE43                      | 300    | 4.16             | 410                 | 319        | 7000        | 0-30 |
| `ovation`          | Ovation!                  | 203    | 4.1              | 410                 | 319        | 7000        | 0-30 |
| `ovation-300`      | Ovation! 300              | 300    | 4.16             | 410                 | 319        | 7000        | 0-30 |
| `ovation-2`        | Ovation 2                 | 203    | 4.256            | 425                 | 319        | 7000        | 0-30 |
| `titan-6200`       | Titan 6200                | 203    | 6.62             | 661                 | 399        | 10000       | 0-30 |

### Speeds

| Model              | Print speed               | Slew (feed) speed         | Backup speed              |
|--------------------|---------------------------|---------------------------|---------------------------|
| `generic`          | C (2 ips), default C      | C (2 ips), default C      | none                      |
| `permissive`       | A-S (1-10 ips), default C | A-S (1-10 ips), default C | A-S (1-10 ips), default C |
| `allegro`          | C (2 ips), default C      | C (2 ips), default C      | C (2 ips), default C      |
| `allegro-2`        | C-E (2-3 ips), default C  | C-E (2-3 ips), default C  | C-E (2-3 ips), default C  |
| `prodigy`          | A-G (1-4 ips), default E  | A-G (1-4 ips), default G  | none                      |
| `prodigy-152`      | A-G (1-4 ips), default E  | A-G (1-4 ips), default G  | none                      |
| `prodigy-plus`     | C-O (2-8 ips), default K  | C-O (2-8 ips), default K  | C-I (2-5 ips), default E  |
| `prodigy-plus-152` | C-O (2-8 ips), default K  | C-O (2-8 ips), default K  | C-I (2-5 ips), default E  |
| `dmx-400`          | C-G (2-4 ips), default G  | C-I (2-5 ips), default K  | C-I (2-5 ips), default E  |
| `dmx-430`          | C-K (2-6 ips), default G  | C-K (2-6 ips), default K  | C-I (2-5 ips), default E  |
| `dmx-600`          | C-O (2-8 ips), default K  | C-S (2-10 ips), default O | C-I (2-5 ips), default E  |
| `dmx-800`          | C-I (2-5 ips), default G  | C-K (2-6 ips), default K  | C-I (2-5 ips), default E  |
| `prodigy-max-203`  | C-S (2-10 ips), default K | C-S (2-10 ips), default O | C-I (2-5 ips), default I  |
| `prodigy-max-300`  | C-O (2-8 ips), default K  | C-O (2-8 ips), default O  | C-I (2-5 ips), default E  |
| `pe42`             | C-S (2-10 ips), default K | C-S (2-10 ips), default O | C-I (2-5 ips), default E  |
| `pe43`             | C-O (2-8 ips), default K  | C-O (2-8 ips), default M  | C-I (2-5 ips), default E  |
| `ovation`          | A-C (1-2 ips), default C  | A-C (1-2 ips), default C  | A-C (1-2 ips), default C  |
| `ovation-300`      | A-C (1-2 ips), default C  | A-C (1-2 ips), default C  | A-C (1-2 ips), default C  |
| `ovation-2`        | A-C (1-2 ips), default C  | A-C (1-2 ips), default C  | A-C (1-2 ips), default C  |
| `titan-6200`       | C-K (2-6 ips), default G  | C-K (2-6 ips), default K  | C-G (2-4 ips), default C  |

### Font 9 point sizes

| Model              | `Annn` sizes (pt)                              | Old `000`-`010` sizes (pt)              |
|--------------------|------------------------------------------------|-----------------------------------------|
| `generic`          | none                                           | 6, 8, 10, 12, 14, 18, 24, 30, 36, 48    |
| `permissive`       | 4, 5, 6, 8, 10, 12, 14, 18, 24, 30, 36, 48, 72 | 5, 6, 8, 10, 12, 14, 18, 24, 30, 36, 48 |
| `allegro`          | none                                           | 6, 8, 10, 12, 14, 18, 24, 30, 36, 48    |
| `allegro-2`        | none                                           | 6, 8, 10, 12, 14, 18, 24, 30, 36, 48    |
| `prodigy`          | none                                           | 5, 6, 8, 10, 12, 14, 18, 24, 30, 36, 48 |
| `prodigy-152`      | none                                           | 5, 6, 8, 10, 12, 14, 18, 24, 30, 36, 48 |
| `prodigy-plus`     | none                                           | 6, 8, 10, 12, 14, 18, 24, 30, 36, 48    |
| `prodigy-plus-152` | none                                           | 6, 8, 10, 12, 14, 18, 24, 30, 36, 48    |
| `dmx-400`          | none                                           | 5, 6, 8, 10, 12, 14, 18, 24, 30, 36, 48 |
| `dmx-430`          | 4, 5, 6, 8, 10, 12, 14, 18, 24, 30, 36, 48     | 5, 6, 8, 10, 12, 14, 18, 24, 30, 36, 48 |
| `dmx-600`          | 4, 5, 6, 8, 10, 12, 14, 18, 24, 30, 36, 48, 72 | 5, 6, 8, 10, 12, 14, 18, 24, 30, 36, 48 |
| `dmx-800`          | 4, 5, 6, 8, 10, 12, 14, 18, 24, 30, 36, 48, 72 | 5, 6, 8, 10, 12, 14, 18, 24, 30, 36, 48 |
| `prodigy-max-203`  | 6, 8, 10, 12, 14, 18, 24, 30, 36, 48           | 5, 6, 8, 10, 12, 14, 18, 24, 30, 36, 48 |
| `prodigy-max-300`  | 6, 8, 10, 12, 14, 18, 24, 30, 36, 48           | 5, 6, 8, 10, 12, 14, 18, 24, 30, 36, 48 |
| `pe42`             | 6, 8, 10, 12, 14, 18, 24, 30, 36, 48           | 5, 6, 8, 10, 12, 14, 18, 24, 30, 36, 48 |
| `pe43`             | 6, 8, 10, 12, 14, 18, 24, 30, 36, 48           | 5, 6, 8, 10, 12, 14, 18, 24, 30, 36, 48 |
| `ovation`          | 4, 5, 6, 8, 10, 12, 14, 18, 24, 30, 36, 48     | 5, 6, 8, 10, 12, 14, 18, 24, 30, 36, 48 |
| `ovation-300`      | 4, 5, 6, 8, 10, 12, 14, 18, 24, 30, 36, 48     | 5, 6, 8, 10, 12, 14, 18, 24, 30, 36, 48 |
| `ovation-2`        | 4, 5, 6, 8, 10, 12, 14, 18, 24, 30, 36, 48     | 5, 6, 8, 10, 12, 14, 18, 24, 30, 36, 48 |
| `titan-6200`       | 4, 5, 6, 8, 10, 12, 14, 18, 24, 30, 36, 48     | 5, 6, 8, 10, 12, 14, 18, 24, 30, 36, 48 |

### Features: job, media and printer settings

| Model              | `cut_by` | `cut_now` | `cutter_pulse` | `stop_position` | `present_distance` | `pause_each_label` | `sensor_select` | `backup_speed` | `stored_formats` | `time_date` |
|--------------------|----------|-----------|----------------|-----------------|--------------------|--------------------|-----------------|----------------|------------------|-------------|
| `generic`          | no       | no        | no             | no              | no                 | no                 | no              | no             | no               | no          |
| `permissive`       | yes      | yes       | yes            | yes             | yes                | yes                | yes             | yes            | yes              | yes         |
| `allegro`          | yes      | yes       | no             | yes             | no                 | yes                | yes             | no             | yes              | yes         |
| `allegro-2`        | yes      | yes       | no             | yes             | no                 | yes                | yes             | no             | yes              | yes         |
| `prodigy`          | no       | no        | yes            | no              | no                 | no                 | no              | no             | no               | no          |
| `prodigy-152`      | no       | no        | yes            | no              | no                 | no                 | no              | no             | no               | no          |
| `prodigy-plus`     | yes      | fw 1.18   | yes            | yes             | no                 | no                 | yes             | yes            | yes              | yes         |
| `prodigy-plus-152` | yes      | fw 1.18   | yes            | yes             | no                 | no                 | yes             | yes            | yes              | yes         |
| `dmx-400`          | yes      | yes       | no             | yes             | no                 | yes                | yes             | yes            | yes              | yes         |
| `dmx-430`          | yes      | yes       | no             | yes             | no                 | yes                | yes             | yes            | yes              | yes         |
| `dmx-600`          | yes      | yes       | no             | yes             | no                 | yes                | yes             | yes            | yes              | yes         |
| `dmx-800`          | yes      | yes       | no             | yes             | no                 | yes                | yes             | yes            | yes              | yes         |
| `prodigy-max-203`  | yes      | yes       | no             | yes             | no                 | yes                | yes             | yes            | yes              | yes         |
| `prodigy-max-300`  | yes      | yes       | no             | yes             | no                 | yes                | yes             | yes            | yes              | yes         |
| `pe42`             | yes      | yes       | no             | yes             | yes                | yes                | yes             | yes            | yes              | yes         |
| `pe43`             | yes      | yes       | no             | yes             | yes                | yes                | yes             | yes            | yes              | yes         |
| `ovation`          | yes      | yes       | no             | yes             | yes                | yes                | yes             | yes            | yes              | yes         |
| `ovation-300`      | yes      | yes       | no             | yes             | yes                | yes                | yes             | yes            | yes              | yes         |
| `ovation-2`        | yes      | yes       | no             | yes             | yes                | yes                | yes             | yes            | yes              | yes         |
| `titan-6200`       | yes      | yes       | no             | yes             | no                 | yes                | yes             | yes            | yes              | yes         |

### Features: format, graphics and barcodes

| Model              | `format_metric` | `attribute` | `mirror` | `field_terminator` | `circle_polygon` | `scalable_fonts` | `extended_barcodes` | `pdf417` | `datamatrix`      | `default_bar_widths` |
|--------------------|-----------------|-------------|----------|--------------------|------------------|------------------|---------------------|----------|-------------------|----------------------|
| `generic`          | no              | no          | no       | no                 | no               | no               | no                  | no       | no                | no                   |
| `permissive`       | yes             | yes         | yes      | yes                | yes              | yes              | yes                 | yes      | yes               | yes                  |
| `allegro`          | no              | no          | no       | no                 | no               | no               | no                  | fw 3.02  | option datamatrix | yes                  |
| `allegro-2`        | no              | no          | no       | no                 | no               | no               | no                  | fw 3.02  | option datamatrix | yes                  |
| `prodigy`          | no              | no          | no       | no                 | no               | no               | no                  | fw 3.02  | option datamatrix | yes                  |
| `prodigy-152`      | no              | no          | no       | no                 | no               | no               | no                  | fw 3.02  | option datamatrix | yes                  |
| `prodigy-plus`     | no              | no          | no       | yes                | no               | no               | no                  | fw 3.02  | option datamatrix | no                   |
| `prodigy-plus-152` | no              | no          | no       | yes                | no               | no               | no                  | fw 3.02  | option datamatrix | no                   |
| `dmx-400`          | yes             | yes         | no       | yes                | fw 2.01          | no               | yes                 | yes      | option datamatrix | yes                  |
| `dmx-430`          | yes             | yes         | no       | yes                | yes              | no               | yes                 | yes      | option datamatrix | yes                  |
| `dmx-600`          | yes             | yes         | fw 2.01  | yes                | yes              | fw 2.01          | yes                 | yes      | option datamatrix | yes                  |
| `dmx-800`          | yes             | yes         | yes      | yes                | yes              | yes              | yes                 | yes      | option datamatrix | yes                  |
| `prodigy-max-203`  | yes             | yes         | yes      | yes                | yes              | yes              | yes                 | yes      | option datamatrix | yes                  |
| `prodigy-max-300`  | yes             | yes         | yes      | yes                | yes              | yes              | yes                 | yes      | option datamatrix | yes                  |
| `pe42`             | yes             | yes         | yes      | yes                | yes              | yes              | yes                 | yes      | option datamatrix | yes                  |
| `pe43`             | yes             | yes         | yes      | yes                | yes              | yes              | yes                 | yes      | option datamatrix | yes                  |
| `ovation`          | yes             | yes         | yes      | yes                | yes              | no               | yes                 | yes      | option datamatrix | yes                  |
| `ovation-300`      | yes             | yes         | yes      | yes                | yes              | no               | yes                 | yes      | option datamatrix | yes                  |
| `ovation-2`        | yes             | yes         | yes      | yes                | yes              | no               | yes                 | yes      | option datamatrix | yes                  |
| `titan-6200`       | yes             | yes         | yes      | yes                | yes              | yes              | yes                 | yes      | option datamatrix | yes                  |

<!-- END GENERATED: scripts/gen_capabilities.py -->
