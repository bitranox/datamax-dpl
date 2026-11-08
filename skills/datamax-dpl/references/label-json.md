# Label JSON: fonts, QR Codes and overlapping fields

A page of this skill: `SKILL.md`, one folder up, has the workflow and the index of these pages.

## Fonts

- Fonts 0-8 are fixed bitmaps scaled by `width_multiplier`/`height_multiplier`; 3-6 print
  capitals only. Font 9 needs `points`. Umlauts work in fonts 1, 2 and 9, capital umlauts and
  sharp s also in 3-6; the euro sign in none.

## QR Codes

- `{"type": "qr", "x": 3, "y": 3, "data": "{url}", "error": "M", "module_dots": 3, "quiet_zone": 4}`
  (defaults shown; `x`/`y` is the quiet zone's lower-left corner; no rotation). No model has a QR
  record, so it is downloaded as an image; the Prodigy and Prodigy Plus refuse it with
  `image_module_unknown` until `image_module` names the module fitted. Refused:
  `qr_data_too_long`, `qr_past_print_width`; warning `qr_dot_size_not_one` (a `dot_size` other than
  1x1). `module_dots` is printer dots per module, `quiet_zone` modules on every side. Versions 1-40
  all work and the smallest that holds the data is used; there is no version key. At 203 dpi (8
  dots per mm) and level `M`, symbol alone: V10 holds 213 bytes, 21.5 mm at 3 dots or 28.5 mm at 4;
  V20 holds 666 bytes, 36.5 mm or 48.5 mm; V40 holds 2331 bytes, 66.5 mm or 88.5 mm. The default
  quiet zone adds 3 mm in total to the width and to the height at 3 dots, 4 mm at 4 dots. Text is
  UTF-8, so `ä` takes 2 bytes and `€` 3. On an Allegro 2 one phone read V13 at 3 dots and codes up
  to V20 at 4 dots fine and struggled with V29 at 3 dots and V40 at 2 dots; that is one phone, not
  a limit, so test denser codes with the real reader. Nothing checks a QR Code against the label
  height: a code too tall for the label gets no finding. `"micro": true` allows a Micro QR where the
  data fits one (else a full code), `"boost_error": true` raises the level where the version has
  room; both default `false`. More sizes:
  <https://github.com/bitranox/datamax-dpl/blob/HEAD/docs/label-spec.md>.

## Overlapping fields

- Overlapping fields combine by the label's `attribute` (1 XOR, 2 transparent, 3 opaque; a text
  field's own `attribute` overrides it; XOR when unset). Models without the `A` command
  (`allegro`, `allegro-2`, `prodigy`, `prodigy-152`, `prodigy-plus`, `prodigy-plus-152`,
  `generic`) always XOR: dots two fields share print WHITE, so a box over an image of the same
  size shows only what differs. `preview` draws whichever applies; `validate` reports an overlap
  only for `"attribute": 2` on a model without `A` (below), so look at the preview.
- There, `"attribute": 2` merges overlapping lines, boxes, circles, polygons, QR Codes and images
  with a `bitmap` into one downloaded image (`transparency_emulated`; it counts against the image
  memory budget); so does a stored image (an `image` without `bitmap`) whose name another element
  downloads, as that bitmap. Text, barcodes and other stored images cannot be merged and still XOR
  (`transparency_not_emulated`). `strict` refuses both.
  Unverified on paper; otherwise keep fields apart.
