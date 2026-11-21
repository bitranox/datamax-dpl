# Font 9 widths on paper: Allegro 2, firmware CA02 03.04, 2026-11-21

Paper measurements of the resident font 9 (CG Triumvirate Bold) on one Datamax Allegro 2 (203 dpi,
firmware CA02 03.04), printed and read on 2026-11-21. They replaced the Helvetica-Bold stand-in in
`src/datamax_dpl/domain/text_metrics.py` for the characters they cover. Nothing here was measured
on another model, another firmware or another resolution.

## Method

Every line repeats one character N times and ends with an `M` (a crisp vertical stem), right-aligned
at a printed vertical reference line: 95 mm on the labels in `chars/` and `calib/`, 85 mm on the
labels in `next/`. The labels are 100 x 55 mm. Each line's number sits on its own small line above
it. The reading is the gap between the line's last ink and the reference line, judged by eye to
0.1 mm: `S<mm>` short of the line (the text printed narrower than predicted), `O<mm>` over it
(wider), `O?` ran off the label edge, a `?` an uncertain reading. Lines of one character attribute
the error to that character; the line of `M` alone gives the error of the final `M`.

- `calib/`: the first calibration set (labels 02-04 of five: lines mixing many characters at 12 pt,
  and `Wi1.0M` at 8, 18, 30 and 48 pt). Readings in `calib/readings.txt`, lowest line first.
- `chars/`: lines 1-41, 12 pt, at 95 mm: `M`, the digits, `,` `.` `/` `-` space, `A`-`Z`.
- `next/`: lines 42-58, at 85 mm (so an overrun stays on the label): 12 pt `M` `,` `.` `/` space,
  then 30 pt `M` `0` `5` `,` space `/` `S` `T` `K` `B` `O` `X`.
- `vline/`: the print shift (below): `v01-font9-vline.json` and the two reprints moved by the
  shift (`v02`, `v03`), the label's `README.txt` and the readings of all three in `readings.txt`.
- `fill/`: the fill-and-V round (below), 35 labels `f01`-`f35` with 164 rows at 12, 18, 24 and
  30 pt, the round's `README.txt` (row, size, character, N) and the readings in `readings.txt`.
- `fill.json`: one record per fill row: label, size, character, N, the reading in lines, the column
  of the text record and of the middle vertical line (DPL units of 2 dots), and the advance that
  follows (`advance_dots`, below) with its resolution, 2 dots / N.
- `lines.json`: one record per line: label, text, size, the column its record was written at
  (`text_column`) and the reference line's column (`line_column`), both in DPL units of 2 dots, the
  reading and its quality, and the ink length that follows: `2 * (line_column - text_column) -
  reading_mm * 203 / 25.4` dots, short readings positive. `characters` says whether every character
  of the line is now measured.
- `scripts/`: the generators of the labels (`gen_calib`, `gen_chars`, `gen_next`, `gen_vline`,
  `gen_fill`), the first per-line calculators (`calc_chars`, `calc_next`), `fit_data` (writes
  `lines.json`), `fit_widths` (the fit below), `fit_fill` (writes `fill.json` and the per-size
  tables) and `gen_bearings` (the left and right side bearings from Adobe's `phvb8a.afm`;
  the right ones are checked on paper, the left ones, which centre a line, are not). They are stored
  as `.py.txt` so the test and type gates leave them alone; copy one to `.py` beside the files it
  reads to run it. The generators and `fit_data` place lines with the stand-in table, so they
  reproduce the printed labels and columns only with a datamax-dpl that still has it.

## Print shift

A line's end cannot tell the widths from a constant offset of where the printer starts the line,
so that offset was measured apart, on the same printer and day, with the V-line label in `vline/`:
a lone right-aligned `M` per size (30, 24, 18 and 12 pt) at x 50 and x 95, and five vertical lines
0.5 mm apart around the predicted middle of the `M`, where its diagonals meet. The printer prints
dots shared by text and a line white, so the line shows where it crosses the ink. The reading is
how far the `M` must move to put its V on the middle line, in lines of 0.5 mm, `+` to the right.
The label was reprinted with each `M` moved left in the label by a trial shift until every reading
was zero:

| Round | Shift in the label (dots)  | Readings, lines 1-8 (30, 24, 18, 12 pt at x 50 and x 95) |
|-------|----------------------------|----------------------------------------------------------|
| 1     | none                       | -1 -1 -0.5 -0.5 -0.25 -0.25 -0.5 -0.5                    |
| 2     | 30: 4, 24: 2, 18: 0, 12: 2 | 1 1 1 1 0 0 1 1                                          |
| 3     | 30: 2                      | 0.5 0 0 0 0 0 0 0                                        |

Result: font 9 prints 2 dots right of where its record starts at 30 pt, and at its record at 24,
18 and 12 pt. Fields move in units of 2 dots, so line 1's half line (half a unit) cannot be taken
out while line 2 reads 0; the same line read -0.5 and 0 in two rounds at 24 and 12 pt, so one
reading varies by about half a line. The same shift at x 50 and x 95 makes it an offset, not a
scale. datamax-dpl holds it as the `allegro-2` profile's `font9_print_shift` and writes every
upright font 9 field that much further left.

## Model

A character advances by `width * em / 1000` dots, the em being `points * 203 / 72` dots.

- At 12 pt each advance is rounded to whole dots. With the right side bearing of `M` from the AFM
  file (68/1000 em, 2.3 dots), every 12 pt line of one character gives an advance within 0.05 dot of
  a whole number.
- At 30 pt the exact advances accumulate and only the position each character reaches is rounded.
  Whole-dot advances do not fit there: with the print shift taken off, the `M` line would need its
  ink to end 2 dots (70 dots per `M`) or 11 dots (71 per `M`) before the last advance, 0.47 or
  0.66 mm away from the AFM bearing (5.75 dots), while exact advances fit with the AFM bearing as
  it is.
- 18 and 24 pt, measured in the fill-and-V round (below), follow the 30 pt rule, not the 12 pt one.
  13 to 17 pt are unmeasured; datamax-dpl rounds whole dots up to 12 pt and positions above it.
- The ink ends before the last advance by that glyph's right side bearing, rounded to whole dots.
- A line starts at its record's column plus the print shift (2 dots at 30 pt, none at 12 pt), so
  the fit takes the shift off each 30 pt line before it reads the widths.
- `/` is the one measured character whose 12 pt bitmap rounds against its design width: it
  advances 11 dots at 12 pt (line 45, `O7.5`), while at 30 pt it is exactly as wide as `,` and the
  space (lines 50-52, `O2` each), which gives 10 dots at 12 pt. datamax-dpl keeps 11 dots as a
  12 pt exception.

Widths: a character measured at 30 pt takes its 30 pt width; one measured only at 12 pt the middle
of the widths that round to its 12 pt advance, which leaves +/- 15/1000 em open at other sizes. The
ten digits share one width (the mean of `0` and `5` at 30 pt: 540.1), as the design does and as all
ten measured at 12 pt (18 dots); the period takes the comma's width (the same in the design, and
10 dots each at 12 pt).

The 30 pt lines fit by construction: each character comes from its own line. What tests the model
across sizes are the 12 pt lines of the characters measured at 30 pt (`M`, the digits, `,` `.`
space `/` `S` `T` `K` `B` `O` `X`), all within -0.18 to +0.29 mm.

Precision: the two lines of `M` alone at 12 pt, on different labels, come out 0.3 mm apart (-0.05
and +0.25 mm), and the reference line itself is 0.25 mm wide. The test
(`tests/test_font_9_paper_widths.py`) allows 0.35 mm on every line whose characters are all
measured; the largest residual is 0.32 mm (line 38, `W`). The lines that ran off the label must be
predicted past its 100 mm edge.

Not measured: the lower case, the other punctuation and every byte above 127. They keep the
Helvetica-Bold widths, and the lines mixing them are off by up to 5.4 mm (the lower case 5.4 mm too
wide, `+,-./:;<=>?` 4.25 mm too narrow).

## Fill-and-V round: 18, 24 and 30 pt

Every row of the labels in `fill/` repeats one character N times, as many as fit in 89 mm (N 8 to
76), and ends in an `M`, right-aligned at x 95 mm. Five vertical lines 0.5 mm apart cross the final
`M`, the middle one at the predicted middle of the `M`'s ink, where its diagonals meet, as in
`vline/`. The reading is how many lines the `M` must move to sit on the middle line, `+` to the
right, so a positive reading means the N characters ran short. One line is 0.5 mm, 4 dots; the
readings come in half lines. All 41 characters measured before were printed at 12, 18, 24 and
30 pt, one size per label, from the build of commit 83272f6, which placed 18 and 24 pt from the
widths above scaled by size. The printed labels carry no name of their own: `gen_fill` prints one
(`f01 12pt`) since this round, so the stored labels lack it.

`fit_fill` reads both columns from the rendered records, never from x. The N characters reach

    reached = 2 * line_column + 1 - 4 * reading - 2 * text_column - shift - 0.417 * em

dots: a line record is one unit (2 dots) wide, so its middle lies 1 dot right of its column; the
shift is the print shift above (2 dots at 30 pt, none below); the `M`'s ink middle lies
(69 + 765) / 2 / 1000 = 0.417 em right of its origin (AFM). Each character advances reached / N, to
2 dots / N, half a line over N characters: 0.03 to 0.25 dot.

- 12 pt: all 41 rows read 0, and every advance lies within its resolution of the whole-dot width
  datamax-dpl already uses. Under the model the rows reach 0.74 dot further than those widths on
  average (-0.11 to +0.89), inside a half line. This round therefore cannot see the 0.17 mm by which
  the 12 pt line ends of `chars/` and `next/` fall short (residual table below: 39 of 44 positive),
  and what it shows points the other way, so it gives no support to a width change. The 12 pt widths
  stay as they are, and that bias stays unexplained.
- Whole dots do not hold above 12 pt: 23 of 41 advances at 18 pt and 33 of 41 at 24 pt lie within
  their resolution of a whole number, against 41 of 41 at 12 pt and 3 of 41 at 30 pt. At 18 pt `U`
  (37.52 dots), `W` (46.56), `P` (32.69) and `Y` (34.20) lie clearly between two. Exact advances
  with rounded positions, as at 30 pt, hold from 18 pt up.
- The widths do not scale across sizes: against the build's scaled widths, 28 of 41 characters at
  18 pt and 29 of 41 at 24 pt read outside their resolution, `/` at 18 pt by 1.07 dots per
  character (15.95 against 14.88). Each measured size keeps its own table.
- 30 pt: this round agrees with the widths above on all 21 characters they give at 30 pt (`M`, the
  digits, `,` `.` `/` space, `S` `T` `K` `B` `O` `X`): 20 read 0 and lie within this round's
  resolution, and `S` (0.21 dot) within the two rounds' combined resolution (0.26). Those 21 keep
  the widths above, whose line ends were read to 0.1 mm over 9 to 25 characters, finer than half a
  line over 8 to 28. The other 20 take this round's value.
- The `M` rows: 41.99 dots at 18 pt (resolution 0.13; the build had 42.25), 56.80 at 24 pt (0.18;
  56.33), 70.41 at 30 pt (0.22; 70.42).
- Label f23 (24 pt `U` `V` `W` `X` `Y`) read `2 0 -4 -1 3`, exactly the readings of label f13 (18 pt
  `P` `Q` `R` `S` `T`), and its `W` (63.68 dots) fits neither the 18 nor the 30 pt width scaled. Its
  five rows are left out: those characters take the 24 pt advance interpolated per point between
  18 and 30 pt (`U` 49.24, `V` 43.94, `W` 62.03, `X` 43.95, `Y` 45.55). That is unverified and only
  approximate: `A`, measured at all three sizes, lies 1.07 dots off its own interpolation at 24 pt.

datamax-dpl uses the result per printed size: a measured character at 12, 18, 24 or 30 pt takes its
advance from this record at that size, and at any other size the advance of the nearest measured
size, scaled by size, which is unverified. The other characters keep the stand-in.

## Residual of every line

Predicted minus measured end of the line, in mm, the print shift included: positive when the
prediction runs longer than the print.

| Line         | Size  | Text              | Reading | Residual (mm)                              | Characters                         |
|--------------|-------|-------------------|---------|--------------------------------------------|------------------------------------|
| calib 02, 1  | 12 pt | `abcdefghijkl...` | S5.5    | +5.37                                      | some unmeasured                    |
| calib 02, 2  | 12 pt | `ABCDEFGHIJKL...` | S1.8    | -0.08                                      | measured                           |
| calib 02, 3  | 12 pt | `0123456789`      | S1.2    | -0.18                                      | measured                           |
| calib 02, 4  | 12 pt | ` !"#$%&'()*`     | 0?      | -0.13                                      | some unmeasured, reading uncertain |
| calib 02, 5  | 12 pt | `+,-./:;<=>?`     | O4.5    | -4.25                                      | some unmeasured                    |
| calib 02, 6  | 12 pt | `@[\]^_'{\|}~`    | O1.5    | -1.63                                      | some unmeasured                    |
| calib 02, 7  | 12 pt | `Çüéâäàåçêëèï`    | S1      | +1.25                                      | some unmeasured                    |
| calib 02, 8  | 12 pt | `îìÄÅÉæÆôöòûù`    | S1.5    | +1.25                                      | some unmeasured                    |
| calib 02, 9  | 12 pt | `ÿÖÜø£Ø×ƒáíóú`    | S0.2    | -0.05                                      | some unmeasured                    |
| calib 02, 10 | 12 pt | `ñÑªº¿®½¼¡ÁÂÀ`    | S1      | +0.75                                      | some unmeasured                    |
| calib 03, 1  | 12 pt | `©¢¥ãÃðÐÊËÈıÍ`    | S2.8    | +3.05                                      | some unmeasured                    |
| calib 03, 2  | 12 pt | `ÎÏÌÓßÔÒõÕµþÞ`    | S1      | +0.87                                      | some unmeasured                    |
| calib 03, 3  | 12 pt | `ÚÛÙýÝ±¾÷¸°¨·`    | O3?     | -3.38                                      | some unmeasured, reading uncertain |
| calib 04, 1  | 8 pt  | `Wi1.0M`          | S0.5    | +0.12                                      | some unmeasured                    |
| calib 04, 2  | 18 pt | `Wi1.0M`          | S0.5    | -0.00                                      | some unmeasured                    |
| calib 04, 3  | 30 pt | `Wi1.0M`          | S1.8    | +0.80                                      | some unmeasured                    |
| calib 04, 4  | 48 pt | `Wi1.0M`          | S2.8    | +0.92                                      | some unmeasured                    |
| 1            | 12 pt | `MMM...MM`        | S0.2    | -0.05                                      | measured                           |
| 2            | 12 pt | `000...0M`        | S5      | +0.25                                      | measured                           |
| 3            | 12 pt | `111...1M`        | S5      | +0.25                                      | measured                           |
| 4            | 12 pt | `222...2M`        | S5      | +0.25                                      | measured                           |
| 5            | 12 pt | `333...3M`        | S5      | +0.25                                      | measured                           |
| 6            | 12 pt | `444...4M`        | S5      | +0.25                                      | measured                           |
| 7            | 12 pt | `555...5M`        | S5      | +0.25                                      | measured                           |
| 8            | 12 pt | `666...6M`        | S5      | +0.25                                      | measured                           |
| 9            | 12 pt | `777...7M`        | S5      | +0.25                                      | measured                           |
| 10           | 12 pt | `888...8M`        | S5      | +0.25                                      | measured                           |
| 11           | 12 pt | `999...9M`        | S5      | +0.25                                      | measured                           |
| 12           | 12 pt | `,,,...,M`        | O?      | ran off the label, predicted end 104.60 mm | measured                           |
| 13           | 12 pt | `.......M`        | O?      | ran off the label, predicted end 104.60 mm | measured                           |
| 14           | 12 pt | `///.../M`        | O?      | ran off the label, predicted end 114.24 mm | measured                           |
| 15           | 12 pt | `---...-M`        | S8.2    | +0.19                                      | measured                           |
| 16           | 12 pt | `   ... M`        | O?      | ran off the label, predicted end 104.60 mm | measured                           |
| 17           | 12 pt | `AAA...AM`        | S4      | +0.12                                      | measured                           |
| 18           | 12 pt | `BBB...BM`        | S4      | +0.12                                      | measured                           |
| 19           | 12 pt | `CCC...CM`        | S0.4    | +0.15                                      | measured                           |
| 20           | 12 pt | `DDD...DM`        | S0.4    | +0.15                                      | measured                           |
| 21           | 12 pt | `EEE...EM`        | S4.1    | +0.10                                      | measured                           |
| 22           | 12 pt | `FFF...FM`        | S4.1    | -0.15                                      | measured                           |
| 23           | 12 pt | `GGG...GM`        | S0.5    | +0.25                                      | measured                           |
| 24           | 12 pt | `HHH...HM`        | O3.2    | +0.18                                      | measured                           |
| 25           | 12 pt | `III...IM`        | S0.2    | +0.07                                      | measured                           |
| 26           | 12 pt | `JJJ...JM`        | S9.5    | +0.24                                      | measured                           |
| 27           | 12 pt | `KKK...KM`        | S7.8    | +0.29                                      | measured                           |
| 28           | 12 pt | `LLL...LM`        | S8.5    | +0.12                                      | measured                           |
| 29           | 12 pt | `NNN...NM`        | O3.2    | +0.18                                      | measured                           |
| 30           | 12 pt | `OOO...OM`        | S0.5    | +0.25                                      | measured                           |
| 31           | 12 pt | `PPP...PM`        | S4.2    | +0.20                                      | measured                           |
| 32           | 12 pt | `QQQ...QM`        | S0.5    | +0.25                                      | measured                           |
| 33           | 12 pt | `RRR...RM`        | S0.5    | +0.25                                      | measured                           |
| 34           | 12 pt | `SSS...SM`        | S4.0    | -0.00                                      | measured                           |
| 35           | 12 pt | `TTT...TM`        | S4.4    | +0.15                                      | measured                           |
| 36           | 12 pt | `UUU...UM`        | O3.2    | +0.18                                      | measured                           |
| 37           | 12 pt | `VVV...VM`        | S4.2    | +0.20                                      | measured                           |
| 38           | 12 pt | `WWW...WM`        | S3.2    | +0.32                                      | measured                           |
| 39           | 12 pt | `XXX...XM`        | S4.2    | +0.20                                      | measured                           |
| 40           | 12 pt | `YYY...YM`        | S0.5    | +0.25                                      | measured                           |
| 41           | 12 pt | `ZZZ...ZM`        | S4.5    | +0.25                                      | measured                           |
| 42           | 12 pt | `MMMMMMMMMMM`     | S0.5    | +0.25                                      | measured                           |
| 43           | 12 pt | `,,,...,M`        | O3.6    | +0.15                                      | measured                           |
| 44           | 12 pt | `.......M`        | O3.6    | +0.15                                      | measured                           |
| 45           | 12 pt | `///.../M`        | O7.5    | +0.26                                      | measured                           |
| 46           | 12 pt | `   ... M`        | O3.6    | +0.15                                      | measured                           |
| 47           | 30 pt | `MMMMMMMMM`       | S0      | +0.00                                      | measured                           |
| 48           | 30 pt | `000000000000M`   | S2.3    | -0.08                                      | measured                           |
| 49           | 30 pt | `555555555555M`   | S2.5    | +0.12                                      | measured                           |
| 50           | 30 pt | `,,,...,M`        | O2      | +0.00                                      | measured                           |
| 51           | 30 pt | `   ... M`        | O2      | +0.00                                      | measured                           |
| 52           | 30 pt | `///.../M`        | O2      | +0.00                                      | measured                           |
| 53           | 30 pt | `SSSSSSSSSSM`     | S2      | -0.00                                      | measured                           |
| 54           | 30 pt | `TTTTTTTTTTTM`    | S2.3    | -0.08                                      | measured                           |
| 55           | 30 pt | `KKKKKKKKKM`      | S7.6    | -0.03                                      | measured                           |
| 56           | 30 pt | `BBBBBBBBBM`      | S5.5    | -0.01                                      | measured                           |
| 57           | 30 pt | `OOOOOOOOM`       | S2      | -0.00                                      | measured                           |
| 58           | 30 pt | `XXXXXXXXXXM`     | S2      | -0.00                                      | measured                           |
