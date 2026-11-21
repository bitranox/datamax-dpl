Allegro 2 font calibration labels, 100 x 55 mm, datamax-dpl branch feat/text-align (0.12.0).

Print in this order with the branch build (font 9 code page 850 needs it), one at a time,
waiting for the printer to be idle between jobs:
  datamax-dpl print FILE --model allegro-2 --cutter yes --printer tcp://PRINTER:9100

Every label carries:
  - a thin vertical line at x 95 mm (from y 0.5 to 51 mm), the right edge every right-aligned
    line should end at;
  - a tick ruler along the top edge, one tick per mm from x 5 to x 95, a longer tick every
    5 mm (5, 10, ... 95), so a phone photo can be measured in mm.

How to read a right-aligned line: measure the gap between its last ink and the line at 95.
Ink touching or crossing the line means the printed text is wider than predicted; a gap
larger than a letter's side bearing (about 0.1-0.3 mm) means it is narrower. The 'predicted
width' is what datamax-dpl measured, so (predicted - real start-to-end) is the summed error
of that line's characters. Dots the text and the line share print WHITE (the Allegro 2
XORs overlapping fields), so a letter touching the line shows a break in it.
Fonts 0-8 are fixed cells per the manual: their lines should end exactly at 95.
Font 9 widths are a Helvetica-Bold stand-in: these lines measure how far it is off.

01-fonts-0-8.json: Fonts 0-8: fixed cells
  y  1.0  font 0: 'Wi1.0Mm' right-aligned at 95, predicted width 5.13 mm (exact cells); tag 'f0' at x 3 in font 2
  y  4.5  font 1: 'Wi1.0Mm' right-aligned at 95, predicted width 7.63 mm (exact cells); tag 'f1' at x 3 in font 2
  y  7.9  font 2: 'Wi1.0Mm' right-aligned at 95, predicted width 10.26 mm (exact cells); tag 'f2' at x 3 in font 2
  y 11.4  font 3: 'WI1.0MM' right-aligned at 95, predicted width 13.76 mm (exact cells); tag 'f3' at x 3 in font 2
  y 15.9  font 4: 'WI1.0MM' right-aligned at 95, predicted width 18.02 mm (exact cells); tag 'f4' at x 3 in font 2
  y 21.6  font 5: 'WI1.0MM' right-aligned at 95, predicted width 18.02 mm (exact cells); tag 'f5' at x 3 in font 2
  y 29.3  font 6: 'WI1.0MM' right-aligned at 95, predicted width 31.03 mm (exact cells); tag 'f6' at x 3 in font 2
  y 38.6  font 7: 'Wi1.0Mm' right-aligned at 95, predicted width 16.89 mm (exact cells); tag 'f7' at x 3 in font 2
  y 43.8  font 8: 'ZC10NE8' right-aligned at 95, predicted width 16.89 mm (exact cells); tag 'f8' at x 3 in font 2

02-font9-characters-1.json: Font 9 characters, 12 pt
  y  1.0  font 9 12 pt, lower case: 'abcdefghijklmnopqrstuvwxyz' right-aligned at 95, predicted width 59.56 mm; bytes 97 98 99 100 101 102 103 104 105 106 107 108 109 110 111 112 113 114 115 116 117 118 119 120 121 122
  y  6.0  font 9 12 pt, upper case: 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' right-aligned at 95, predicted width 75.57 mm; bytes 65 66 67 68 69 70 71 72 73 74 75 76 77 78 79 80 81 82 83 84 85 86 87 88 89 90
  y 11.0  font 9 12 pt, digits: '0123456789' right-aligned at 95, predicted width 23.77 mm; bytes 48 49 50 51 52 53 54 55 56 57
  y 16.0  font 9 12 pt, punctuation 1: ' !"#$%&\'()*' right-aligned at 95, predicted width 21.40 mm; bytes 32 33 34 35 36 37 38 39 40 41 42
  y 21.0  font 9 12 pt, punctuation 2: '+,-./:;<=>?' right-aligned at 95, predicted width 20.14 mm; bytes 43 44 45 46 47 58 59 60 61 62 63
  y 26.0  font 9 12 pt, punctuation 3: '@[\\]^_`{|}~' right-aligned at 95, predicted width 21.15 mm; bytes 64 91 92 93 94 95 96 123 124 125 126
  y 31.0  font 9 12 pt, high bytes 1: 'Çüéâäàåçêëèï' right-aligned at 95, predicted width 28.15 mm; bytes 128 129 130 131 132 133 134 135 136 137 138 139
  y 36.0  font 9 12 pt, high bytes 2: 'îìÄÅÉæÆôöòûù' right-aligned at 95, predicted width 32.28 mm; bytes 140 141 142 143 144 145 146 147 148 149 150 151
  y 41.0  font 9 12 pt, high bytes 3: 'ÿÖÜø£Ø×ƒáíóú' right-aligned at 95, predicted width 30.53 mm; bytes 152 153 154 155 156 157 158 159 160 161 162 163
  y 46.0  font 9 12 pt, high bytes 4: 'ñÑªº¿®½¼¡ÁÂÀ' right-aligned at 95, predicted width 31.91 mm; bytes 164 165 166 167 168 169 171 172 173 181 182 183

03-font9-characters-2.json: Font 9 characters, 12 pt
  y  1.0  font 9 12 pt, high bytes 5: '©¢¥ãÃðÐÊËÈıÍ' right-aligned at 95, predicted width 29.78 mm; bytes 184 189 190 198 199 208 209 210 211 212 213 214
  y  6.0  font 9 12 pt, high bytes 6: 'ÎÏÌÓßÔÒõÕµþÞ' right-aligned at 95, predicted width 29.78 mm; bytes 215 216 222 224 225 226 227 228 229 230 231 232
  y 11.0  font 9 12 pt, high bytes 7: 'ÚÛÙýÝ±¾÷¸°¨·' right-aligned at 95, predicted width 28.40 mm; bytes 233 234 235 236 237 241 243 246 247 248 249 250

04-font9-scale.json: Font 9 scale: one sample at four sizes
  y  1.0  font 9 8 pt: 'Wi1.0M' right-aligned at 95, predicted width 9.76 mm
  y  5.8  font 9 18 pt: 'Wi1.0M' right-aligned at 95, predicted width 21.77 mm
  y 14.2  font 9 30 pt: 'Wi1.0M' right-aligned at 95, predicted width 36.54 mm
  y 26.8  font 9 48 pt: 'Wi1.0M' right-aligned at 95, predicted width 58.43 mm

05-font9-codepage.json: Font 9 code page 437 vs 850 bytes
  y 40.0  font 9 12 pt left-aligned at x 3: 'øØ×®ÁÂÀ©¢¥ãÃð'; 155=ø (437: ¢), 157=Ø (437: ¥), 158=× (437: ₧), 169=® (437: ⌐), 181=Á (437: ╡), 182=Â (437: ╢), 183=À (437: ╖), 184=© (437: ╕), 189=¢ (437: ╜), 190=¥ (437: ╛), 198=ã (437: ╞), 199=Ã (437: ╟), 208=ð (437: ╨)
  y 37.0  font 1 under it: the same bytes as numbers, in order: 155 157 158 169 181 182 183 184 189 190 198 199 208
  y 32.0  font 9 12 pt left-aligned at x 3: 'ÐÊËÈıÍÎÏÌÓÔÒõ'; 209=Ð (437: ╤), 210=Ê (437: ╥), 211=Ë (437: ╙), 212=È (437: ╘), 213=ı (437: ╒), 214=Í (437: ╓), 215=Î (437: ╫), 216=Ï (437: ╪), 222=Ì (437: ▐), 224=Ó (437: α), 226=Ô (437: Γ), 227=Ò (437: π), 228=õ (437: Σ)
  y 29.0  font 1 under it: the same bytes as numbers, in order: 209 210 211 212 213 214 215 216 222 224 226 227 228
  y 24.0  font 9 12 pt left-aligned at x 3: 'ÕþÞÚÛÙýÝ¾¸¨'; 229=Õ (437: σ), 231=þ (437: τ), 232=Þ (437: Φ), 233=Ú (437: Θ), 234=Û (437: Ω), 235=Ù (437: δ), 236=ý (437: ∞), 237=Ý (437: φ), 243=¾ (437: ≤), 247=¸ (437: ≈), 249=¨ (437: ∙)
  y 21.0  font 1 under it: the same bytes as numbers, in order: 229 231 232 233 234 235 236 237 243 247 249

The code page label prints font 9's bytes that differ between code page 437 and 850 as
their code page 850 characters: accented letters on paper confirm 850; box-drawing or Greek
glyphs, or blanks, would mean the printer's font 9 is not 850 at those bytes.
