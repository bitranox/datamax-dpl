# DPL reference (as implemented)

This is the project's working summary of the Datamax Programming Language as described in the
*Datamax DPL Programmer's Manual*, part number 88-2051-01, revision D2 (1998). It restates the
facts the code relies on, in our own tables, so that every rule in `src/datamax_dpl/domain/` can be
traced to one line here. It is not a copy of the manual; get the manual from the vendor for the
full text. Where the manual contradicts itself, the row says so and names the reading we chose.

Notation: `<SOH>` 0x01, `<STX>` 0x02, `<ESC>` 0x1B, `<CR>` 0x0D. Distances are fixed-width decimal
fields in hundredths of an inch (inch mode, the default) or tenths of a millimetre (metric mode).

## 1. Models

| Profile id       | Name             | dpi    | Max column (in/100)               | Print width (in)                            | Max fields | Chars/field |
|------------------|------------------|--------|-----------------------------------|---------------------------------------------|------------|-------------|
| allegro          | Allegro          | 203    | 410                               | 4.10                                        | 200        | 5000        |
| allegro-2        | Allegro 2        | 203    | 410                               | 4.10                                        | 200        | 5000        |
| prodigy          | Prodigy          | 203    | 410 (blank in manual, width used) | 4.10                                        | 100        | 3000        |
| prodigy-152      | Prodigy 152      | 152    | 410 (assumed)                     | 4.15                                        | 100        | 3000        |
| prodigy-plus     | Prodigy Plus     | 203    | 410                               | 4.10                                        | 200        | 5000        |
| prodigy-plus-152 | Prodigy Plus 152 | 152    | 410                               | 4.21                                        | 200        | 5000        |
| dmx-400          | DMX 400          | 203    | 410                               | 4.10                                        | 199        | 5000        |
| dmx-430          | DMX 430          | 289.56 | 398                               | 3.98 (manual says 1.0, taken as a misprint) | 399        | 10000       |
| dmx-600          | DMX 600          | 300    | 640                               | 6.40                                        | 399        | 10000       |
| dmx-800          | DMX 800          | 300    | 860                               | 8.53 (TDK head; 8.64 Kyocera)               | 399        | 10000       |
| prodigy-max-203  | Prodigy Max 203  | 203    | 410                               | 4.10                                        | 319        | 7000        |
| prodigy-max-300  | Prodigy Max 300  | 300    | 410                               | 4.16                                        | 319        | 7000        |
| pe42             | PE42             | 203    | 410                               | 4.10                                        | 319        | 7000        |
| pe43             | PE43             | 300    | 410                               | 4.16                                        | 319        | 7000        |
| ovation          | Ovation!         | 203    | 410 (assumed)                     | 4.10                                        | 319        | 7000        |
| ovation-300      | Ovation! 300     | 300    | 410 (assumed)                     | 4.16                                        | 319        | 7000        |
| ovation-2        | Ovation 2        | 203    | 425 (assumed from width)          | 4.256                                       | 319        | 7000        |
| titan-6200       | Titan 6200       | 203    | 661                               | 6.62                                        | 399        | 10000       |

Metric max column is the inch value times 2.54 (manual: 410 -> 1041, 640 -> 1625, 860 -> 2184,
661 -> 1680, 398 -> 1011).

Two extra profiles exist in code: `generic` (the intersection of every model above: the smallest
limits and only features every model has) and `permissive` (no model limits, syntax checks only).

## 2. Feature matrix

`Y` supported, `-` not supported, `fw X` from firmware X, `opt` only with the option installed.

| Feature (command)                               | allegro, allegro-2 | prodigy(-152) | prodigy-plus(-152) | dmx-400 | dmx-430 | dmx-600 | dmx-800 | prodigy-max-*, pe42, pe43 | ovation* | titan-6200 |
|-------------------------------------------------|--------------------|---------------|--------------------|---------|---------|---------|---------|---------------------------|----------|------------|
| Cut by N in format (`:nnnn`, `cnn`)             | Y                  | -             | Y                  | Y       | Y       | Y       | Y       | Y                         | Y        | Y          |
| Cut now (`<STX>o`)                              | Y                  | -             | fw 1.18            | Y       | Y       | Y       | Y       | Y                         | Y        | Y          |
| Cutter pulse time (`<STX>b`, `<STX>H`)          | -                  | Y             | Y                  | -       | -       | -       | -       | -                         | -        | -          |
| Stop position / backfeed (`<STX>f`)             | Y                  | -             | Y                  | Y       | Y       | Y       | Y       | Y                         | Y        | Y          |
| Present distance (`<STX>Kf`)                    | -                  | -             | -                  | -       | -       | -       | -       | pe42, pe43 only           | Y        | -          |
| Pause each label (`<STX>J`)                     | Y                  | -             | -                  | Y       | Y       | Y       | Y       | Y                         | Y        | Y          |
| Gap / reflective sensor (`<STX>e`, `<STX>r`)    | Y                  | -             | Y                  | Y       | Y       | Y       | Y       | Y                         | Y        | Y          |
| Attribute (`A1/2/3/5`)                          | -                  | -             | -                  | Y       | Y       | Y       | Y       | Y                         | Y        | Y          |
| Mirror (`M`)                                    | -                  | -             | -                  | -       | -       | fw 2.01 | Y       | Y                         | Y        | Y          |
| Metric inside a format (`m`, `n`)               | -                  | -             | -                  | Y       | Y       | Y       | Y       | Y                         | Y        | Y          |
| Field terminator (`Tnn`)                        | -                  | -             | Y                  | Y       | Y       | Y       | Y       | Y                         | Y        | Y          |
| Backup speed (`pa`)                             | -                  | -             | Y                  | Y       | Y       | Y       | Y       | Y                         | Y        | Y          |
| Circles and polygons (`X` with `C`/`P`)         | -                  | -             | -                  | fw 2.01 | Y       | Y       | Y       | Y                         | Y        | Y          |
| Scalable fonts (`9Sxx`), symbol sets (`ySxx`)   | -                  | -             | -                  | -       | -       | fw 2.01 | Y       | Y                         | -        | Y          |
| Barcodes S, T, u (MaxiCode), v (FIM)            | -                  | -             | -                  | Y       | Y       | Y       | Y       | Y                         | Y        | Y          |
| PDF417 (`z`)                                    | fw 3.2             | fw 3.2        | fw 3.2             | Y       | Y       | Y       | Y       | Y                         | Y        | Y          |
| DataMatrix (`W1c`)                              | opt                | opt           | opt                | opt     | opt     | opt     | opt     | opt                       | opt      | opt        |
| Default bar widths (`c`=`d`=`0`)                | Y                  | Y             | -                  | Y       | Y       | Y       | Y       | Y                         | Y        | Y          |
| Store / recall format (`s`, `r`), `U`, `<STX>U` | Y                  | -             | Y                  | Y       | Y       | Y       | Y       | Y                         | Y        | Y          |
| Print time/date in data (`<STX>T`)              | Y                  | -             | Y                  | Y       | Y       | Y       | Y       | Y                         | Y        | Y          |

Heat (`Hnn`): 00-20 on every model, 00-30 on prodigy-max-*, pe42, pe43, ovation*, titan-6200.
Default 10.

Font 9 point sizes (`A04`..`A72`): A72 only on dmx-600/800; no A04/A05 on pe42 and prodigy-max-*;
the old numbering `000`-`010` works everywhere except that `000` (5 pt) is missing on allegro,
allegro-2, prodigy-plus. Models without the A-series use the old numbering only.

## 3. Speeds

Codes: A 1.0, B 1.5, C 2.0, D 2.5, E 3.0, F 3.5, G 4.0, H 4.5, I 5.0, J 5.5, K 6.0, L 6.5, M 7.0,
N 7.5, O 8.0, P 8.5, Q 9.0, R 9.5, S 10.0 inches per second.

| Model           | Print (range, default) | Slew (range, default) | Backup (range, default) |
|-----------------|------------------------|-----------------------|-------------------------|
| allegro         | C, C                   | C, C                  | C, C                    |
| allegro-2       | C-E, C                 | C-E, C                | C-E, C                  |
| prodigy         | A-G, E                 | A-G, G                | n/a                     |
| prodigy-plus    | C-O, K                 | C-O, K                | C-I, E                  |
| dmx-400         | C-G, G                 | C-I, K                | C-I, E                  |
| dmx-430         | C-K, G                 | C-K, K                | C-I, E                  |
| dmx-600         | C-O, K                 | C-S, O                | C-I, E                  |
| dmx-800         | C-I, G                 | C-K, K                | C-I, E                  |
| prodigy-max-203 | C-S, K                 | C-S, O                | C-I, I                  |
| prodigy-max-300 | C-O, K                 | C-O, O                | C-I, E                  |
| pe42            | C-S, K                 | C-S, O                | C-I, E                  |
| pe43            | C-O, K                 | C-O, M                | C-I, E                  |
| ovation*        | A-C, C                 | A-C, C                | A-C, C                  |
| titan-6200      | C-K, G                 | C-K, K                | C-G, C                  |

The 152 dpi variants take the values of their 203 dpi sibling. The slew default above K on a model
whose slew range ends at K is clamped by the range.

## 4. Job structure

```
[system commands]          e.g. <STX>m, <STX>V, <STX>Kf0100, <STX>f230
<STX>L<CR>                 enter label formatting
Dwh<CR>                    dot size (D11, D22 ...); sent as D11 when the label sets none
                           and the model's default is D22
Hnn<CR> Pa<CR> Sa<CR> pa<CR> Cnnnn<CR> Rnnnn<CR> An<CR> M<CR> m<CR> ySxx<CR> :nnnn<CR> / cnn<CR> z<CR>
<records>...               one per line, each ending in <CR>
Qnnnn<CR>                  quantity
E<CR>                      end and print   (X: end without printing, s<mod><name>: store)
[<STX>o]                   cut the rest of a cut-by batch now
```

Label-format commands are ignored by the printer outside `<STX>L` ... `E`; system and immediate
commands are not allowed inside it. `:nnnn` (4 digits, 0001-9999) and `cnn` (2 digits) cut after
every N labels and N must be smaller than Q; the last partial group is cut at the start of the next
format, so a job that must end cut sends `<STX>o` after `E`.

## 5. Record header

`a b cd eee ffff gggg data`

| Field | Width | Meaning                                                                                                                         |
|-------|-------|---------------------------------------------------------------------------------------------------------------------------------|
| a     | 1     | rotation 1 (0), 2 (90), 3 (180), 4 (270 degrees), clockwise as the manual draws them                                            |
| b     | 1     | `0`-`8` bitmapped font, `9` font 9 / scalable / downloaded, `A`-`z` barcode, `X` graphic, `Y` image, `W` 2-D (`W1c` DataMatrix) |
| c     | 1     | width multiplier `1`-`9`,`A`-`O` (1-24); for ratio barcodes the wide bar width in dots                                          |
| d     | 1     | height multiplier, same range; for barcodes the narrow bar or module width in dots                                              |
| eee   | 3     | `000` for fonts 0-8, X, Y; barcode height 001-999 (in/100 or mm/10, `000` default); font 9 size id                              |
| ffff  | 4     | row 0000-9999, measured upward from the bottom of the label                                                                     |
| gggg  | 4     | column 0000-max column, measured from the left edge                                                                             |
| data  | -     | up to 255 characters (PDF417 up to 3000), ends with `<CR>`                                                                      |

Scalable font records add `hhhh iiii` after the column: height and width as `P004`-`P999` points
or `0016`-`4163` dots, and `eee` is `S00`-`Szz` (binary data) or `s00`-`szz` (hex data); `S00` is
the resident CG Triumvirate Bold Condensed.

Graphics (`b` = `X`, `c` = `d` = `1`, `eee` = `000`):

| Data                | Meaning                                                             |
|---------------------|---------------------------------------------------------------------|
| `Lhhhvvv`           | line: horizontal length, vertical length (3 digits each)            |
| `lhhhhvvvv`         | the same with 4 digits each                                         |
| `Bhhhvvvbbbsss`     | box: width, height, top/bottom thickness, side thickness (3 digits) |
| `bhhhhvvvvbbbbssss` | the same with 4 digits                                              |

Polygon: `1X11ppprrrrccccP001 0001 rrrrcccc rrrrcccc ...` (spaces only for reading): `ppp` in the
height slot is the fill pattern, the first point is the header's row/column, the vertices follow,
the shape closes back to the first point.
Circle: `1X11ppprrrrccccC0010001rrrr`: centre at the header's row/column, radius last.
Fill patterns: 0 none, 1 solid, 2 6%, 3 12%, 4 25%, 5 38%, 6 50%, 7 diamonds, 8 circles,
9 right diagonal, 10 left diagonal, 11 grid.

Image placement: `1Y11000rrrrccccNAME` (rotation 1 only, name up to 8 characters). Image download:
`<STX>IAAFNAME<CR>` followed by rows `80nn<hex bytes><CR>` (nn = byte count in hex),
`0000FFnn<CR>` (repeat the previous row nn times), and `FFFF<CR>` to end.

An image the library draws (a raster fallback, a QR Code) is named `D` plus 7 upper-case hex
digits of a sha256 of its size and bits: the printer keeps an image in its module across jobs and
answers BELL when a job loads a name it already holds (Appendix D), without saying the new image
replaces the old, so a per-job counter could let the next job print the previous job's image. When
that name is already taken in the job (another drawn image with the same hash, or a user `image`
of that name), the hash is salted until it is free; a user-named `image` always keeps its own name.
Across separate jobs two different bitmaps share a name only on a hash collision, about 1 in 2**28.

Measured on an Allegro 2 (firmware CA02 03.04), not stated in the manual: the printer draws the
FIRST row record at the BOTTOM of the image, and images holding `0000FFnn` repeat records print
wrong (one row short per repeat record, or empty, or garbled) while the same images sent one row
record per row print right. datamax-dpl therefore sends the bottom row first and never emits a
repeat record; its decoder still reads them.

## 6. Bitmapped fonts

| Font | Characters                      | Cell (h/w/gap dots) at 203 dpi | at 300 dpi | at 289.56 dpi |
|------|---------------------------------|--------------------------------|------------|---------------|
| 0    | ASCII 32-127                    | 7/5/1                          | 10/7/1     | 10/7/1        |
| 1    | 32-168, 171, 172, 225           | 13/7/2                         | 19/10/3    | 18/10/3       |
| 2    | 32-168, 171, 172, 225           | 18/10/2                        | 27/15/3    | 26/14/3       |
| 3    | upper case set (see code)       | 27/14/2                        | 40/21/3    | 39/20/3       |
| 4    | upper case set                  | 36/18/3                        | 53/27/4    | 52/26/4       |
| 5    | upper case set                  | 52/18/3                        | 77/27/4    | 75/26/4       |
| 6    | upper case set                  | 64/32/4                        | 95/47/6    | 92/46/6       |
| 7    | OCR-A, ASCII 32-126             | 32/15/5                        | 47/22/7    | 46/22/7       |
| 8    | OCR-B digits and a few symbols  | 28/15/5                        | 41/22/7    | 40/22/7       |
| 9    | CG Triumvirate, size from `eee` | proportional                   |            |               |

Character codes above 127 follow code page 437 for fonts 1, 2 and 9 (ä 132, ö 148, ü 129,
Ä 142, Ö 153, Ü 154, ß 225); fonts 3-6 have upper case Ä Ö Ü and ß only. Scalable fonts use the
symbol set chosen with `ySxx` (for example `PM` code page 850, `W1` Windows Latin 1, `E1` ISO 8859-1).

## 7. Barcodes

| Id (with text / bars only) | Symbology                       | Data                                                                                                            | Check digit                |
|----------------------------|---------------------------------|-----------------------------------------------------------------------------------------------------------------|----------------------------|
| A / a                      | Code 39                         | 0-9 A-Z space - . $ / + % *                                                                                     | none                       |
| B / b                      | UPC-A                           | 11 digits, or 12 with check                                                                                     | added when 11 digits given |
| C / c                      | UPC-E                           | 6 digits, or 7 with check                                                                                       | added                      |
| D / d                      | Interleaved 2 of 5              | digits                                                                                                          | none                       |
| E / e                      | Code 128                        | ASCII 32-127, optional subset prefix A/B/C, `&` escapes                                                         | mod 103 by printer         |
| F / f                      | EAN-13                          | 12 digits, or 13 with check                                                                                     | added                      |
| G / g                      | EAN-8                           | 7 digits, or 8 with check                                                                                       | added                      |
| H / h                      | HIBC (Code 39 + mod 43)         | Code 39 set, leading `+` supplied by the host                                                                   | mod 43                     |
| I / i                      | Codabar                         | 0-9 - $ : / . + with start/stop A-D in the data                                                                 | none                       |
| J / j                      | Interleaved 2 of 5 + mod 10     | digits                                                                                                          | mod 10                     |
| K / k                      | Plessey                         | 1-14 digits                                                                                                     | mod 10                     |
| L / l                      | I 2 of 5 + mod 10 + bearer bars | 13 digits                                                                                                       | mod 10                     |
| M / m                      | UPC/EAN 2-digit add-on          | 2 digits                                                                                                        | -                          |
| N / n                      | UPC/EAN 5-digit add-on          | 5 digits                                                                                                        | -                          |
| O / o                      | Code 93                         | 0-9 A-Z - . $ / + % space                                                                                       | by printer                 |
| p                          | Postnet                         | 5, 9 or 11 digits                                                                                               | by printer                 |
| Q / q                      | UCC/EAN-128                     | 19 digits                                                                                                       | by printer                 |
| R / r                      | UCC/EAN-128 K-Mart non-EDI      | 18 digits                                                                                                       | by printer                 |
| S / s                      | UCC/EAN-128 random weight       | 34 or more digits                                                                                               | by printer                 |
| T / t                      | Telepen                         | ASCII 0-127                                                                                                     | by printer                 |
| u                          | MaxiCode                        | structured, up to 84 characters (see code)                                                                      | -                          |
| v                          | FIM                             | one of A B C D                                                                                                  | -                          |
| z                          | PDF417                          | `T`/`F`, security 0-8, aspect 2 digits, rows 2 digits (03-90, 00 auto), columns 2 digits (01-30, 00 auto), data | -                          |
| W1c (`Wc` also accepted)   | DataMatrix                      | `hhh i jjj kkk data`: ECC 000/050/080/100/140/200, format 0-6, rows, columns                                    | -                          |

Code 128 escapes: `&A` FNC3, `&B` FNC2, `&C` SHIFT, `&D` CODE C, `&E` CODE B / FNC4, `&F` FNC4 /
CODE A, `&G` FNC1. UPC-A and EAN-13 accept a `V` in the price position (UPC-A position 6 or 7,
EAN-13 position 7 or 8) to have the printer compute a price check digit.

## 8. Status answers

| Request   | Answer                                                                                                                        | Meaning                                                                                                                   |
|-----------|-------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------|
| `<SOH>A`  | 8 characters `Y`/`N` then `<CR>`                                                                                              | 1 interpreter busy, 2 paper out, 3 ribbon out, 4 printing batch, 5 busy printing, 6 paused, 7 label presented, 8 always N |
| `<SOH>F`  | one byte then `<CR>`                                                                                                          | bits 0-6 as above in the same order, bit 7 always 0                                                                       |
| `<SOH>E`  | `nnnn<CR>`                                                                                                                    | labels left in the batch                                                                                                  |
| `<SOH>#`  | `T` and XON                                                                                                                   | reset done                                                                                                                |
| `<STX>k`  | `Y`                                                                                                                           | serial port test; every model except prodigy                                                                              |
| `<STX>v`  | e.g. `VER DA - 01.01 12/21/93<CR>`                                                                                            | firmware version; not prodigy                                                                                             |
| `<STX>KQ` | lines `INTERNAL MEMORY`, `VER:`, `INSTALLED:`, `AVAILABLE:`, `MODULE:`, `SCALABLE:`, `LABEL MEM:`, `LABEL SIZE: wwww:gggg:oo` | memory and label size; dmx-600/800, prodigy-max, pe42/43, ovation, titan-6200                                             |

Error characters the printer may send: `c` framing error, `v` input buffer overflow, `I` invalid
command sequence, `R` power-on reset, `T` software reset, BEL (0x07) bad image download.

### Memory module directory (`<STX>W`)

`<STX>Wa` asks for the directory of the memory modules (manual, "STX W Request Memory Module
information", printed pages 43-44). A module holds fonts, graphics and label formats together but
lists one kind per request, so `a` picks it: `F` font data, `G` graphic data, `L` format data. The
manual names no other letter. The `memory` command sends `<STX>WF`, `<STX>WG` and `<STX>WL`.

The manual's sample answer to `<STX>WG`, as printed:

```text
MODULE: A<CR>
AVAILABLE BYTES IN MODULE:
00049083<CR>
MODULE: B<CR>
LOGO<CR>
CAM<CR>
AVAILABLE BYTES IN MODULE:
00257919<CR>
```

Module A holds no graphic and has 49083 bytes free; module B holds the graphics `LOGO` and `CAM`
and has 257919 bytes free. The module letters are `A` to `E` (Appendix L), and a stored name is at
most 16 characters. A corrupt module answers `No Modules Available` (the `<STX>q` Clear Module
entry, page 34).

What one Allegro 2 (firmware CA02 03.04) sent on 2026-10-30, read through a print server, differs
from the sample in three ways. It puts a space before the colon of the free-bytes header and the
number on the same line. Every line, the last one included, ends in `<CR>`. The font answer starts
with an XON byte (0x11). Its answer to `<STX>WG`:

```text
MODULE: A<CR>
M7F<CR>
M7R<CR>
M7B<CR>
M9F<CR>
M9R<CR>
M9B<CR>
M10F<CR>
M10R<CR>
M10B<CR>
CK2IMG<CR>
CK3IMG<CR>
AVAILABLE BYTES IN MODULE : 00048631<CR>
```

`<STX>WF` and `<STX>WL` listed module A with no names and the same 00048631 free bytes: the figure
is the module's free space, not a per-kind count. The three answers are kept byte for byte in
`tests/fixtures/memory_allegro2_*.txt`.

The answer has no terminator and no fixed length: it ends when the printer stops sending, so it is
read until a pause (1 s by default, `--idle`; the Allegro 2 above answered completely with it). The
number after the free-bytes header is accepted on the same line and on the next, with blanks
around the colon and in any letter case; CR, LF and CRLF all end a line, and XON/XOFF bytes are
dropped. A line that starts like the free-bytes header but cannot be read as one is never taken as
a name. The directory is reported unparsed, with its raw text, when it is neither a module list nor
`No Modules Available`, or when a module's free bytes could not be read. The manual does not bound
how many names a module lists; the read stops at 1 MiB.

The manual has no command that returns the model name. `detect` therefore reports evidence
(which queries answered, the firmware string, the label width from `<STX>KQ`) and the profiles that
fit it.

## 9. Known contradictions in the manual and the reading taken

| Topic                         | Manual says                                                                                                    | We use                                                                                                                                                                                                             |
|-------------------------------|----------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| DMX 430 print width           | 1.0 in                                                                                                         | 3.98 in, from its max column 398                                                                                                                                                                                   |
| Cut-by example                | cuts after labels 5, 10 and 20 of 21                                                                           | every 5th label (5, 10, 15, 20)                                                                                                                                                                                    |
| Print speed sample `PC`       | 3.0 ips                                                                                                        | C = 2.0 ips, from the speed table                                                                                                                                                                                  |
| MaxiCode id                   | `u` in chapter 8, `U` in the barcode appendix                                                                  | `u`                                                                                                                                                                                                                |
| Cartridge landscape rotations | "3 and 4"                                                                                                      | 2 and 4                                                                                                                                                                                                            |
| Code 93 check digit           | "No" in one table                                                                                              | printer computes it                                                                                                                                                                                                |
| DMX 400 slew speed            | range C-I, default K (Appendix M), outside its own range                                                       | range C-I enforced: a label asking for more gets the nearest speed (`speed_out_of_range`); K is shown by `capabilities` but never sent. Unverified on hardware; the 1999 edition (88-2051-01 E) does not settle it |
| PE43 font 9 sizes A04, A05    | not named: the point-size tables have a "PE42 and Max" column only; the 300 dpi "MAX300dpi" column has A04/A05 | treated like the PE42, no A04/A05: a 4 or 5 pt request gets the nearest size (`font_size_substituted`). The PE43 is 300 dpi, so it may have them; unverified on hardware                                           |

## 10. Measured on hardware

What one printer did on paper where the manual is silent or gives only nominal values. Measured on
an Allegro 2 (firmware CA02 03.04, 203 dpi, 100 x 55 mm gap labels).

These values are now the `allegro-2` model defaults (`scale_along` 0.976, `offset_along` -0.25 mm,
tear-off stop position 67.4 mm, see the calibration defaults table in
[capabilities.md](capabilities.md)). They apply wherever nothing is configured, and the job reports
them in the info finding `model_defaults_applied`. A printer's own calibration
(`calibrate set`, or the keys in [CONFIG.md](../CONFIG.md)) wins over them.

Why 0.976 when the "Length along the feed" row below says 0.977: 0.977 is the first measurement, the ratio of printed
to nominal length with no correction. A calibration label printed at `scale_along` 0.977 then measured
199.8 mm of 200, and `calibrate set` stored 0.977 x 199.8 / 200 = 0.976; at 0.976 the next label
measured exactly 200 mm.

| Topic                  | Measured                                                                                                                                                                                                                                                                              | We use                                                                                                                         |
|------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------|
| 1/100 in in inch mode  | printed as 2 whole dots on both axes (0.250 mm, not 0.254 mm), so inch lengths came out 1.6 % short                                                                                                                                                                                   | the `allegro-2` profile converts with 2 dots per unit; other profiles keep the exact 0.254 mm                                  |
| Length along the feed  | a dot row is 0.1221 mm: 0.977 of nominal, from the platen roller, so it differs per printer                                                                                                                                                                                           | `scale_along`, measured per printer with `calibrate print` / `calibrate set`; `scale_across` likewise                          |
| Position on the label  | a frame meant to sit 2 mm in from every edge sat 0.25 mm high (1.8 mm from the top, 2.3 mm from the bottom), independent of the tear-off position; after `calibrate set --top 1.8 --bottom 2.3` stored `offset_along = -0.25`, all four borders and both lengths measured as intended | `offset_along` / `offset_across`, measured per printer from the across label's borders                                         |
| Overlapping fields     | XOR: dots two fields share print white (a box over an image of the same size showed only their difference); no attribute command                                                                                                                                                      | calibration labels keep every field apart; label designers must too                                                            |
| Longest format         | a format longer than about 201.5 mm is cut off                                                                                                                                                                                                                                        | the along calibration bar is at most 200 mm                                                                                    |
| Tear-off stop position | found on the printer by trial on 2026-10-29: `<STX>f265`, sent with scale 1.0, stopped the label where wanted; stored as 67.4 mm, which converts back to `<STX>f265`                                                                                                                  | `stop_position` (and the `allegro-2` default 67.4 mm) is sent unscaled: a scaled conversion would move the stop that was found |
