# datamax-dpl

<!-- Badges -->
[![CI](https://github.com/bitranox/datamax-dpl/actions/workflows/default_cicd_public.yml/badge.svg)](https://github.com/bitranox/datamax-dpl/actions/workflows/default_cicd_public.yml)
[![CodeQL](https://github.com/bitranox/datamax-dpl/actions/workflows/codeql.yml/badge.svg)](https://github.com/bitranox/datamax-dpl/actions/workflows/codeql.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Open in Codespaces](https://img.shields.io/badge/Codespaces-Open-blue?logo=github&logoColor=white&style=flat-square)](https://codespaces.new/bitranox/datamax-dpl?quickstart=1)
[![PyPI](https://img.shields.io/pypi/v/datamax-dpl.svg)](https://pypi.org/project/datamax-dpl/)
[![PyPI - Downloads](https://img.shields.io/pypi/dm/datamax-dpl.svg)](https://pypi.org/project/datamax-dpl/)
[![Code Style: Ruff](https://img.shields.io/badge/Code%20Style-Ruff-46A3FF?logo=ruff&labelColor=000)](https://docs.astral.sh/ruff/)
[![codecov](https://codecov.io/gh/bitranox/datamax-dpl/graph/badge.svg)](https://codecov.io/gh/bitranox/datamax-dpl)
[![security: bandit](https://img.shields.io/badge/security-bandit-yellow.svg)](https://github.com/PyCQA/bandit)

datamax-dpl turns a label described in JSON into a print job in DPL, the Datamax Programming
Language, and sends it to the printer. You describe text, lines, boxes, circles, polygons,
barcodes (PDF417, MaxiCode and DataMatrix included), QR codes and images in millimetres;
datamax-dpl writes the DPL records for the printer model you name.

Datamax models differ a lot. A Prodigy has no circles, an Allegro no metric formats, scalable
fonts arrived on the DMX 600 with firmware 2.01. datamax-dpl knows the 18 models of the 1998 DPL
manual and checks every label against the one you print on. Where a model lacks a feature it
either applies a documented fallback and tells you (a circle becomes a downloaded bitmap) or, if
you prefer, refuses the label.

**QR codes work although none of these printers has a QR command.** datamax-dpl draws the code
itself (versions 1 to 40, error levels L, M, Q and H) and sends it as a downloaded graphic, so it
prints on all 18 models; the Prodigy and Prodigy Plus need `image_module` set first. On an
Allegro 2 a phone read the printed codes fine up to version 20 at 4 dots per module.

**Custom fonts on demand.** Text prints in the printers' own fonts today. Your own fonts can be
added on demand, for example drawn as graphics the same way as QR codes. If you are interested,
contact us by opening an issue at <https://github.com/bitranox/datamax-dpl/issues>.

It is a Python library and a command line tool. Every command can answer with a JSON envelope
and meaningful exit codes, so scripts and LLM agents can drive it.

**Status:** built from the manual and tested against simulated printers; one real printer, an
Allegro 2, has been checked on paper (text, lines, boxes, images, rotation, four barcode types,
lengths). Other models are still unverified on hardware.

## Install

Python 3.10 or newer. Run it without installing, with [uv](https://docs.astral.sh/uv/):

```bash
uvx datamax-dpl --help
```

or install it:

```bash
uv tool install datamax-dpl
# or
pip install datamax-dpl
```

`datamax-dpl preview` draws text as grey cells by default. With the `preview` extra (Pillow,
Python 3.11 or newer) it writes the actual text into the picture, and a field crossing the text
combines with the letters' dots, as the printer does: a line through text on an Allegro 2 (which
XORs) shows gaps only under the strokes. The lettering is an approximation in the preview's own
font, not the printer's, and may be cut at the end of the field, so the gaps near the end can
differ from the paper. Without the extra the text combines as whole cells, and `preview` says so
with the `preview_text_approximated` finding:

```bash
uvx --from "datamax-dpl[preview]" datamax-dpl --help
# or
pip install "datamax-dpl[preview]"
```

More ways to install are in [INSTALL.md](INSTALL.md).

## Quick start

The `examples/` folder holds a product label, a shipping label with placeholders, and data
records for it.

Check a label against a model without printing:

```bash
datamax-dpl validate examples/product-label.json --model dmx-600
```

```text
OK: model dmx-600, 1 label(s), 5 field(s), 178 bytes
INFO    software_switches_not_sent: software switches (<STX>V) not sent: whether the cutter is installed is unknown (set RenderOptions.cutter_installed; [printer] cutter)
INFO    check_digit_added [element 3]: the printer adds check digit 1
INFO    cutter_assumed: cut commands assume the printer's cutter is installed and enabled
```

Show the DPL it produces (control characters written as `<STX>`, `<CR>`):

```bash
datamax-dpl render examples/product-label.json --model dmx-600
```

Write the raw job to a file instead, or draw it as a PNG at the printer's resolution:

```bash
datamax-dpl render examples/product-label.json --model dmx-600 -o label.dpl
datamax-dpl preview examples/product-label.json --model dmx-600 -o label.png
```

Print it. Datamax network cards listen for raw jobs on port 9100:

```bash
datamax-dpl print examples/product-label.json --model dmx-600 --printer tcp://192.0.2.60:9100
```

Fill placeholders from data records, one label per record:

```bash
datamax-dpl render examples/shipping-label.json --data examples/shipping-records.json --model pe42
```

The same from Python:

```python
from datamax_dpl import Barcode, Label, RenderOptions, Symbology, Text, render

label = Label(
    width=60,
    height=30,
    elements=[
        Text(x=3, y=20, text="ART. 4711", font=3),
        Barcode(x=3, y=3, symbology=Symbology.EAN13, data="400638133393", height=12),
    ],
)
result = render(label, RenderOptions(model="dmx-600"))
# The printer settings and the clear of module E, the format, then the clear after the label:
# b'\x02n\x02c0000\x02O0220\x02M1600\x02SO\x02qE\x02L\rm\r131100002000030ART. 4711\r1F0012000300030400638133393\rQ0001\rE\r\x02qE'
print(result.data)
for finding in result.findings:
    print(finding.severity.value, finding.code.value, finding.message)
```

`render` raises `LabelRejectedError` when a finding is an error; `build_job` returns the same
result without raising. For data records use `render_batch(label, records, options)`, which holds
every label. For a large batch `iter_batch(label, records, options)` yields one `RenderResult` at a
time and reads one record ahead, and `check_batch(label, records, options)` goes over all of them and
returns a `BatchReport` holding no job bytes. Check `report.ok` before anything goes out: then `renders()` goes
to `print_checked` and `record_jobs()` yields the job's pieces, memory not growing with the records. The
report has `records`, `job_bytes`, `findings` (`ReportedFinding` pairs of `record` and `finding`), `downloads`
and their totals; `limits=ReportLimits(findings=..., downloads=...)` says how much of those it keeps, and
`each=` gets a `BatchEntry` for each record as it is rendered. Time does grow: each
record renders once to check and once more to send, unless `spool=` gets a `JobSpool` (`add`, `seal`,
`entries`, the port in `datamax_dpl.application.ports`) to keep each record in, with its values, as it
is checked: the send then reads it back and never reads the records again, so with a spool the records
may be a generator. `print` and `render -o` keep theirs in a temp file. On a two-way link each label with an
image costs a directory read after its downloads, a reused logo included, about 1 s plus the printer's
answer: about 17 minutes per 1000 labels. With no clear before the downloads a label with a new image
costs one more read before them (about 35 minutes per 1000), and a label whose images the printer
already holds costs that read alone. To send
bytes open a transport with `open_printer(uri, timeout=10)` and call `send(data)` on it. `explain(data)`
reads DPL bytes back, as the command `datamax-dpl explain job.dpl` does. All of these are imported
from `datamax_dpl`.

The rest of the commands have library forms too, imported from `datamax_dpl` as well:

- `fill_label(label, record)` returns a copy of the label with its `{name}` placeholders filled from
  one record, as each record of a batch is.
- `get_profile("dmx-600")` returns a model's `PrinterProfile` (its limits, features, image module
  and measured `CalibrationDefaults`), and `list_profiles()` every profile, `generic` and
  `permissive` first, as `datamax-dpl capabilities` and `models` show them.
- `render_preview_png(label, get_profile(model), options=options)` returns the PNG `preview`
  draws, lettered with Pillow when `pillow_available()` (the `preview` extra), cell blocks
  otherwise; `preview_findings(label)` says what that picture cannot show.
- `calibration_labels(width=100, height=55, title="lager")` builds the two labels `calibrate
  print` sends (`CalibrationLabels` with `across` and `along`); `across_label`, `along_label`,
  `across_frame_width` (the frame length to measure, `ACROSS_INSET_MM` in from each edge) and
  `ALONG_LENGTH_MM` (200) are the parts it uses. Pass `options=RenderOptions(model=...)` and an
  along bar of no given length is the longest one format holds on that printer
  (`default_along_length`, 190 mm on the Allegro 2), as `calibrate print` sizes it; without
  options it is 200 mm.

`send(data)` writes the job as it is: it does not check what the printer stored, so an image
download a full module lost gives a label with that image missing, and no error.

`print_label(label, PrintRequest(options=options, endpoint=PrinterEndpoint(uri=uri, timeout=10,
open_printer=open_printer), check=UploadCheck(wait=AnswerWait(seconds=30)),
one_way=printer_uri_is_one_way(uri)))` prints the way the `print` command does. It renders for the
link: it decides `options.model_image_budget` itself and whether the printer's image names are read
at send. A label with an error finding raises `PrintRefusedError` and opens no printer; pass
`records=` for a batch.

To send a job you rendered yourself over a channel that can read answers (tcp, serial), use the
step `print_label` ends with: `print_checked(job, CheckedJob(renders=record_renders(label, None,
options), check=UploadCheck(wait=AnswerWait(seconds=30))),
endpoint=PrinterEndpoint(uri=uri, timeout=10, open_printer=open_printer))`. It reads the printer's
directory after each label's downloads and raises `UploadRefusedError` before a format would print
without its image. It also reads the status once first: on a reported reset one calibration label
goes out before the job, and `CheckedJob(reset=None)` asks nothing. Render with
`model_image_budget=False` to send images past the model's budget at full size, as `print` does.

Everything the package raises on purpose derives from `DatamaxDplError`, also imported from
`datamax_dpl`, so `except DatamaxDplError` catches it all. Below it, `TransportError` covers what a
printer channel does (a timeout, a refused connection, a bad answer), `ConfigurationError` a bad URI
or setting, and `LabelRejectedError` a refused label. `UnknownModelError` and `MissingFieldError`
are `LookupError`s too, `QrDataTooLongError` and `PreviewTooLongError` `ValueError`s too. Three
things stay outside the base: pydantic's `ValidationError` for a label or options document that does
not validate, `lib_layered_config.ConfigError` from `get_config` for a configuration file it cannot
read, and a plain `ValueError` for an argument out of range (a timeout, a calibration size), as each
function's `Raises` section says.

## The label format

A label is a JSON document with job settings (unit, size, quantity, cutting, speeds, heat, media)
and a list of elements:

```json
{
  "unit": "mm",
  "width": 60,
  "height": 30,
  "elements": [
    {"type": "text", "x": 3, "y": 20, "text": "ART. 4711", "font": 3},
    {"type": "barcode", "x": 3, "y": 3, "symbology": "ean13", "data": "400638133393", "height": 12}
  ]
}
```

Element types are `text`, `scalable_text`, `line`, `box`, `circle`, `polygon`, `barcode`, `image`
and `qr`. Text, barcode and QR data may contain `{name}` placeholders. Every field is described
in [docs/label-spec.md](docs/label-spec.md); `datamax-dpl schema` prints the JSON Schema.

## Coordinates

Coordinates follow DPL, which is not how screens do it:

- `x` is measured from the left edge of the label.
- `y` is measured from the BOTTOM edge. A larger `y` is higher up.
- An element is placed by its lower-left corner before rotation; a circle by its centre.
- Lengths are millimetres unless the label sets `"unit": "in"` or `"unit": "dots"`.

`datamax-dpl preview` draws the label with these rules, so you can check the placement before
wasting labels.

## Printer models and fallbacks

`datamax-dpl models` lists the profiles; `datamax-dpl capabilities <model>` shows one in full.
Besides the 18 models there are `generic` (the default: only what every model can do) and
`permissive` (no model checks). Tell datamax-dpl the firmware (`--firmware 2.01`) and installed
options (`--option datamatrix`) where a feature depends on them. Images download to the model's
built-in memory module (`capabilities` shows it as `image_module`); `[printer] image_module`
(`A` to `E`) names another, and a Prodigy or Prodigy Plus, which has none, refuses a job that
downloads an image until it is set.

The policy decides what happens when a label asks for something the model lacks:

- `--policy degrade` (default) applies the documented fallback and reports a warning.
- `--policy strict` turns the same finding into an error, and nothing is printed.

The model matrix, the limits and the full fallback table are in
[docs/capabilities.md](docs/capabilities.md).

## Printer URIs

`--printer` (or `[printer] uri` in the configuration) says where the job goes:

| URI                                                                               | Channel                                                                                       |
|-----------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------|
| `tcp://HOST:PORT`                                                                 | Raw socket; the port defaults to 9100. Can read answers.                                      |
| `serial:///dev/ttyS0?baud=9600&parity=N&bytesize=8&stopbits=1&rtscts=0&xonxoff=0` | Serial port (`serial://COM3?...` on Windows); every parameter is optional. Can read answers.  |
| `device:///dev/usb/lp0`                                                           | A device file, write only.                                                                    |
| `file:PATH` or `file:///abs/path`                                                 | Appends the job to a file.                                                                    |
| `-`                                                                               | Writes the job to standard output; what the command says goes to stderr. `--json` refuses it. |

`--timeout` bounds connecting and each piece of a job. On POSIX that covers `device:` and `file:`
too, and a FIFO nobody reads is refused at once. A write to `device:` or `file:` on Windows, and to
`-` everywhere, takes as long as the far end takes: no timeout applies there.

`status`, `detect` and `memory` need a channel that can read the printer's answer (tcp or serial).
`status` and `memory` keep trying for `[printer] status_wait` seconds (default 30) while a print
server is still busy with an earlier job: a connection that took no request (refused, timed out
while connecting, or closed before answering) is tried again on a new one, and a silent one is read
until the wait ends, never asked twice. `--timeout` sets that whole wait instead. A printer that
refuses every connection, such as one asked on a mistyped port, therefore takes the whole wait to
report.
Within one connection's opening a refused tcp connect is tried again every `[printer]
refused_connect_retry_interval` seconds (0.25) for up to `refused_connect_wait` seconds (3, never
longer than `--timeout`): a print server refuses the next connection for a moment after it closed
one.
A job goes out in pieces of `[printer] send_chunk_bytes` (default 1024, at most 65536), and
`--timeout` bounds each piece, not the whole job: it fires when one piece is not taken in time. A
piece needs its size over the line's speed, about 1.07 s for 1024 bytes at 9600 baud (about 960
bytes/s), so with a timeout well above that it fires only when the printer stops taking data,
however long the whole job takes.

Before each label no clear precedes, `print` may spend up to `[printer] stored_names_wait` seconds
(30), plus the 1 s of silence that ends the printer's answer (`directory_idle`), reading the printer's stored image
names; a `--data` batch of more than one such label needs a tcp or serial printer
(`batch_image_names_unread`), unless the clear after each label (`[printer] clear_graphics_after`)
reaches the module. On a two-way link `print` sends a label's image downloads first and its format
only once the printer lists every one of them, clearing and uploading again, then uploading the
images halved, before refusing the label (`[printer] upload_check_rounds`, see
[CONFIG.md](CONFIG.md)). There the model's own image memory budget is left out, so images `validate`
reports halved go at full size unless the printer did not store them (`image_budget_skipped`), and a
job whose images go to the default module `C`, which no directory lists, is refused before anything
is sent. Before the first label of a two-way job `print` reads the printer's status once, for up to
`[printer] reset_check_wait` seconds (3): on a reported reset one calibration label goes first
(`printer_reset_calibrated`), see [CONFIG.md](CONFIG.md).

`reset`, `cancel`, `pause`, `feed` and `cut` send single printer commands.

`memory` lists what the printer's memory modules hold and how many bytes each has free
(`<STX>W`); `--kind` picks `graphic` (the default), `font`, `format` or `all`:

```bash
datamax-dpl memory --kind graphic --printer tcp://192.0.2.60:9100
```

```text
graphic:
  module A: 49083 bytes free
  module B: 257919 bytes free
    DtLbYG5m
    CAM
```

`--printer` also takes the NAME of a printer configured in a `[printers.<name>]` section, which
then supplies its model, address, label size and scale factors ([CONFIG.md](CONFIG.md)).

## Calibrating a printer

Printed lengths differ a little from nominal, per printer: an Allegro 2 printed 0.977 of nominal
along the feed before calibration (its model default, after one calibration round, is 0.976). Two calibration labels measure it:

```bash
datamax-dpl calibrate print --printer lager          # frame across, bar along the feed
datamax-dpl calibrate set --printer lager --across 95.6 --along 195.4
datamax-dpl calibrate set --printer lager --top 1.8 --bottom 2.3   # borders around the frame
```

Measure the across label's frame from outside edge to outside edge (it stands 2 mm in from every
edge, so it is 96 mm on a 100 mm label) and the along bar on the liner (it runs over several
labels). `calibrate set` computes the new `scale_across` / `scale_along`
(old factor x measured / nominal) and writes it into that printer's section of the user
configuration. The across label also states the border to expect on every side (2 mm); measure
the borders on opposite sides and pass them in pairs (`--top`/`--bottom`, `--left`/`--right`):
`calibrate set` stores `offset_along` / `offset_across`, millimetres every field is moved so the
print sits centred, independent of the tear-off position. Print again to confirm. Fonts and images
sized in printer dots are not scaled.

The along bar is the longest one format holds at the printer's current calibration, in whole
10 mm and at most 200 mm (190 mm on an Allegro 2 at its 0.976 default, where a 200 mm bar needs
row 1653, past the Allegro 2's limit of 1648), and `calibrate set` measures against that same
length, so the new factor is computed against the length printed; where a format limit applies,
`calibrate set` works that length out from `label_width` or `--width`, as `calibrate print`
built the label. `--along-length` sets another; one longer than a format holds is refused
(`format_past_max_length`) with the longest that fits named. Pass the length you printed to
`calibrate set` as well. `calibrate print` names the bar length it drew (`along_length_mm` in
`--json`), and when `calibrate set` would measure against another length (a `--model` or other
render option changed it), its output says which `--along-length` to pass.

Where nothing is configured, a model's measured defaults apply: the `allegro-2` profile prints with
`scale_along` 0.976, `offset_along` -0.25 mm and a 66.8 mm tear-off stop, every other model nominal.
The result names each default it used (`model_defaults_applied`); `calibrate print` prints with
them and `calibrate set` starts from them. Any key you set, even `scale_along = 1.0`, wins.

The stop positions are configured beside the calibration; which one a job sends is in
[Where the label stops](#where-the-label-stops).

## Where the label stops

After printing, the printer feeds the label to a stop position: the tear-off edge, the cutter, or
where it is presented for removal. The job's stop position (`<STX>f`) comes from the first of
these that applies:

1. The label's `stop_position`, in the label's unit, sent as `<STX>f` and corrected by the
   printer's `scale_along` like every length on the label. It wins over every configured stop,
   in a job that cuts too. `"stop_position": "printer"` wins the same way but sends no `<STX>f`:
   the printer keeps its own setting. The one exception is a label `present_distance` on a model
   without `<STX>Kf`, which still goes out as `<STX>f` (`present_distance_unsupported`, an error
   under `strict`).
2. On a model with `<STX>f` but no `<STX>Kf`, the label's `present_distance`, sent as the label's
   stop position (`present_distance_unsupported`: it is then measured from the sensor rather than
   from the next label).
3. A configured stop: `[printer] cutter_stop_position` for a job that cuts (the label has `cut`),
   `[printer] stop_position` for every other job. Both are sent exactly as configured, without
   scale or offset. An unset `stop_position` takes the model's default, which only `allegro-2`
   has (66.8 mm, sent as `<STX>f263`); `stop_position = "printer"` sends none, the model's default
   included. An unset `cutter_stop_position` takes the model's cutter default, which only
   `allegro-2` has (76.8 mm, sent as `<STX>f302`, where one Allegro 2's cutter cut in the gap,
   2026-11-06); on other models a cutting job then sends no stop.
4. Nothing: the printer keeps its own setting.

A `present_distance` can also go out beside that stop:

- On a model with `<STX>Kf` (the `present_distance` column in
  [docs/capabilities.md](docs/capabilities.md): PE42, PE43, the Ovation models and `permissive`),
  it is sent as `<STX>Kf` in addition to the stop from the list. The manual (page 53) says
  `<STX>Kf` has the same effect as `<STX>f`; which of the two takes effect when both are sent is
  not checked on hardware. Past 9999/100 inch it is refused (`present_distance_too_large`).
- On a model with `<STX>f` but no `<STX>Kf`, a label that sets both a number as `stop_position`
  and `present_distance` sends two `<STX>f`: `stop_position` first, the present distance second
  (`present_distance_unsupported`); with `"stop_position": "printer"` only the present distance. A printer that applies the last `<STX>f` it receives stops at
  the present distance; not checked on hardware.

`generic`, `prodigy` and `prodigy-152` have no `<STX>f` (the `stop_position` column in
[docs/capabilities.md](docs/capabilities.md)). There a label's `stop_position` is not
sent (`stop_position_unsupported`), a `present_distance` is ignored (`present_distance_unsupported`),
and a configured stop is not sent either (the info `configured_stop_position_unsupported`; the
job still prints, under `strict` too). A `"printer"` stop, in the label or the configuration, asks
for exactly what these models do and reports nothing. A stop past 999/100 inch is refused
(`stop_position_too_large`). `render` and `print` name the model's stop position in
`model_defaults_applied` only when the job sends it. Keys, bounds and the note for a deployed
configuration that still sets the calibration keys explicitly are in [CONFIG.md](CONFIG.md).

## Long labels on continuous media

One format reaches only so far along the feed (on the Allegro 2, 1648 dot rows, about 200 mm;
`format_past_max_length` refuses a label past it). A longer label prints as several labels, each
one segment of it, back to back on continuous media. Measured on one Allegro 2 (firmware CA02
03.04): on continuous media the printer prints a label's content and then feeds the `<STX>c`
length (`continuous_length`, else `height`) on top of it, so that length is the blank paper after
each label, not the label's length. The measurements are in
[docs/dpl-reference.md](docs/dpl-reference.md) section 10.

1. Design each segment as its own label, or one label filled from several data records, each no
   longer than one format.
2. Give each `"media": "continuous"` and `"continuous_length": 0.25` (in mm; the smallest length
   that stays in continuous mode, sent as `<STX>c0001`). Labels then follow each other with about
   2 mm of blank paper between them, so plan for a 2 mm gap where one segment meets the next: put
   the seams where nothing crosses them.
3. Print them in one job (`datamax-dpl print segment.json --data segments.json --printer lager`) or as
   separate `print` commands: on one Allegro 2 both left the same 2 mm.
4. Return to the label sensor and the tear-off position:

   ```bash
   datamax-dpl feed --media gap --printer lager
   ```

   `feed --media gap` (or `reflective`) sends `<STX>c0000`, the sensor (`<STX>e` or `<STX>r`), the
   configured stop position (the Allegro 2 default `<STX>f263`) and one feed. On the Allegro 2 this
   left the stock at the tear-off position in both prints. A model that cannot choose its sensor or
   has no stop command gets the same findings a label's job gives (the Prodigy: only `<STX>c0000`
   and the feed). Without that feed the next gap label's job sets the sensor itself, but the stock
   stays where the last segment ended until then.

## JSON output for scripts and LLMs

Put `--json` (indented) or `--json-bare` (one line) before the command. The answer on stdout is
an envelope:

```bash
datamax-dpl --json validate examples/product-label.json --model allegro --policy strict
```

```json
{
  "ok": false,
  "command": "validate",
  "data": {
    "model": "allegro",
    "dpl_unit": "inch",
    "records": 1,
    "fields": 5,
    "bytes": 2382,
    "downloads": ["DtLbYG5m"],
    "downloads_total": 1,
    "findings": [
      {"code": "inch_units", "severity": "info", "message": "Allegro formats take inches; millimetres are rounded to 1/100 in", "element": null, "fallback": null, "record": null},
      {"code": "software_switches_not_sent", "severity": "info", "message": "software switches (<STX>V) not sent: whether the cutter is installed is unknown (set RenderOptions.cutter_installed; [printer] cutter)", "element": null, "fallback": null, "record": null},
      {"code": "check_digit_added", "severity": "info", "message": "the printer adds check digit 1", "element": 3, "fallback": null, "record": null},
      {"code": "circle_rasterized", "severity": "error", "message": "circle cannot be drawn natively: circles: Allegro does not have it", "element": 4, "fallback": "drawn as downloaded image DtLbYG5m (81x81 dots)", "record": null},
      {"code": "cutter_assumed", "severity": "info", "message": "cut commands assume the printer's cutter is installed and enabled", "element": null, "fallback": null, "record": null}
    ],
    "findings_total": 5,
    "findings_by_code": {"inch_units": 1, "software_switches_not_sent": 1, "check_digit_added": 1, "circle_rasterized": 1, "cutter_assumed": 1},
    "report_note": null,
    "dpl": null,
    "dpl_base64": null,
    "output": null,
    "printer": null,
    "bytes_sent": null
  },
  "skipped": []
}
```

The real answer is indented one value per line; each finding is shown on one line here. A `--data`
batch keeps the first findings, past those the first of each code, and the first image names
(`[printer] max_reported_findings`, `max_reported_downloads`, 1000 each); `findings_total`,
`findings_by_code` and `downloads_total` count all of them, and `report_note` says what was cut and
which key keeps more. A record number
counts from 0, in a finding's `record` and in every message (`data record 7`); only a JSON line's own
parse error goes by its line in the file. When the
command cannot run at all, the envelope carries `error` (the exception type) and `message`
instead of `data`.

| Exit code | Meaning                                                                                                                                                                                                                                                                                                                                                        |
|-----------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 0         | Done.                                                                                                                                                                                                                                                                                                                                                          |
| 1         | Refused: at least one finding is an error. Nothing more was printed or written; a `--data` batch may have printed earlier labels, and the `record` of the finding says where it stopped. `status` also exits 1 when the printer reports a problem (paper out, for example), `explain` when it could not read part of the job, and `detect` when no model fits. |
| 2         | Could not run: unreadable or invalid label, unknown model, bad configuration, printer not reachable.                                                                                                                                                                                                                                                           |
| 130       | Interrupted (Ctrl-C, or Ctrl-Break on Windows). For a command that sends, part of a job may already have been sent. On Windows a Ctrl-C during a wait on a silent socket takes effect only when that wait ends, up to its timeout.                                                                                                                             |
| 141       | The reader of the output went away (`datamax-dpl schema \| head -1`); nothing more is said.                                                                                                                                                                                                                                                                    |

`info` and the config commands answer in the same envelopes (`config`: the dict `config --format
json` prints; `config-deploy` and `config-generate-examples`: the written paths under `paths`) and
keep their own exit codes: `config` exits 22 for an unknown `--section` and 78 for a configuration
that does not load, `config-deploy` 13 for a permission error, 78 for refused permission settings
and 1 for any other failure, `config-generate-examples` 1. A usage error (a mistyped option, an
unknown command) exits 2 and carries the click error's name in `error`; `command` is null when the
command line named none.

Finding codes are stable, so a script can react to `circle_rasterized` without parsing the
message.

## Claude Code skill

The repository is also a Claude Code plugin with a skill that teaches an agent to use
datamax-dpl. Inside Claude Code:

```text
/plugin marketplace add bitranox/datamax-dpl
/plugin install datamax-dpl
```

## Status and limits

- Built from the Datamax DPL Programmer's Manual 88-2051-01 Rev D2 (1998) only. Models and
  commands that came later are not supported. QR codes are printed as downloaded images, in
  every version from 1 to 40; [the label spec](docs/label-spec.md#sizes) lists their sizes and how
  they scanned on an Allegro 2.
- Only an Allegro 2 has been checked on a real printer.
- An Allegro 2 cuts a long format off, at a row that depends on the format's content in a way
  that is not understood: a tall element from the bottom was cut at rows 1648 to 1652, a format
  with nothing tall printed to row 1675. A label reaching past row 1648 (about 201 mm), the lowest
  cut seen, is refused (`format_past_max_length`); that is conservative, and `[printer]
  max_format_rows` raises it.
- The manual contradicts itself in places; [docs/dpl-reference.md](docs/dpl-reference.md) lists
  each case and the reading taken.
- The manual is copyrighted and not included or redistributed here. Get it from the vendor.

## Configuration

Defaults for the printer (model, firmware, URI, policy, timeout, cutter, options, scale factors,
label size, the waits and retries of a printer exchange, `refused_connect_wait` and
`refused_connect_retry_interval` among them, and the printer state keys `send_settings`, `media`,
`start_of_print_mm`, `max_label_length_mm`, `feed_rate` and `label_present`) and named printers live
in a layered configuration: bundled defaults, system, host and user files, `.env`, environment
variables and `--set`. [CONFIG.md](CONFIG.md) explains every key.

Every label's job sets the persistent printer state it relies on before its format (media sensor,
start of print, maximum label length, feed rate, software switches, stop position), since another
program or the front panel may have changed it; [The printer state each job
sets](CONFIG.md#the-printer-state-each-job-sets) lists what goes out and the keys that change it.

## Development

[DEVELOPMENT.md](DEVELOPMENT.md) covers the make targets and the test setup,
[CONTRIBUTING.md](CONTRIBUTING.md) the workflow,
[docs/systemdesign/module_reference.md](docs/systemdesign/module_reference.md) the module layout,
and [CHANGELOG.md](CHANGELOG.md) the releases. How AI tools were used is described in
[ai-transparency.md](ai-transparency.md).

## License

MIT, see [LICENSE](LICENSE).
