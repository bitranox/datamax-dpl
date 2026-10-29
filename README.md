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
barcodes (PDF417, MaxiCode and DataMatrix included) and images in millimetres; datamax-dpl writes
the DPL records for the printer model you name.

Datamax models differ a lot. A Prodigy has no circles, an Allegro no metric formats, scalable
fonts arrived on the DMX 600 with firmware 2.01. datamax-dpl knows the 18 models of the 1998 DPL
manual and checks every label against the one you print on. Where a model lacks a feature it
either applies a documented fallback and tells you (a circle becomes a downloaded bitmap) or, if
you prefer, refuses the label.

It is a Python library and a command line tool. Every command can answer with a JSON envelope
and meaningful exit codes, so scripts and LLM agents can drive it. With a camera configured,
`verify` prints a label, photographs it and puts the photo next to the expected preview.

**Status:** built from the manual and tested against simulated printers; one real printer, an
Allegro 2, has been checked on paper (text, lines, boxes, images, rotation, four barcode types,
lengths). Other models and the camera are still unverified on hardware.

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
Python 3.11 or newer) it writes the actual text into the picture:

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
OK: model dmx-600, 1 label(s), 5 field(s), 149 bytes
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
print(result.data)  # b'\x02L\rm\r131100002000030ART. 4711\r1F0012000300030400638133393\rQ0001\rE\r'
for finding in result.findings:
    print(finding.severity.value, finding.code, finding.message)
```

`render` raises `LabelRejectedError` when a finding is an error; `build_job` returns the same
result without raising. For data records use `render_batch(label, records, options)`, and to send
bytes open a transport with `open_printer(uri, timeout=10)` and call `send(data)` on it. All of
these are imported from `datamax_dpl`.

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

Element types are `text`, `scalable_text`, `line`, `box`, `circle`, `polygon`, `barcode` and
`image`. Text and barcode data may contain `{name}` placeholders. Every field is described in
[docs/label-spec.md](docs/label-spec.md); `datamax-dpl schema` prints the JSON Schema.

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
options (`--option datamatrix`) where a feature depends on them.

The policy decides what happens when a label asks for something the model lacks:

- `--policy degrade` (default) applies the documented fallback and reports a warning.
- `--policy strict` turns the same finding into an error, and nothing is printed.

The model matrix, the limits and the full fallback table are in
[docs/capabilities.md](docs/capabilities.md).

## Printer URIs

`--printer` (or `[printer] uri` in the configuration) says where the job goes:

| URI                                                                               | Channel                                                                                      |
|-----------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------|
| `tcp://HOST:PORT`                                                                 | Raw socket; the port defaults to 9100. Can read answers.                                     |
| `serial:///dev/ttyS0?baud=9600&parity=N&bytesize=8&stopbits=1&rtscts=0&xonxoff=0` | Serial port (`serial://COM3?...` on Windows); every parameter is optional. Can read answers. |
| `device:///dev/usb/lp0`                                                           | A device file, write only.                                                                   |
| `file:PATH` or `file:///abs/path`                                                 | Appends the job to a file.                                                                   |
| `-`                                                                               | Writes the job to standard output.                                                           |

`status` and `detect` need a channel that can read the printer's answer (tcp or serial). `reset`,
`cancel`, `pause`, `feed` and `cut` send single printer commands.

`--printer` also takes the NAME of a printer configured in a `[printers.<name>]` section, which
then supplies its model, address, label size and scale factors ([CONFIG.md](CONFIG.md)).

## Calibrating a printer

Printed lengths differ a little from nominal, per printer: an Allegro 2 printed 0.977 of nominal
along the feed. Two calibration labels measure it:

```bash
datamax-dpl calibrate print --printer lager          # frame across, 200 mm bar along the feed
datamax-dpl calibrate set --printer lager --across 95.6 --along 195.4
```

Measure the across label's frame from outside edge to outside edge (it stands 2 mm in from every
edge, so it is 96 mm on a 100 mm label) and the along bar on the liner (it runs over several
labels). `calibrate set` computes the new `scale_across` / `scale_along`
(old factor x measured / nominal) and writes it into that printer's section of the user
configuration. Print again to confirm. Fonts and images sized in printer dots are not scaled.

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
    "bytes": 1568,
    "downloads": ["DPLR0001"],
    "findings": [
      {"code": "circle_rasterized", "severity": "error", "message": "circle cannot be drawn natively: circles: Allegro does not have it", "element": 4, "fallback": "drawn as downloaded image DPLR0001 (81x81 dots)", "record": null}
    ]
  },
  "skipped": []
}
```

(Shortened: the real answer also lists the info findings and a few empty fields.) When the
command cannot run at all, the envelope carries `error` (the exception type) and `message`
instead of `data`.

| Exit code | Meaning                                                                                                        |
|-----------|----------------------------------------------------------------------------------------------------------------|
| 0         | Done.                                                                                                          |
| 1         | Refused: at least one finding is an error. Nothing was printed or written.                                     |
| 2         | Could not run: unreadable or invalid label, unknown model, bad configuration, printer or camera not reachable. |

Finding codes are stable, so a script can react to `circle_rasterized` without parsing the
message.

## Camera verification

The `[camera]` configuration section names a camera; see [CONFIG.md](CONFIG.md). Four kinds
exist: `none` (default), `command` (run a capture program such as `fswebcam`), `http` (fetch a
snapshot URL) and `file` (take the newest image a camera drops into a folder).

```bash
datamax-dpl capture --output-dir photos
datamax-dpl verify examples/product-label.json --model dmx-600 --printer tcp://192.0.2.60:9100 --output-dir photos
```

`verify` sends the job, polls the printer status until it is idle (on a one-way channel it waits
five seconds instead), waits `settle_seconds`, takes one photo, and writes `expected-<time>.png`
next to it. It does not judge the photo; a person, or an LLM given both pictures, compares them.
This loop has only run against a simulated printer and camera so far.

## Claude Code skill

The repository is also a Claude Code plugin with a skill that teaches an agent to use
datamax-dpl. Inside Claude Code:

```text
/plugin marketplace add bitranox/datamax-dpl
/plugin install datamax-dpl
```

## Status and limits

- Built from the Datamax DPL Programmer's Manual 88-2051-01 Rev D2 (1998) only. Models and
  commands that came later, QR codes among them, are not supported.
- Only an Allegro 2 has been checked on a real printer; no camera has been attached yet.
- The manual contradicts itself in places; [docs/dpl-reference.md](docs/dpl-reference.md) lists
  each case and the reading taken.
- The manual is copyrighted and not included or redistributed here. Get it from the vendor.

## Configuration

Defaults for the printer (model, firmware, URI, policy, timeout, cutter, options, scale factors,
label size), named printers and the camera live in a layered configuration: bundled defaults,
system, host and user files, `.env`, environment variables and `--set`. [CONFIG.md](CONFIG.md)
explains every key.

## Development

[DEVELOPMENT.md](DEVELOPMENT.md) covers the make targets and the test setup,
[CONTRIBUTING.md](CONTRIBUTING.md) the workflow,
[docs/systemdesign/module_reference.md](docs/systemdesign/module_reference.md) the module layout,
and [CHANGELOG.md](CHANGELOG.md) the releases. How AI tools were used is described in
[ai-transparency.md](ai-transparency.md).

## License

MIT, see [LICENSE](LICENSE).
