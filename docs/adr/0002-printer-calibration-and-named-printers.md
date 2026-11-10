# ADR 0002: Printer Calibration and Named Printers

**Status:** Accepted

## Context

Measured on an Allegro 2 (203 dpi):

- One printhead dot across the web is 0.125 mm, but one dot row along the feed is about
  0.1221 mm: a 1638-row image printed 200.0 mm where 204.75 mm was nominal. The feed pitch
  depends on the platen roller, so it differs from printer to printer of the same model.
- The printer maps one 1/100 inch to two whole dots on both axes, so a length rendered in
  1/100 in comes out about 1.6 % short across the web (0.250 mm instead of 0.254 mm per unit).
  An image and a box of the same nominal size therefore do not line up.
- A long format is cut off by the printer: the lowest row any test format was cut at is dot row
  1648, about 201 mm at the calibrated `scale_along`.

Users have more than one printer, and each one needs its own correction, its own address and
its own label size. The configuration had a single `[printer]` section.

## Decision

1. **Named printers.** The configuration gains `[printers.<name>]` sections. Each holds the
   keys of `[printer]` (model, uri, firmware, policy, timeout, cutter, options) plus
   `label_width`, `label_height`, `scale_across` and `scale_along`. `[printer]` stays the
   default every named printer inherits. `--printer` takes a name or a URI; a URI is `-` or
   starts with `tcp://`, `serial://`, `device://` or `file:`, so a name may contain neither
   `:` nor be `-`.
2. **Scale factors.** `scale_across` and `scale_along` are printed length divided by nominal
   length (1.0 by default; the measured Allegro 2 printed 0.977 of nominal along the feed
   before calibration, and its model default after calibration is 0.976). They reach the
   renderer through `RenderOptions` and are applied where lengths become dots, so positions,
   line and box lengths, and shapes the library rasterises itself are corrected. Anything sized
   in dots (a user bitmap, a bitmap font) is not.
3. **Whole-dot inch units.** Converting a length to 1/100 in uses the printer's whole-dot unit
   (two dots at 203 dpi) instead of 0.254 mm. Measured on the Allegro 2 only.
4. **Calibration labels.** The library builds two labels, `calibration_labels(...)`:
   - across: a thin frame standing `ACROSS_INSET_MM` (2 mm) in from every edge of a label of the
     given width and height, and a bar across from the left frame line to the right one, with
     a tick every 10 mm; the frame also shows whether the print is shifted on the label;
   - along: a bar down the feed with a tick every 10 mm, crossing the label gaps and measured
     with the labels still on the liner. Its length is the longest whole 10 mm, at most 200 mm,
     one format holds at the printer's current calibration: 190 mm on an Allegro 2 at
     `scale_along` 0.976, where 200 mm needs row 1653, past the 1648-row limit. A shorter length
     can be asked for; a longer one than a format holds is refused.
   Each label prints its printer name, nominal length and the factor in use.
5. **CLI.** `datamax-dpl calibrate print --printer NAME` prints both labels with the printer's
   current factors. `datamax-dpl calibrate set --printer NAME --along MM [--across MM]` sets
   the new factor to `old x measured / nominal` and writes it into `[printers.NAME]` in the
   user configuration file, keeping its comments. It refuses a printer given only as a URI and
   warns when a `.env` file or an environment variable still overrides the value it wrote.
   Printing the labels again afterwards should measure the nominal lengths.

## Consequences

- A second calibration round corrects the first, because the labels print with the factors
  already in use.
- The CLI writes a configuration file for the first time; everything else still only reads.
- `--printer` has two meanings, told apart by the URI prefixes above.
- The along label uses several labels per print.
- The 1648-row cutoff and the whole-dot inch unit are measured on one Allegro 2; other models
  are unverified.
