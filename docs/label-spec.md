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

| Key                 | Type                                   | Default | Meaning                                                                                               |
|---------------------|----------------------------------------|---------|-------------------------------------------------------------------------------------------------------|
| `unit`              | `mm`, `in`, `dots`                     | `mm`    | Unit of every length in the label.                                                                    |
| `width`             | number > 0                             | none    | Label width. Wider than the model's print width gives a warning. The preview uses it as canvas width. |
| `height`            | number > 0                             | none    | Label length in feed direction. Used by the preview and as the length on continuous media.            |
| `quantity`          | integer 1-9999                         | 1       | Number of labels to print (`Qnnnn`).                                                                  |
| `elements`          | list, 1 to 2048                        | -       | The fields of the label, described below.                                                             |
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
| `stop_position`     | number > 0, or `"printer"`             | none    | Feed this far past the start of print; backed up on the next job (`<STX>f`). `"printer"` sends none.  |
| `present_distance`  | number > 0                             | none    | Distance to present a label for removal (`<STX>Kf`).                                                  |
| `pause_each_label`  | boolean                                | false   | Pause after each label (`<STX>J`). The setting stays on until the printer is reset.                   |
| `count_by`          | integer 1-99                           | none    | Count-by amount (`^nn`) for counters.                                                                 |
| `plain_zero`        | boolean                                | false   | Print 0 without the slash in fonts 0-8 and barcodes (`z`).                                            |

A `stop_position` of `"printer"` sends no `<STX>f`: the printer keeps its own setting, and the
configured stop and the model's default are not sent either. The one exception is a
`present_distance` on a model without `<STX>Kf`, which still goes out as `<STX>f`
(`present_distance_unsupported`). How `stop_position` and `present_distance` rank against the
configured stop positions and the model's default, and what a model without `<STX>f` or `<STX>Kf`
does with them, is in the README under [Where the label stops](../README.md#where-the-label-stops).

Where fields overlap, the attribute decides what prints, and `preview` draws the same: 1 XOR
prints only the dots an odd number of fields share, 2 transparent prints both, 3 opaque lets the
later field whiten the rectangle it covers. Inverse (5) prints white on black, which the preview
does not draw; it shows as XOR there. With Pillow (the `preview` extra) text combines by its
letters' dots, as an Allegro 2 printed a line through text with gaps only under the strokes;
without it by its whole character cells (`preview_text_approximated`). A text or scalable text
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

A rotated field turns clockwise about its reference point, which stays where `x` and `y` put it,
as the manual's sample label prints ROTATION 1 to 4 from one point (Figure 7-1): 0 runs right and
grows up, 90 runs down and grows right, 180 runs left and grows down, 270 runs up and grows left.
`preview` draws text, scalable text and barcodes that way, Pillow's lettering turned with them;
the preview's reading of the figure is unverified until a test print.

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
and 9 (German umlauts and sharp s included); fonts 3 to 6 print capital umlauts and sharp s; a
character a font cannot print becomes `?` with a warning.

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
| `datamatrix`               | 8-bit characters, up to 245 (255 less the 10-character parameter header); needs the DataMatrix printer option              |

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

| Key             | Type                      | Meaning                                                                                       |
|-----------------|---------------------------|-----------------------------------------------------------------------------------------------|
| `x`, `y`        | number >= 0               | Lower-left corner. DPL images cannot be rotated.                                              |
| `name`          | 1-8 of `A-Z a-z 0-9 _`    | The image's name in the label; the printer name of an image without `bitmap`.                 |
| `bitmap`        | list of strings, optional | Rows, top row first: `1` or `#` is black, `0` or `.` is white. All rows have the same length. |
| `magnification` | integer 1-9, default 1    | Printer dots per image dot, across and along.                                                 |

With `bitmap` the image is downloaded to the printer before the label under its content name, `D`
plus 7 characters from `0-9 A-Z a-z` taken from a hash of its size and bits
([dpl-reference.md](dpl-reference.md), section 5),
never under `name`: the same picture gets the same printer name in every job, so a name the
printer already holds is that very picture, two elements with the same bitmap download it once,
and two elements of one `name` with different bitmaps download both. `name` stays in findings,
and `preview` draws each element's own bitmap. Without `bitmap`, an element whose `name` another
`image` element of the label downloads prints that element's bitmap (the first one's, when several
do). Any other image without `bitmap` must already be stored on the printer under `name`, and the
info finding `image_must_be_stored` says so. A label that downloads `LOGO` therefore does not
store anything under `LOGO`: a later label that names `LOGO` without a bitmap finds only an image
another program stored under that name (the printer lists the job's own images by their content
names, as `datamax-dpl memory` shows). Where the job clears the image module before the label
(`<STX>q`, on the model's built-in RAM with the `[printer]` key `clear_graphics_before` on, the
render option `clear_graphics_before_label` in the library), the clear removes
such a stored image, so the label is refused with `stored_image_cleared`. The clear after each
label (`clear_graphics_after`, the render option `clear_graphics_after_label`) removes it the same
way once the label printed, so in a `--data` batch every record after the first is refused with
`stored_image_cleared` too: keeping a stored image across a batch needs both clear keys off.
Where no clear runs before the downloads, the printer's stored names decide instead: a printer
that already holds a name prints nothing for a second download of it, so `print` reads its names
right before each such label's downloads and leaves out the download of each held one
(`image_name_held`), or keeps every download of a single label when they cannot be read
(`image_names_unread`). A `--data` batch of such labels is refused where no printer is read before
each label: `render`, `validate` with no tcp or serial printer named, and `print` to a file
(`batch_image_names_unread`, from the second record on; the first is judged as a single label is),
unless the clear after each label reaches the module, which removes every name the earlier label
downloaded.
An image with `bitmap` that ends past the model's printable width, its width times its
magnification counted from the column it starts at, is refused with `image_past_print_width`
under both policies; so is a raster fallback or a merged transparency image that does. A stored
image's size is unknown, so it is not checked.

A model may carry an image memory budget, the bytes of dot data one job's downloads may hold (the
Allegro 2 ships 44535; `[printer] image_memory_bytes` sets it, see
[capabilities.md](capabilities.md)). An image without `bitmap` costs nothing. Past the budget,
under `degrade`, every image the job downloads or draws (raster fallbacks) is sent at half
resolution, each 2x2 block of dots becoming one dot that is black if any of the four was, and
placed at double magnification, with the warning `image_halved`; `preview` draws it the same way.
A halved image is sent under the content name of its halved bits, so a printer name always holds
the bits it is named for; an element that names the same image without a `bitmap` in that job is
placed as the halved image too, at double its own magnification. A QR Code is
never halved. A job still past the budget after one halving, or any job past it under `strict`, is
refused with `image_memory_exceeded`, as is an image at `magnification` 5 or more, since doubled it
would pass 9. Unverified on paper: halved images have not been printed yet.

```json
{
  "elements": [
    {"type": "image", "x": 5, "y": 5, "name": "ARROW", "bitmap": ["...##...", "..####..", ".######.", "...##...", "...##..."]},
    {"type": "image", "x": 30, "y": 5, "name": "LOGO"}
  ]
}
```

### `qr`

| Key           | Type                 | Default | Meaning                                                                                                                                     |
|---------------|----------------------|---------|---------------------------------------------------------------------------------------------------------------------------------------------|
| `x`, `y`      | number >= 0          | -       | Lower-left corner of the quiet zone. A QR Code cannot be rotated.                                                                           |
| `data`        | string, 1-4296 chars | -       | The data; may contain `{name}` placeholders.                                                                                                |
| `error`       | `L`, `M`, `Q`, `H`   | `M`     | Error correction level: about 7, 15, 25 or 30 percent of the symbol may be lost.                                                            |
| `module_dots` | integer 1-9          | 3       | Printer dots per module, across and along.                                                                                                  |
| `quiet_zone`  | integer 0-10         | 4       | Modules of white space kept free on every side of the symbol.                                                                               |
| `micro`       | boolean              | `false` | Allow a Micro QR (11 to 17 modules, level `L` to `Q`) where the data fits; a full QR Code otherwise. Many phone readers decode no Micro QR. |
| `boost_error` | boolean              | `false` | Raise the error level as far as the chosen version still holds the data.                                                                    |

No model in the manual has a QR Code record, so datamax-dpl draws the symbol itself and downloads
it as an image before the label, one dot per module, then places it with an image record whose dot
width and height are `module_dots`. The image name follows from its content
([dpl-reference.md](dpl-reference.md), section 5). By default the symbol is a full QR Code and
the error level is used as given: `micro` lets a short text take the smaller Micro QR (`HELLO` at
`M` is 13 modules instead of 21; level `H` and data past a Micro QR's size still give a full one),
and `boost_error` raises the level where the version the data needs has room for it (`HELLO` at
`L` prints as `H`). The preview, the overlap check and the job all draw the same symbol.

The field is `(modules + 2 * quiet_zone) * module_dots` dots square; the module count follows from
the data and the level (21 for a short text at `M`). The symbol starts `quiet_zone` modules in
from `(x, y)`, and nothing is printed in that space, so keep other elements out of it. The quiet
zone counts as part of the field: `(x, y)` follows the same position rules as any other field's
reference point, so a QR at the label edge on a model with a default offset (the Allegro 2 moves
fields 0.25 mm down) is placed on the edge with the warning `model_offset_clamped`, which `strict`
refuses.

| Finding               | Severity | When                                                                   |
|-----------------------|----------|------------------------------------------------------------------------|
| `qr_data_too_long`    | error    | No QR Code version holds the data at that error level.                 |
| `qr_past_print_width` | error    | The field, quiet zone included, ends past the model's printable width. |
| `qr_dot_size_not_one` | warning  | The label's `dot_size` is not 1x1, which prints every module larger.   |

```json
{
  "elements": [
    {"type": "qr", "x": 5, "y": 5, "data": "https://example.com/p/4711", "error": "Q", "module_dots": 4}
  ]
}
```

#### Sizes

Every QR Code version from 1 to 40 is supported at every `module_dots` from 1 to 9. There is no
version key: the smallest version that holds the data is used, and only `qr_data_too_long` and
`qr_past_print_width` refuse a size. This table shows some versions at 203 dpi (8 dots per mm),
error level `M`, with the width of the symbol alone, rounded to 0.5 mm:

| Version | Modules | Bytes at level M | Width at 3 dots | Width at 4 dots |
|---------|---------|------------------|-----------------|-----------------|
| V3      | 29      | 42               | 11 mm           | 14.5 mm         |
| V5      | 37      | 84               | 14 mm           | 18.5 mm         |
| V6      | 41      | 106              | 15.5 mm         | 20.5 mm         |
| V8      | 49      | 152              | 18.5 mm         | 24.5 mm         |
| V10     | 57      | 213              | 21.5 mm         | 28.5 mm         |
| V13     | 69      | 331              | 26 mm           | 34.5 mm         |
| V20     | 97      | 666              | 36.5 mm         | 48.5 mm         |
| V40     | 177     | 2331             | 66.5 mm         | 88.5 mm         |

- **Other versions:** a version has `17 + 4 * version` modules.
- **Quiet zone:** the default of 4 modules on every side adds 3 mm to the width at 3 dots and 4 mm
  at 4 dots, in both directions. Plan the label for that size.
- **Other resolutions:** the width is `modules * module_dots * 25.4 / dpi` mm, so the same
  `module_dots` prints smaller at 300 dpi.
- **Bytes:** byte mode holds text as UTF-8. An ASCII character takes 1 byte, `ä`, `ß` and `°` take
  2, and `€` and CJK characters take 3.
- **Digits and capitals:** data of only digits, or only capitals, digits and ` $%*+-./:`, needs
  less room. V20 at `M` holds 1600 digits or 970 such characters.
- **Other levels:** `L` holds more than the table says, `Q` and `H` less.
- **The `data` limit:** 4296 characters is the most V40 holds at `L` of those capitals and digits.
  Digits alone would fit 7089 but stop at this limit.
- **Image memory:** the job is the same size at 3 and 4 dots, because the symbol is downloaded at
  one dot per module and the printer magnifies it.
- **Prodigy and Prodigy Plus:** these models have no built-in image memory, so a QR Code, like any
  image, is refused with `image_module_unknown` until `image_module` names the module fitted.

Nothing checks that a QR Code fits the label's height: V40 at 4 dots is accepted on a 55 mm label
with no finding.

#### Scanning

Printed on one Allegro 2 on 100x55 mm gap labels, level `M`, quiet zone 4, and read with one
phone. Every label printed complete; only the reading got harder. These are that phone's results,
not limits, and sizes denser than the good ones need a test with the real reader. The side includes
the quiet zone.

| Module size       | Version | Characters | Side     | Phone scan         |
|-------------------|---------|------------|----------|--------------------|
| 3 dots (0.375 mm) | 13      | 300        | 29 mm    | fine               |
| 4 dots (0.5 mm)   | 15      | 400        | 42.5 mm  | fine               |
| 4 dots (0.5 mm)   | 20      | 666        | 52.5 mm  | fine               |
| 3 dots (0.375 mm) | 29      | 1264       | 53 mm    | difficult          |
| 2 dots (0.25 mm)  | 40      | 2331       | 46.25 mm | difficult / failed |

Text outside ASCII was checked with the zxing-cpp decoder library, not with a phone. Other models
have not printed a QR Code.

## Placeholders and data records

Text in `text`, `scalable_text`, barcode `data` and qr `data` may contain `{name}` placeholders.
Names start with a letter or `_` and continue with letters, digits or `_`. `--data` fills them, one
label per record:

- a `.csv` file: the header row names the fields;
- any other file: a JSON array of objects, one JSON object, or JSON lines;
- `-` reads JSON records (array, object or JSON lines) from standard input.

Every value is used as text. Only element text is filled: coordinates and settings cannot come
from a record. Two kinds of problem stop the command with exit 2 before anything is rendered: a
record that lacks a field a placeholder names, and a filled text longer than the label format
allows (255 characters for text, 3000 for barcode `data`, 4296 for qr `data`). A filled value the printer would
reject, such as barcode data with the wrong number of digits or a barcode other than PDF417 past 255 characters, is an
error finding of that record (exit 1), like any other label error.

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
