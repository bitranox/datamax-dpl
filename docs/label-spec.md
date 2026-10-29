# Label format

A label is one JSON document: job settings plus a list of `elements`. The same document drives
`validate`, `render`, `preview`, `print` and `verify`, and the library reads it with
`Label.model_validate()`. The machine-readable JSON Schema comes from the CLI:

```bash
datamax-dpl schema > label.schema.json
```

With `--json`, the schema is wrapped in the usual envelope under `data.label_schema`.

Unknown keys are refused, so a typo is an error rather than a silently ignored setting. Every
example on this page passes `datamax-dpl validate --model dmx-800 --option datamatrix`. A model
that lacks a feature may refuse or replace an element; [capabilities.md](capabilities.md) lists
what happens on which model.

## Units and coordinates

Every length is a number in the label's `unit`:

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

| Key                 | Type                                   | Default | Meaning                                                                                               |
|---------------------|----------------------------------------|---------|-------------------------------------------------------------------------------------------------------|
| `unit`              | `mm`, `in`, `dots`                     | `mm`    | Unit of every length in the label.                                                                    |
| `width`             | number > 0                             | none    | Label width. Wider than the model's print width gives a warning. The preview uses it as canvas width. |
| `height`            | number > 0                             | none    | Label length in feed direction. Used by the preview and as the length on continuous media.            |
| `quantity`          | integer 1-9999                         | 1       | Number of labels to print (`Qnnnn`).                                                                  |
| `elements`          | list, at least one                     | -       | The fields of the label, described below.                                                             |
| `cut`               | object                                 | none    | Cutting, see below. Needs a cutter; `--cutter no` refuses a label that sets it.                       |
| `heat`              | integer 0-30                           | none    | Print head heat (`Hnn`). Above the model's maximum it is lowered to the maximum.                      |
| `print_speed`       | letter `A`-`S`                         | none    | Print speed (`Pa`): `A` = 1.0 inch per second, each letter 0.5 more, `S` = 10.0.                      |
| `slew_speed`        | letter `A`-`S`                         | none    | Feed speed without printing (`Sa`).                                                                   |
| `backup_speed`      | letter `A`-`S`                         | none    | Backfeed speed (`pa`).                                                                                |
| `dot_size`          | object `{"width": 1-2, "height": 1-3}` | none    | Dot size command `Dwh`. Without it the format sends `D11` where the model's default is `D22`.         |
| `column_offset`     | number >= 0                            | none    | Shift every field to the right (`Cnnnn`).                                                             |
| `row_offset`        | number >= 0                            | none    | Shift every field up (`Rnnnn`).                                                                       |
| `attribute`         | 1, 2, 3, 5                             | none    | How fields combine with what is already printed: 1 XOR, 2 transparent, 3 opaque, 5 inverse (`An`).    |
| `mirror`            | boolean                                | false   | Print the label mirrored (`M`).                                                                       |
| `symbol_set`        | two characters `A-Z0-9`                | none    | Symbol set of scalable fonts (`ySxx`), for example `PM` (code page 850) or `W1` (Windows Latin 1).    |
| `media`             | `gap`, `reflective`, `continuous`      | none    | How the printer finds the next label. `continuous` needs `continuous_length` or `height`.             |
| `continuous_length` | number > 0                             | none    | Label length on continuous media.                                                                     |
| `stop_position`     | number > 0                             | none    | Feed this far past the start of print; backed up on the next job (`<STX>f`).                          |
| `present_distance`  | number > 0                             | none    | Distance to present a label for removal (`<STX>Kf`).                                                  |
| `pause_each_label`  | boolean                                | false   | Pause after each label (`<STX>J`). The setting stays on until the printer is reset.                   |
| `count_by`          | integer 1-99                           | none    | Count-by amount (`^nn`) for counters.                                                                 |
| `plain_zero`        | boolean                                | false   | Print 0 without the slash in fonts 0-8 and barcodes (`z`).                                            |

`cut`:

| Key      | Type           | Default | Meaning                                                                                                     |
|----------|----------------|---------|-------------------------------------------------------------------------------------------------------------|
| `every`  | integer 1-9999 | 1       | Cut after every N labels. With 1 no cut command goes into the format; the printer's cutter setting applies. |
| `at_end` | boolean        | true    | Also cut the last group when the quantity is not a multiple of `every` (`<STX>o` after the format).         |

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

### `text`: built-in fonts

| Key                 | Type                | Default | Meaning                                                                              |
|---------------------|---------------------|---------|--------------------------------------------------------------------------------------|
| `x`, `y`            | number >= 0         | -       | Lower-left corner.                                                                   |
| `rotation`          | 0, 90, 180, 270     | 0       | Rotation in degrees.                                                                 |
| `text`              | string, 1-255 chars | -       | The text; may contain `{name}` placeholders.                                         |
| `font`              | integer 0-9         | 2       | Fonts 0-8 are fixed bitmapped fonts; 9 is CG Triumvirate in point sizes.             |
| `points`            | integer 4-72        | none    | Point size for font 9. A size the model lacks is replaced by the nearest one it has. |
| `font_id`           | three characters    | none    | A downloaded or cartridge font by DPL id (`100`-`999`, `B04` ...); needs `font` 9.   |
| `width_multiplier`  | integer 1-24        | 1       | Horizontal scale of fonts 0-8.                                                       |
| `height_multiplier` | integer 1-24        | 1       | Vertical scale of fonts 0-8.                                                         |
| `attribute`         | 1, 2, 3, 5          | none    | Attribute for this field only.                                                       |
| `counter`           | object              | none    | Make the field count from label to label, see below.                                 |

Font 9 needs `points` or `font_id`; the other fonts take neither. Fonts 3 to 6 print capitals
only, font 7 is OCR-A and font 8 OCR-B. Characters above ASCII follow code page 437 in fonts 1, 2
and 9 (German umlauts and sharp s included); a character a font cannot print becomes `?` with a
warning.

`counter`:

| Key            | Type          | Default | Meaning                                                |
|----------------|---------------|---------|--------------------------------------------------------|
| `step`         | integer 1-99  | 1       | Amount added (or subtracted) per label.                |
| `decrement`    | boolean       | false   | Count down instead of up.                              |
| `alphanumeric` | boolean       | false   | Count letters too, not only digits.                    |
| `fill`         | one character | `0`     | Character that pads the field on the left as it grows. |

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

| Key      | Type                      | Default | Meaning                                            |
|----------|---------------------------|---------|----------------------------------------------------|
| `points` | list of 2-60 `{"x", "y"}` | -       | Vertices; the last one connects back to the first. |
| `fill`   | 0-11                      | 0       | Fill pattern.                                      |

```json
{
  "elements": [
    {"type": "polygon", "points": [{"x": 10, "y": 5}, {"x": 40, "y": 5}, {"x": 25, "y": 30}], "fill": 11}
  ]
}
```

### `barcode`

| Key              | Type                 | Default | Meaning                                                                                |
|------------------|----------------------|---------|----------------------------------------------------------------------------------------|
| `x`, `y`         | number >= 0          | -       | Lower-left corner.                                                                     |
| `rotation`       | 0, 90, 180, 270      | 0       | Rotation in degrees.                                                                   |
| `symbology`      | name, see below      | -       | Barcode type.                                                                          |
| `data`           | string, 1-3000 chars | -       | The data; may contain `{name}` placeholders. At most 255 characters except for PDF417. |
| `height`         | number > 0           | none    | Bar height; none uses the printer's default.                                           |
| `narrow_dots`    | integer 0-24         | 0       | Narrow bar width in dots; for module codes (Code 128, EAN, UPC, 2-D) the module width. |
| `wide_dots`      | integer 0-24         | 0       | Wide bar width in dots for ratio codes; ignored for module codes.                      |
| `human_readable` | boolean              | true    | Print the text line under the bars where the symbology has one.                        |
| `pdf417`         | object               | none    | PDF417 parameters, see below.                                                          |
| `datamatrix`     | object               | none    | DataMatrix parameters, see below.                                                      |
| `counter`        | object               | none    | Count the data from label to label, as for `text`.                                     |

`narrow_dots` and `wide_dots` both 0 ask for the printer's default bar widths. Models without
default widths get narrow 2 dots and ratio 3:1 instead. Data is checked before anything is sent:
the character set, the length and any check digit. Where the printer computes the check digit,
the info finding `check_digit_added` names it.

| `symbology`                | Data                                                  |
|----------------------------|-------------------------------------------------------|
| `code39`                   | 0-9 A-Z space - . $ / + % *                           |
| `upc_a`                    | 11 digits, or 12 with check digit                     |
| `upc_e`                    | 6 digits, or 7 with check digit                       |
| `i2of5`                    | digits                                                |
| `code128`                  | ASCII 32-127, optional subset prefix, `&` escapes     |
| `ean13`                    | 12 digits, or 13 with check digit                     |
| `ean8`                     | 7 digits, or 8 with check digit                       |
| `hibc`                     | Code 39 characters, leading `+` from the host         |
| `codabar`                  | 0-9 - $ : / . + with start/stop A-D                   |
| `i2of5_mod10`              | digits                                                |
| `plessey`                  | 1-14 digits                                           |
| `i2of5_bearer`             | 13 digits                                             |
| `addon2`                   | 2 digits                                              |
| `addon5`                   | 5 digits                                              |
| `code93`                   | 0-9 A-Z - . $ / + % space                             |
| `postnet`                  | 5, 9 or 11 digits; no text line                       |
| `ucc_ean128`               | 19 digits                                             |
| `ucc_ean128_kmart`         | 18 digits                                             |
| `ucc_ean128_random_weight` | 34 or more digits                                     |
| `telepen`                  | ASCII 0-127                                           |
| `maxicode`                 | ASCII 0-127, up to 84 characters                      |
| `fim`                      | one of A B C D                                        |
| `pdf417`                   | 8-bit characters, up to 3000                          |
| `datamatrix`               | 8-bit characters; needs the DataMatrix printer option |

The rules come from [dpl-reference.md](dpl-reference.md), section 7. For MaxiCode, datamax-dpl
checks only the character set and the length; the structured message the printer expects is
described in the Datamax manual and is passed through as written.

`pdf417`:

| Key              | Type         | Default | Meaning                             |
|------------------|--------------|---------|-------------------------------------|
| `truncated`      | boolean      | false   | Truncated PDF417.                   |
| `security_level` | integer 0-8  | 2       | Error correction level.             |
| `aspect_ratio`   | integer 0-99 | 0       | Aspect ratio.                       |
| `rows`           | integer 0-90 | 0       | Rows; 0 lets the printer choose.    |
| `columns`        | integer 0-30 | 0       | Columns; 0 lets the printer choose. |

`datamatrix`:

| Key         | Type                     | Default | Meaning                              |
|-------------|--------------------------|---------|--------------------------------------|
| `ecc`       | 0, 50, 80, 100, 140, 200 | 200     | Error correction (ECC 200 is usual). |
| `format_id` | integer 0-6              | 0       | Format id.                           |
| `rows`      | integer 0-144            | 0       | Rows; 0 is automatic.                |
| `columns`   | integer 0-144            | 0       | Columns; 0 is automatic.             |

```json
{
  "elements": [
    {"type": "barcode", "x": 5, "y": 40, "symbology": "ean13", "data": "400638133393", "height": 15},
    {"type": "barcode", "x": 5, "y": 25, "symbology": "code128", "data": "AT0001234567", "height": 10, "narrow_dots": 2},
    {"type": "barcode", "x": 5, "y": 5, "symbology": "code39", "data": "LOT 7", "narrow_dots": 2, "wide_dots": 6, "human_readable": false},
    {"type": "barcode", "x": 60, "y": 30, "symbology": "pdf417", "data": "Shipment 4711", "pdf417": {"security_level": 3, "columns": 4}},
    {"type": "barcode", "x": 60, "y": 5, "symbology": "datamatrix", "data": "SN-000123", "narrow_dots": 4, "datamatrix": {"ecc": 200}},
    {"type": "barcode", "x": 40, "y": 5, "symbology": "maxicode", "data": "MAXICODE TEST"}
  ]
}
```

### `image`

| Key      | Type                      | Meaning                                                                                       |
|----------|---------------------------|-----------------------------------------------------------------------------------------------|
| `x`, `y` | number >= 0               | Lower-left corner. DPL images cannot be rotated.                                              |
| `name`   | 1-8 of `A-Z a-z 0-9 _`    | Image name on the printer.                                                                    |
| `bitmap` | list of strings, optional | Rows, top row first: `1` or `#` is black, `0` or `.` is white. All rows have the same length. |

With `bitmap` the image is downloaded to the printer under `name` before the label. Without it the
image must already be stored on the printer, and the info finding `image_must_be_stored` says so.

```json
{
  "elements": [
    {"type": "image", "x": 5, "y": 5, "name": "ARROW", "bitmap": ["...##...", "..####..", ".######.", "...##...", "...##..."]},
    {"type": "image", "x": 30, "y": 5, "name": "LOGO"}
  ]
}
```

## Placeholders and data records

Text in `text`, `scalable_text` and barcode `data` may contain `{name}` placeholders. Names start
with a letter or `_` and continue with letters, digits or `_`. `--data` fills them, one label per
record:

- a `.csv` file: the header row names the fields;
- any other file: a JSON array of objects, one JSON object, or JSON lines;
- `-` reads JSON records (array, object or JSON lines) from standard input.

Every value is used as text. Only element text is filled: coordinates and settings cannot come
from a record. Two kinds of problem stop the command with exit 2 before anything is rendered: a
record that lacks a field a placeholder names, and a filled text longer than the label format
allows (255 characters). A filled value the printer would reject, such as barcode data with the
wrong number of digits, is an error finding of that record (exit 1), like any other label error.

`{{` and `}}` stand for literal braces when the label is filled from records. Without `--data`
the text is printed exactly as written, `{name}` included, and the warning
`placeholders_unfilled` lists the names.

```json
{
  "elements": [
    {"type": "text", "x": 5, "y": 20, "text": "{name}", "font": 9, "points": 14},
    {"type": "barcode", "x": 5, "y": 5, "symbology": "code128", "data": "{tracking}", "height": 10}
  ]
}
```

```bash
datamax-dpl render examples/shipping-label.json --data examples/shipping-records.json --model pe42
```
