# Label format

A label is one JSON document: job settings plus a list of `elements`. The same document drives
`validate`, `render`, `preview` and `print`, and the library reads it with
`Label.model_validate()`. The machine-readable JSON Schema comes from the CLI:

```bash
datamax-dpl schema > label.schema.json
```

With `--json`, the schema is wrapped in the usual envelope under `data.label_schema`.

Unknown keys are refused, so a typo is an error rather than a silently ignored setting. Every
example on this page passes `datamax-dpl validate --model dmx-800 --option datamatrix`, except the
`image` one, which names a stored `LOGO`: that model clears its image module before the label, so
it answers exit 1 with `stored_image_cleared`, as described there. A model
that lacks a feature may refuse or replace an element; [capabilities.md](capabilities.md) lists
what happens on which model.

## Units and coordinates

Every length is a finite number in the label's `unit`, at most 100000; past that the label is
refused naming the field:

| `unit`         | Meaning                                 |
|----------------|-----------------------------------------|
| `mm` (default) | millimetres                             |
| `in`           | inches                                  |
| `dots`         | printer dots, at the chosen model's dpi |

Coordinates follow DPL, not screen conventions:

- `x` is the distance from the LEFT edge of the label.
- `y` is the distance from the BOTTOM edge, the edge that leaves the printer last. A larger `y`
  is higher up on the label.
- An element's reference point is its lower-left corner before rotation. A circle is placed by
  its centre.

DPL writes distances as whole numbers: tenths of a millimetre on models with metric formats,
hundredths of an inch elsewhere. Values are rounded to that grid, and a model without metric
formats reports the info finding `inch_units`. Check placement without paper with
`datamax-dpl preview`, which draws the label as a PNG with the same coordinates.

## Job settings

| Key                 | Type                                   | Default | Meaning                                                                                                                                                                    |
|---------------------|----------------------------------------|---------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `unit`              | `mm`, `in`, `dots`                     | `mm`    | Unit of every length in the label.                                                                                                                                         |
| `width`             | number > 0                             | none    | Label width. Wider than the model's print width gives a warning. The preview uses it as canvas width.                                                                      |
| `height`            | number > 0                             | none    | Label length in feed direction. Used by the preview and as the length on continuous media.                                                                                 |
| `quantity`          | integer 1-9999                         | 1       | Number of labels to print (`Qnnnn`).                                                                                                                                       |
| `elements`          | list, 1 to 2048                        | -       | The fields of the label, described below.                                                                                                                                  |
| `cut`               | object                                 | none    | Cutting, see below. Needs a cutter; `--cutter no` refuses a label that sets it.                                                                                            |
| `heat`              | integer 0-30                           | none    | Print head heat (`Hnn`). Above the model's maximum it is lowered to the maximum.                                                                                           |
| `print_speed`       | letter `A`-`S`                         | none    | Print speed (`Pa`): `A` = 1.0 inch per second, each letter 0.5 more, `S` = 10.0.                                                                                           |
| `slew_speed`        | letter `A`-`S`                         | none    | Feed speed without printing (`Sa`).                                                                                                                                        |
| `backup_speed`      | letter `A`-`S`                         | none    | Backfeed speed (`pa`).                                                                                                                                                     |
| `dot_size`          | object `{"width": 1-2, "height": 1-3}` | none    | Dot size command `Dwh`. Without it the format sends `D11` where the model's default is `D22`.                                                                              |
| `column_offset`     | number >= 0                            | none    | Shift every field to the right (`Cnnnn`).                                                                                                                                  |
| `row_offset`        | number >= 0                            | none    | Shift every field up (`Rnnnn`).                                                                                                                                            |
| `attribute`         | 1, 2, 3, 5                             | none    | How fields combine with what is already printed: 1 XOR, 2 transparent, 3 opaque, 5 inverse (`An`).                                                                         |
| `mirror`            | boolean                                | false   | Print the label mirrored (`M`).                                                                                                                                            |
| `symbol_set`        | two characters `A-Z0-9`                | none    | Symbol set of scalable fonts (`ySxx`), for example `PM` (code page 850) or `W1` (Windows Latin 1).                                                                         |
| `media`             | `gap`, `reflective`, `continuous`      | none    | How the printer finds the next label. `continuous` needs `continuous_length` or `height`.                                                                                  |
| `continuous_length` | number > 0                             | none    | Length sent as `<STX>c` on continuous media. An Allegro 2 feeds it after the content, so its label pitch is content plus this (measured, see dpl-reference.md section 10). |
| `stop_position`     | number > 0, or `"printer"`             | none    | Feed this far past the start of print; backed up on the next job (`<STX>f`). `"printer"` sends none.                                                                       |
| `present_distance`  | number > 0                             | none    | Distance to present a label for removal (`<STX>Kf`).                                                                                                                       |
| `pause_each_label`  | boolean                                | false   | Pause after each label (`<STX>J`). The setting stays on until the printer is reset.                                                                                        |
| `count_by`          | integer 1-99                           | none    | Count-by amount (`^nn`) for counters.                                                                                                                                      |
| `plain_zero`        | boolean                                | false   | Print 0 without the slash in fonts 0-8 and barcodes (`z`).                                                                                                                 |

A `stop_position` of `"printer"` sends no `<STX>f`: the printer keeps its own setting, and the
configured stop and the model's default are not sent either. The one exception is a
`present_distance` on a model without `<STX>Kf`, which still goes out as `<STX>f`
(`present_distance_unsupported`). How `stop_position` and `present_distance` rank against the
configured stop positions and the model's default, and what a model without `<STX>f` or `<STX>Kf`
does with them, is in the README under [Where the label stops](../README.md#where-the-label-stops).

`heat`, `print_speed`, `slew_speed`, `backup_speed`, `column_offset`, `row_offset` and
`present_distance` each have a `[printer]` key of the same name that sets it for a label that sets
none, the lengths in mm; the label's own value wins, and an unset key sends nothing. See
[Label settings for every label](../CONFIG.md#label-settings-for-every-label).

Where fields overlap, the attribute decides what prints, and `preview` draws the same: 1 XOR
prints only the dots an odd number of fields share, 2 transparent prints both, 3 opaque lets the
later field whiten the rectangle it covers. Inverse (5) prints white on black, which the preview
does not draw; it shows as XOR there. With Pillow (the `preview` extra) text combines by its
letters' dots, as an Allegro 2 printed a line through text with gaps only under the strokes;
without it by its whole character cells (`preview_text_approximated`). A barcode is drawn as a
hatched area of its estimated size, not as its bars, so a field crossing a barcode combines with
the hatching's dots and previews only approximately. A text or scalable text
field's own `attribute` overrides the label's. Models without the `An` command (see
[capabilities.md](capabilities.md)) always XOR: an Allegro 2 printed a box over an image of the
same size as only their difference. There a label's `attribute` 2 is emulated: lines, boxes,
circles, polygons, QR Codes and images with a bitmap that share dots are drawn together into one
downloaded image (`transparency_emulated`), and `preview` draws them OR. So is a stored image whose
name another element of the label downloads, as the bitmap it prints. Text, barcodes and other
stored images cannot be merged; one that overlaps another field still prints XOR
(`transparency_not_emulated`). Unverified on paper; see [dpl-reference.md](dpl-reference.md) for
the cost in image bytes. Without an `attribute` the preview shows XOR, each format's default; a DMX
600 or 800 takes its default from the operator panel instead.

`cut`:

| Key      | Type           | Default | Meaning                                                                                               |
|----------|----------------|---------|-------------------------------------------------------------------------------------------------------|
| `every`  | integer 1-9999 | 1       | Cut after every N labels. With 1 the format carries no cut-by command and the cutter cuts each label. |
| `at_end` | boolean        | true    | Also cut the last group when the quantity is not a multiple of `every` (`<STX>o` after the format).   |

A label that sets `cut` switches the cutter on for its job (`<STX>V`, sent where `[printer] cutter` is
`yes`). One without it is cut once, after its last copy, where `[printer] cut_after_print` is on (the
default): a printer with its cutter installed stops the stock at the cutter, where it cannot be torn
off. With `cut_after_print` off its job switches the cutter off, and only a label with `cut` is cut.
A `--data` series is cut after its last record, the records before it staying joined, and a cut's
`at_end: false` is overridden there. `[printer] cut_every` gives every label without its own `cut`
one (to print one job without it where the config sets it, run it with
`--set printer.cut_every=null`). An `every` at or past the quantity
cuts once, after the last copy: the format asks for the whole quantity (`:0003` for three copies).
The manual (p.64) wants an amount below the quantity, but one Allegro 2 cut once after the third of
three copies (2026-11-06); other models are unverified. A model without cut-by cuts after every
label (`cut_by_unsupported`).

A job with settings:

```json
{
  "unit": "mm",
  "width": 100,
  "height": 50,
  "quantity": 12,
  "heat": 12,
  "print_speed": "E",
  "media": "gap",
  "cut": {"every": 5},
  "elements": [{"type": "text", "x": 5, "y": 40, "text": "BATCH 7", "font": 3}]
}
```

## Elements

Every element has a `type`. Rotated elements take `rotation` in degrees: 0, 90, 180 or 270,
written as the DPL rotation digit 1-4 (see [dpl-reference.md](dpl-reference.md), section 5).

A rotated field turns clockwise about its reference point, which stays where `x` and `y` put it,
as the manual's sample label prints ROTATION 1 to 4 from one point (Figure 7-1): 0 runs right and
grows up, 90 runs down and grows right, 180 runs left and grows down, 270 runs up and grows left.
`preview` draws text, scalable text and barcodes that way, Pillow's lettering turned with them;
the preview's reading of the figure is unverified until a test print.

### `text`: built-in fonts

| Key                 | Type                      | Default | Meaning                                                                              |
|---------------------|---------------------------|---------|--------------------------------------------------------------------------------------|
| `x`, `y`            | number >= 0               | -       | Lower-left corner; with `align`, `x` is the line's centre or right end instead.      |
| `rotation`          | 0, 90, 180, 270           | 0       | Rotation in degrees.                                                                 |
| `text`              | string, 1-255 chars       | -       | The text; may contain `{name}` placeholders.                                         |
| `font`              | integer 0-9               | 2       | Fonts 0-8 are fixed bitmapped fonts; 9 is CG Triumvirate in point sizes.             |
| `points`            | integer 4-72              | none    | Point size for font 9. A size the model lacks is replaced by the nearest one it has. |
| `font_id`           | three characters          | none    | A downloaded or cartridge font by DPL id (`100`-`999`, `B04` ...); needs `font` 9.   |
| `width_multiplier`  | integer 1-24              | 1       | Horizontal scale of fonts 0-8.                                                       |
| `height_multiplier` | integer 1-24              | 1       | Vertical scale of fonts 0-8.                                                         |
| `attribute`         | 1, 2, 3, 5                | none    | Attribute for this field only.                                                       |
| `counter`           | object                    | none    | Make the field count from label to label, see below.                                 |
| `align`             | `left`, `center`, `right` | `left`  | Where `x` sits on the printed line: its left end, its centre or its right end.       |

Font 9 needs `points` or `font_id`; the other fonts take neither. Fonts 3 to 6 print capitals
only, font 7 is OCR-A and font 8 OCR-B. Characters above ASCII follow code page 437 in fonts 1 and
2 and code page 850 in font 9 (German umlauts and sharp s included in all three; font 9 adds the
accented letters such as `Á` and signs such as `©`); fonts 3 to 6 print capital umlauts and sharp
s; a character a font cannot print becomes `?` with a warning.

#### Alignment and text widths

DPL has no justification: a field is placed at its lower-left corner. For `"align": "right"` the
encoder moves the field back from `x` by the width the line prints, so the line ends at `x`; for
`"center"` by half of it. The width is worked out per record, after `{name}` placeholders are
filled, so `{"text": "{qty} STK", "align": "right"}` ends at the same `x` whatever the quantity.
It is rounded to whole DPL units (on an Allegro 2, 1/100 in, 2 dots). A line that would start left
of the label's left edge is refused (`text_past_left_edge`, measured from that edge wherever
`offset_across` puts it on the printhead). It names the smallest move of `x`, in 0.1 mm steps,
that puts the line on the label, tried through the same conversion and rounding `x` takes; when a
negative `offset_across` puts that edge left of the printhead's first dot, the move to that dot is
named too. Moving `x` right by the larger move named gives a line that starts on the label and the
printhead; a line wider than the printable width must be shortened instead, since it still ends
past that width (`outside_print_width`). A line on the label but left of the printhead's first dot
is reported like a left-aligned field that offset moves there (`offset_past_label_edge`, naming
the smallest move of `x` in the label's unit that clears it). Either is measured from where `x`
lies, even left of the printhead, and a field gets one of them. A refused line is still encoded,
at the later of the label's left edge and the printhead's first dot, so one wider than the label
also gets `element_past_label_edge`. A label rendered without `--data` prints each placeholder
field as its template, braces and all, and is not refused for that template's width: only filled
records are measured. `x` itself is checked against the printable width like any field's start
(`outside_print_width`).

- Fonts 0-8 are measured by their character cells (dpl-reference.md, section 6): exact, and
  confirmed on paper (Allegro 2: right-aligned lines in each font ended on their mark). The gap
  after the last character prints nothing and is left out, so the last character's cell ends at
  `x`; the preview, the label edge check and the overlap checks end the line there too, aligned or
  not.
- Font 9 is the resident CG Triumvirate Bold (manual, printed page 174). The manual gives no
  widths. The capitals, the digits, `,` `-` `.` `/` and the space are measured on paper (Allegro
  2, firmware CA02 03.04, at 12 and 30 pt;
  `docs/measurements/2026-11-21-allegro-2-CA02-03.04-font-9-widths`): a right-aligned line of them
  ends within about 0.35 mm of `x` there. Every other character takes Helvetica-Bold's published
  width, a stand-in that is unverified: the lower-case alphabet printed about 9% narrower and
  `+,-./:;<=>?` about 22% wider than it predicts, so a line holding them can end millimetres from
  `x`. Sizes from 14 to 24 pt and other models are unmeasured. A font 9 line ends at its last
  glyph's ink, and in the preview, the label edge check (`element_past_label_edge`) and the
  overlap checks it is as long as these widths say, aligned or not.
- An aligned field must be upright (rotation 0) and take no `counter` (the printer changes the
  number, and so the width, from label to label), no `font_id` (a downloaded font's widths are not
  known) and, in font 9, no `width_multiplier`; each is refused when the label is read.

```json
{
  "unit": "mm", "width": 100, "height": 55,
  "elements": [
    {"type": "text", "x": 95, "y": 40, "text": "{qty} STK", "font": 9, "points": 30, "align": "right"},
    {"type": "text", "x": 50, "y": 5, "text": "centred", "font": 2, "align": "center"}
  ]
}
```

`counter`:

| Key            | Type                          | Default | Meaning                                                |
|----------------|-------------------------------|---------|--------------------------------------------------------|
| `step`         | integer 1-99                  | 1       | Amount added (or subtracted) per label.                |
| `decrement`    | boolean                       | false   | Count down instead of up.                              |
| `alphanumeric` | boolean                       | false   | Count letters too, not only digits.                    |
| `fill`         | one printable ASCII character | `0`     | Character that pads the field on the left as it grows. |

```json
{
  "elements": [
    {"type": "text", "x": 5, "y": 30, "text": "Part no.", "font": 1},
    {"type": "text", "x": 5, "y": 20, "text": "Gr\u00fcn 24 pt", "font": 9, "points": 24},
    {"type": "text", "x": 5, "y": 5, "text": "000001", "font": 2, "width_multiplier": 2, "counter": {"step": 1}}
  ]
}
```

### `scalable_text`: outline fonts

| Key             | Type                | Default | Meaning                                                                |
|-----------------|---------------------|---------|------------------------------------------------------------------------|
| `x`, `y`        | number >= 0         | -       | Lower-left corner.                                                     |
| `rotation`      | 0, 90, 180, 270     | 0       | Rotation in degrees.                                                   |
| `text`          | string, 1-255 chars | -       | The text; may contain `{name}` placeholders.                           |
| `font_id`       | `S` plus two chars  | `S00`   | Scalable font id; `S00` is the resident CG Triumvirate Bold Condensed. |
| `height_points` | number 4-999        | -       | Character height in points.                                            |
| `width_points`  | number 4-999        | none    | Character width in points; defaults to the height.                     |
| `attribute`     | 1, 2, 3, 5          | none    | Attribute for this field only.                                         |

The characters follow the label's `symbol_set`. On a model without scalable fonts the text is
printed in font 9 at the nearest point size.

```json
{
  "symbol_set": "PM",
  "elements": [{"type": "scalable_text", "x": 5, "y": 10, "text": "SALE", "height_points": 48, "width_points": 36}]
}
```

### `line`

| Key         | Type        | Meaning      |
|-------------|-------------|--------------|
| `x`, `y`    | number >= 0 | Start point. |
| `x2`, `y2`  | number >= 0 | End point.   |
| `thickness` | number > 0  | Line width.  |

Horizontal and vertical lines print on every model. A slanted line needs circle and polygon
support; elsewhere it is drawn as a bitmap image.

A horizontal or vertical line is a filled rectangle whose lower-left corner is the line's lower or
left end: a horizontal line grows UP from `y` by its thickness, a vertical one RIGHT from `x`, so
the thickness is not centred on the line. `preview` draws it the same way. A slanted line is
centred on the path from start to end.

```json
{
  "elements": [
    {"type": "line", "x": 2, "y": 25, "x2": 98, "y2": 25, "thickness": 0.4},
    {"type": "line", "x": 50, "y": 2, "x2": 50, "y2": 24, "thickness": 0.4},
    {"type": "line", "x": 2, "y": 2, "x2": 48, "y2": 24, "thickness": 0.3}
  ]
}
```

### `box`

| Key              | Type        | Default     | Meaning                  |
|------------------|-------------|-------------|--------------------------|
| `x`, `y`         | number >= 0 | -           | Lower-left corner.       |
| `width`          | number > 0  | -           | Width.                   |
| `height`         | number > 0  | -           | Height.                  |
| `thickness`      | number > 0  | -           | Top and bottom edge.     |
| `side_thickness` | number > 0  | `thickness` | Left and right edge.     |
| `fill`           | 0-11        | 0           | Fill pattern, see below. |

```json
{
  "elements": [
    {"type": "box", "x": 2, "y": 2, "width": 96, "height": 46, "thickness": 0.5},
    {"type": "box", "x": 10, "y": 10, "width": 20, "height": 10, "thickness": 0.3, "side_thickness": 0.6, "fill": 4}
  ]
}
```

Fill patterns (for `box`, `circle` and `polygon`): 0 none, 1 solid, 2 6 %, 3 12 %, 4 25 %,
5 38 %, 6 50 %, 7 diamonds, 8 circles, 9 right diagonal, 10 left diagonal, 11 grid.

### `circle`

| Key      | Type        | Default | Meaning       |
|----------|-------------|---------|---------------|
| `x`, `y` | number >= 0 | -       | Centre.       |
| `radius` | number > 0  | -       | Radius.       |
| `fill`   | 0-11        | 0       | Fill pattern. |

```json
{
  "elements": [{"type": "circle", "x": 20, "y": 20, "radius": 8, "fill": 1}]
}
```

### `polygon`

| Key      | Type                      | Default | Meaning                                                                                                            |
|----------|---------------------------|---------|--------------------------------------------------------------------------------------------------------------------|
| `points` | list of 2-60 `{"x", "y"}` | -       | Vertices; the last one connects back to the first. More than 60 need `[printer] max_polygon_points` (up to 10000). |
| `fill`   | 0-11                      | 0       | Fill pattern.                                                                                                      |

```json
{
  "elements": [
    {"type": "polygon", "points": [{"x": 10, "y": 5}, {"x": 40, "y": 5}, {"x": 25, "y": 30}], "fill": 11}
  ]
}
```

### `barcode`

| Key              | Type                 | Default | Meaning                                                                                                                                                                         |
|------------------|----------------------|---------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `x`, `y`         | number >= 0          | -       | Lower-left corner.                                                                                                                                                              |
| `rotation`       | 0, 90, 180, 270      | 0       | Rotation in degrees.                                                                                                                                                            |
| `symbology`      | name, see below      | -       | Barcode type.                                                                                                                                                                   |
| `data`           | string, 1-3000 chars | -       | The data; may contain `{name}` placeholders. At most 255 characters, 3000 for PDF417; for PDF417 and DataMatrix the record's parameter header (8 and 10 characters) counts too. |
| `height`         | number > 0           | none    | Bar height; none uses the printer's default.                                                                                                                                    |
| `narrow_dots`    | integer 0-24         | 0       | Narrow bar width in dots; for module codes (Code 128, EAN, UPC, 2-D) the module width.                                                                                          |
| `wide_dots`      | integer 0-24         | 0       | Wide bar width in dots for ratio codes; ignored for module codes.                                                                                                               |
| `human_readable` | boolean              | true    | Print the text line under the bars where the symbology has one.                                                                                                                 |
| `pdf417`         | object               | none    | PDF417 parameters, see below.                                                                                                                                                   |
| `datamatrix`     | object               | none    | DataMatrix parameters, see below.                                                                                                                                               |
| `counter`        | object               | none    | Count the data from label to label, as for `text`.                                                                                                                              |

`narrow_dots` and `wide_dots` both 0 ask for the printer's default bar widths. Models without
default widths get narrow 2 dots and ratio 3:1 instead. Data is checked before anything is sent:
the character set, the length and any check digit. Where the printer computes the check digit,
the info finding `check_digit_added` names it. Every symbology refuses `<SOH>` (0x01), `<STX>`
(0x02), `<ESC>` (0x1B) and `<CR>` in its data, whatever its character set says: the printer would
read them as commands or as the end of the record, so binary data holding them is not supported.

| `symbology`                | Data                                                                                                                       |
|----------------------------|----------------------------------------------------------------------------------------------------------------------------|
| `code39`                   | 0-9 A-Z space - . $ / + % *                                                                                                |
| `upc_a`                    | 11 digits, or 12 with check digit; a `V` in place of the digit at position 6 or 7 asks the printer for a price check digit |
| `upc_e`                    | 6 digits, or 7 with check digit                                                                                            |
| `i2of5`                    | digits                                                                                                                     |
| `code128`                  | ASCII 32-127, optional subset prefix, `&` escapes                                                                          |
| `ean13`                    | 12 digits, or 13 with check digit; a `V` in place of the digit at position 7 or 8 asks the printer for a price check digit |
| `ean8`                     | 7 digits, or 8 with check digit                                                                                            |
| `hibc`                     | Code 39 characters, leading `+` from the host                                                                              |
| `codabar`                  | 0-9 - $ : / . + with start/stop A-D                                                                                        |
| `i2of5_mod10`              | digits                                                                                                                     |
| `plessey`                  | 1-14 digits                                                                                                                |
| `i2of5_bearer`             | 13 digits                                                                                                                  |
| `addon2`                   | 2 digits                                                                                                                   |
| `addon5`                   | 5 digits                                                                                                                   |
| `code93`                   | 0-9 A-Z - . $ / + % space                                                                                                  |
| `postnet`                  | 5, 9 or 11 digits; no text line                                                                                            |
| `ucc_ean128`               | 19 digits                                                                                                                  |
| `ucc_ean128_kmart`         | 18 digits                                                                                                                  |
| `ucc_ean128_random_weight` | 34 or more digits                                                                                                          |
| `telepen`                  | ASCII 0-127                                                                                                                |
| `maxicode`                 | ASCII 0-127, up to 84 characters                                                                                           |
| `fim`                      | one of A B C D                                                                                                             |
| `pdf417`                   | 8-bit characters, up to 2992 (3000 less the 8-character parameter header)                                                  |
| `datamatrix`               | 8-                                                                                                                         |