# Printer Calibration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use bitranox:process-agents-subagent-driven-development (recommended) or bitranox:process-plan-executor to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-printer scale factors, named printers and two calibration labels, as decided in
`docs/adr/0002-printer-calibration-and-named-printers.md` (frozen at commit 46ee704).

**Architecture:** Every length the renderer converts learns its axis (across the web or along
the feed); the conversion applies the model's whole-dot inch unit and the printer's scale factor
for that axis. Named printer sections feed the factors through `RenderOptions`. The calibration
labels are pure domain functions; the CLI prints them and writes the measured factors back into
the user configuration.

**Tech Stack:** Python 3.10+, pydantic, rich-click, lib_layered_config, tomlkit, pytest, bmk.

## Global Constraints

- Python floor 3.10; pyright strict; ruff as configured; no suppression without a stated reason.
- Gate: `env -u VIRTUAL_ENV BMK_PYTHON_CMD="$PWD/.venv/bin/python" BMK_OUTPUT_FORMAT=text make test`.
- Public repo: no private hosts, addresses or paths in tracked files; examples use
  `tcp://192.0.2.60:9100` and printer names like `lager`.
- No AI attribution in commits; commit messages via `git commit -F <file>`.
- ASCII punctuation in docs and messages.
- Never edit `.github/`.

---

## HIGH: milestones

1. **M1 Axis-aware lengths and scale factors.** `RenderOptions.scale_across`/`scale_along`, the
   Allegro 2's whole-dot inch unit, every conversion axis-aware; `[printer] scale_across` and
   `scale_along` read from the config. MVP slice: with `--set printer.scale_along=0.977` a 200 mm
   DPL line measures 200 mm on the Allegro 2, and an image and a box of the same size line up.
   **CHECKPOINT:** print on the real printer and measure before M2-M4 get their tasks.
2. **M2 Named printers.** `[printers.<name>]`, `--printer NAME|URI`, label size keys.
3. **M3 Calibration labels and commands.** `calibration_labels()`, `calibrate print`,
   `calibrate set` (tomlkit edit of the user `config.toml`).
4. **M4 Docs and skill.** label spec, config docs, `docs/dpl-reference.md`, the skill's
   "Calibrate a printer" section, CHANGELOG, a real calibration round on the Allegro 2.

Cut: preview stays nominal (it shows the design, not one printer's error); circles keep one
radius (a firmware circle cannot become an ellipse); fonts and user bitmaps are not scaled.

Risks, ranked: (1) a call site given the wrong axis (rotated barcodes, raster frames) - covered
by per-site tests; (2) the whole-dot unit holds on the Allegro 2 only - other profiles keep the
exact 0.254 mm and the factors absorb the rest; (3) the user `config.toml` write must keep
comments - tomlkit round trip, tested on a commented file.

## MID: components and interfaces

- `domain/units.py`: `Axis` enum (`ACROSS`, `ALONG`); `to_dpl(..., unit_dots: float | None = None,
  scale: float = 1.0)` and `to_dots(..., scale: float = 1.0)` keep their current results with the
  defaults.
- `domain/profiles.py`: `PrinterProfile.inch_unit_dots: int | None = None`, set to 2 for
  `allegro-2` only (measured).
- `domain/render_context.py`: `RenderOptions.scale_across`, `scale_along` (floats, 0.5-1.5,
  default 1.0); `RenderContext.scale_across`, `scale_along`; `distance`, `inch_hundredths`,
  `dots`, `offset_dots` take a keyword-only `axis: Axis`.
- Call sites (32, listed by `grep -rn "\.distance(\|\.dots(\|\.offset_dots(\|inch_hundredths("
  src/datamax_dpl`): x, column, column offset, horizontal line length, box width, box side ->
  ACROSS; y, row, row offset, vertical line length, box height, box edge, media lengths, stop
  position, present distance -> ALONG; barcode height -> ALONG at 0/180 degrees, ACROSS at
  90/270; radius and line thickness -> ACROSS; raster frame width ACROSS, height ALONG.
- `adapters/cli/settings.py`: `PrinterSettings.scale_across`, `scale_along`; `render_options()`
  passes them. M2 adds `printers` and name resolution.
- Verification per milestone: unit tests at the renderer seam (`build_job` bytes), the bmk gate,
  and for M1 and M4 a measured print.

## DETAILED: M1 tasks

M2-M4 get their tasks after the M1 checkpoint: the measured print decides whether the axis model
holds before the commands are built on it.

### Task 1: Axis and scaled conversion in units.py

**Files:**
- Modify: `src/datamax_dpl/domain/units.py`
- Test: `tests/test_units.py` (create if absent; check `ls tests | grep unit` first)

**Interfaces:**
- Produces: `Axis(str, Enum)` with `ACROSS = "across"`, `ALONG = "along"`;
  `to_dpl(value, unit, *, target, dpi, unit_dots: float | None = None, scale: float = 1.0) -> Converted`;
  `to_dots(value, unit, *, dpi, scale: float = 1.0) -> int`.

**Out of scope:** `from_dpl` (preview and status read-back stay nominal).

**STOP conditions:** existing doctests in `units.py` change their output with default arguments.

- [ ] **Step 1: Write the failing tests**

```python
from datamax_dpl.domain.enums import Unit
from datamax_dpl.domain.units import DplUnit, to_dots, to_dpl


def test_whole_dot_inch_unit_maps_a_millimetre_length_to_more_hundredths() -> None:
    # 2 dots at 203 dpi = 0.2502 mm per 1/100 in, so 100 mm is 400 units, not 394.
    assert to_dpl(100, Unit.MM, target=DplUnit.INCH_HUNDREDTHS, dpi=203, unit_dots=2).value == 400


def test_scale_below_one_lengthens_the_written_value() -> None:
    # printer prints 0.977 of nominal, so 200 mm must be written as 200 / 0.977 mm.
    assert to_dpl(200, Unit.MM, target=DplUnit.INCH_HUNDREDTHS, dpi=203, scale=0.977).value == 806


def test_dots_unit_ignores_the_scale_and_uses_the_whole_dot_unit() -> None:
    assert to_dpl(203, Unit.DOTS, target=DplUnit.INCH_HUNDREDTHS, dpi=203, unit_dots=2, scale=0.9).value == 102


def test_to_dots_applies_the_scale() -> None:
    assert to_dots(25.4, Unit.MM, dpi=203, scale=0.5) == 406


def test_defaults_keep_the_nominal_result() -> None:
    assert to_dpl(10, Unit.MM, target=DplUnit.INCH_HUNDREDTHS, dpi=203).value == 39
```

- [ ] **Step 2: Run, expect FAIL** (`TypeError: unexpected keyword argument 'unit_dots'`)

Run: `.venv/bin/python -m pytest tests/test_units.py -q`

- [ ] **Step 3: Implement**

In `units.py` add after `DplUnit`:

```python
class Axis(str, Enum):
    """Direction of a length on the label: across the web or along the feed."""

    ACROSS = "across"
    ALONG = "along"
```

Replace `to_dpl` and `to_dots` bodies:

```python
def to_dpl(
    value: float,
    unit: Unit,
    *,
    target: DplUnit,
    dpi: float,
    unit_dots: float | None = None,
    scale: float = 1.0,
) -> Converted:
    """Convert a length to the nearest integer DPL distance.

    Args:
        value: The length.
        unit: Unit ``value`` is written in.
        target: DPL unit of the job.
        dpi: Printer resolution, for :attr:`Unit.DOTS`.
        unit_dots: Whole printer dots one 1/100 in prints as, where the model rounds to whole
            dots (2 on the Allegro 2); None for the exact 1/100 in.
        scale: Printed length divided by nominal length on this axis; ignored for
            :attr:`Unit.DOTS`, which already counts printed dots.

    Returns:
        The rounded value and its rounding error.

    Example:
        >>> to_dpl(10, Unit.MM, target=DplUnit.MM_TENTHS, dpi=203).value
        100
        >>> to_dpl(10, Unit.MM, target=DplUnit.INCH_HUNDREDTHS, dpi=203).value
        39
        >>> to_dpl(100, Unit.MM, target=DplUnit.INCH_HUNDREDTHS, dpi=203, unit_dots=2).value
        400
    """
    per_inch = target.per_inch
    if unit_dots is not None and target is DplUnit.INCH_HUNDREDTHS:
        per_inch = dpi / unit_dots
    inches = to_inches(value, unit, dpi=dpi)
    if unit is not Unit.DOTS:
        inches /= scale
    exact = inches * per_inch
    rounded = round(exact)
    return Converted(value=rounded, error_inches=(rounded - exact) / per_inch)


def to_dots(value: float, unit: Unit, *, dpi: float, scale: float = 1.0) -> int:
    """Convert a length to whole printer dots (at least one).

    Example:
        >>> to_dots(1, Unit.INCH, dpi=300)
        300
        >>> to_dots(0.01, Unit.MM, dpi=203)
        1
    """
    inches = to_inches(value, unit, dpi=dpi)
    if unit is not Unit.DOTS:
        inches /= scale
    return max(1, round(inches * dpi))
```

Add `"Axis"` to `__all__`.

- [ ] **Step 4: Run, expect PASS**, plus doctests: `.venv/bin/python -m pytest tests/test_units.py --doctest-modules src/datamax_dpl/domain/units.py -q`
- [ ] **Step 5: Commit** (`feat(units): axis enum, whole-dot inch unit and scale in conversions`)

### Task 2: Profile field and render options

**Files:**
- Modify: `src/datamax_dpl/domain/profiles.py` (field + `_build`), `src/datamax_dpl/domain/render_context.py`, `src/datamax_dpl/domain/render.py:85-97`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: Task 1 `Axis`, `to_dpl(..., unit_dots, scale)`, `to_dots(..., scale)`.
- Produces: `PrinterProfile.inch_unit_dots: int | None`; `RenderOptions.scale_across: float`,
  `RenderOptions.scale_along: float` (`Field(default=1.0, ge=0.5, le=1.5)`);
  `RenderContext.scale_across`, `RenderContext.scale_along` (float, default 1.0);
  `RenderContext.distance(value, *, axis, element, what)`, `inch_hundredths(value, *, axis)`,
  `dots(value, *, axis)`, `offset_dots(value, *, axis)`.

**Out of scope:** `preview.py` has its own `dots` on a canvas, not the context; it stays nominal.

**STOP conditions:** a profile row type other than the one `_build` reads; `RenderContext` built
anywhere besides `render.py` (grep `RenderContext(`).

- [ ] **Step 1: Failing tests** in `tests/test_render.py`, a new section `# ==== scale ====`:

```python
@pytest.mark.os_agnostic
def test_scale_along_lengthens_rows_and_vertical_lines_only() -> None:
    label = Label(elements=[Line(x=10, y=10, x2=10, y2=110, thickness=0.5)])
    nominal = records(label, "allegro")
    scaled = [
        line
        for line in build_job(label, RenderOptions(model="allegro", scale_along=0.8)).data.split(b"\r")
        if line[:1] == b"1"
    ]
    assert nominal != scaled
    # column (x) unchanged, row (y) and vertical length grow by 1/0.8
    assert nominal[0][9:13] == scaled[0][9:13]


@pytest.mark.os_agnostic
def test_allegro_2_writes_100_mm_as_400_hundredths() -> None:
    label = Label(elements=[Line(x=0, y=0, x2=100, y2=0, thickness=0.25)])
    assert b"L0400" in b"".join(records(label, "allegro-2"))
```

(Check the exact line record layout in `domain/records.py:line_record` before relying on slice
`[9:13]`; adjust the slice to the column field it documents.)

- [ ] **Step 2: Run, expect FAIL** (`scale_along` unknown to `RenderOptions`).
- [ ] **Step 3: Implement**
  - `profiles.py`: add `inch_unit_dots: int | None = None` to `PrinterProfile` with a docstring
    line "whole dots one 1/100 in prints as; measured 2 on the Allegro 2, None = exact"; in
    `_build`, `inch_unit_dots=2 if row.id == "allegro-2" else None` (read how `_build` names the
    id first).
  - `render_context.py`: add the two `RenderOptions` fields; add `scale_across: float = 1.0`,
    `scale_along: float = 1.0` to `RenderContext`; add
    `def _scale(self, axis: Axis) -> float: return self.scale_across if axis is Axis.ACROSS else self.scale_along`;
    pass `unit_dots=self.profile.inch_unit_dots, scale=self._scale(axis)` to every `to_dpl`, and
    `scale=self._scale(axis)` to `to_dots`; `offset_dots` becomes
    `round(to_inches(value, self.unit, dpi=self.profile.dpi) / (1.0 if self.unit is Unit.DOTS else self._scale(axis)) * self.profile.dpi)`.
  - `render.py`: pass `scale_across=options.scale_across, scale_along=options.scale_along` to
    `RenderContext(...)`.
- [ ] **Step 4: Run the full suite**: it fails at every call site with a missing `axis` - Task 3
  fixes them; commit Task 2 and Task 3 together.

### Task 3: Give every call site its axis

**Files:** `src/datamax_dpl/domain/encode_common.py`, `encode_graphics.py`, `encode_barcode.py`,
`raster_fallback.py`, `job.py`, `render.py:110`.

**Interfaces:** Consumes Task 2's axis keyword.

**Out of scope:** `preview.py` (canvas `dots`, nominal by design).

**STOP conditions:** a call site whose axis is not in the MID list above.

- [ ] **Step 1: Failing tests** in `tests/test_render.py` scale section, one per non-obvious site:

```python
@pytest.mark.os_agnostic
@pytest.mark.parametrize(("rotation", "changes"), [(0, True), (90, False)])
def test_barcode_height_follows_the_feed_only_when_unrotated(rotation: int, changes: bool) -> None:
    label = Label.model_validate(
        {
            "elements": [
                {
                    "type": "barcode",
                    "x": 5,
                    "y": 5,
                    "symbology": "code128",
                    "data": "A1",
                    "height": 10,
                    "rotation": rotation,
                }
            ]
        }
    )
    plain = records(label, "allegro")
    along = [
        r
        for r in build_job(label, RenderOptions(model="allegro", scale_along=0.8)).data.split(b"\r")
        if r[:1] in (b"1", b"2", b"3", b"4")
    ]
    assert (plain[0][6:9] != along[0][6:9]) is changes


@pytest.mark.os_agnostic
def test_raster_fallback_frame_grows_along_the_feed_with_scale_along() -> None:
    label = Label.model_validate({"elements": [{"type": "circle", "x": 20, "y": 20, "radius": 5}]})
    plain = build_job(label, RenderOptions(model="allegro"))
    along = build_job(label, RenderOptions(model="allegro", scale_along=0.8))
    assert plain.data.count(b"\r80") < along.data.count(b"\r80")  # more row records
```

(Confirm the barcode height field offset from the record layout in `encode_barcode.py` before
using `[6:9]`; `allegro` has no circle command, so the circle is rasterised - confirm with
`datamax-dpl capabilities allegro`.)

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** - add `axis=Axis.ACROSS` / `axis=Axis.ALONG` per the MID list; in
  `encode_graphics.encode_line` the length axis is `Axis.ACROSS if horizontal else Axis.ALONG`; in
  `_height_field`, `axis = Axis.ACROSS if element.rotation in (Rotation.DEG_90, Rotation.DEG_270) else Axis.ALONG`;
  in `raster_fallback._frame` width ACROSS, height ALONG, `_pixel` column ACROSS, from_bottom ALONG;
  radius, thickness, edge, side ACROSS except box `edge` (top/bottom bars, a height) ALONG;
  `render.py:110` print-width check ACROSS.
- [ ] **Step 4: Full suite green**: `.venv/bin/python -m pytest -q -n auto`
- [ ] **Step 5: Mutation check**: swap ACROSS/ALONG in `_height_field`; the barcode test must go
  RED; restore.
- [ ] **Step 6: Commit Tasks 2+3** (`feat(render): per-axis scale factors and the Allegro 2 whole-dot inch unit`)

### Task 4: Read the factors from the config

**Files:** `src/datamax_dpl/adapters/cli/settings.py`,
`src/datamax_dpl/adapters/config/defaultconfig.d/50-printer.toml`, `tests/test_cli_settings.py`
(find the existing settings test file with `grep -ln printer_settings tests`).

**Interfaces:** Produces `PrinterSettings.scale_across: float = Field(default=1.0, ge=0.5, le=1.5)`,
same for `scale_along`; `render_options()` passes both.

- [ ] **Step 1: Failing test**: `render_options(PrinterSettings(scale_along=0.977)).scale_along == 0.977`
  and a config with `scale_along = 3` raises `ConfigurationError` naming `printer.scale_along`.
- [ ] **Step 2: Implement** the fields, the `render_options` pass-through, and two documented keys
  in `50-printer.toml` (same comment block style as `timeout`: Purpose, Type, Default,
  Environment Variable).
- [ ] **Step 3: Gate** (Global Constraints command), then commit
  (`feat(config): printer scale_across and scale_along`), CHANGELOG `### Added` entry included.

### M1 CHECKPOINT (on the printer)

1. `datamax-dpl --set printer.scale_along=0.977 print <200 mm line label> --model allegro-2 --printer tcp://...`
   - the line measures 200 mm (was 192 mm).
2. A label with a 40 x 20 mm box and an image of the same nominal size at the same place: the
   edges coincide across (was 1.6 % off).
3. Record both results in OPEN-WORK [56]; then detail M2-M4 here.

**Result (measured 2026-10-29 on the Allegro 2, user's ruler): PASSED.**

- The 200 mm DPL line along the feed, rendered with `scale_along=0.977` (818 units), printed
  200 mm.
- A 90 x 10 mm DPL box (360 units) and a 720 x 80 dot image outline, stacked with the same left
  edge, both printed 90 mm with their right edges coinciding. The first attempt placed the two on
  top of each other: the Allegro 2 has no attribute command, so the fields XOR, every coinciding
  dot cancelled and only the 4 rows where the box is taller than the image were left. Compared
  shapes on a calibration label go side by side.
- As implemented, Task 1 bundles `unit_dots` and `scale` into `units.Correction` (ruff PLR0913
  refused a sixth argument): `to_dpl(..., correction=Correction(...))`, `to_dots(..., correction=...)`,
  plus `exact_dots` for unrounded positions. Later tasks use these names.

## DETAILED: M2 tasks (named printers)

### Task 5: Tell a printer URI from a printer name

**Files:** Modify `src/datamax_dpl/adapters/transport/uri.py`; Test `tests/test_transport_uri.py`
(find the existing URI test file with `grep -ln parse_uri tests`).

**Interfaces:** Produces `is_printer_uri(text: str) -> bool` (True for `-` and the `tcp://`,
`serial://`, `device://`, `file:` prefixes the parser dispatches on) and
`is_printer_name(text: str) -> bool` (`^[A-Za-z0-9_][A-Za-z0-9_-]{0,63}$`: no `:`, never `-`).

- [ ] Failing tests: every URI form the parser's docstring lists is a URI and not a name;
  `lager`, `lager-2`, `L3` are names; `-`, `a:b`, `lager.x`, `` are neither.
- [ ] Implement with the module's existing prefix constants (`_TCP_PREFIX` ...), not new literals.
- [ ] Commit with Task 6.

### Task 6: `[printers.<name>]` and `--printer NAME|URI`

**Files:** `src/datamax_dpl/adapters/cli/settings.py`, `src/datamax_dpl/adapters/cli/commands/model_options.py`,
`src/datamax_dpl/adapters/config/defaultconfig.d/50-printer.toml`, `tests/test_cli_overrides.py`, `CHANGELOG.md`.

**Interfaces:**
- `PrinterOverrides.printer: str | None` replaces `uri` (the `--printer` option's dest).
- `PrinterSettings.name: str = ""` (the named printer used, empty for none),
  `label_width: float | None = Field(default=None, gt=0)`, `label_height` the same (millimetres).
- `printer_settings(config, overrides)`: `[printer]` merged with `[printers.<name>]` (the named
  section wins key by key), then the command-line flags; a URI given to `--printer` sets `uri`.
- `--printer` is added to `render` and `validate` (a named printer supplies model and factors);
  `print`, `verify`, `status`, `detect` and the immediate commands keep theirs.

**STOP conditions:** a command passes `uri=` to `resolve_settings` by keyword (grep `uri=`).

- [ ] Failing tests (CLI seam, `config_cli_context` + `--json render` / `print --printer`):
  1. `render --printer lager` with `[printers.lager] model="allegro-2", scale_along=0.8` renders
     the allegro-2 bytes with the scaled rows;
  2. `print --printer lager` where `[printers.lager] uri="file:<tmp>/out.dpl"` writes the job there;
  3. a named printer inherits a key it does not set from `[printer]` (cutter);
  4. `--model generic` still beats the named printer's model;
  5. an unknown name exits 2 with `ConfigurationError` naming it and the configured names;
  6. a section whose name is not a valid printer name (`[printers."a:b"]`) is refused naming it;
  7. `--printer tcp://192.0.2.60:9100` still sets the URI.
- [ ] Implement; `require_uri`'s message becomes "no printer given: pass --printer NAME or URI, or
  set [printer] uri".
- [ ] Document a commented `[printers.lager]` example in `50-printer.toml` (no live section: the
  default config must not invent a printer).
- [ ] Gate, CHANGELOG `### Added`, commit (`feat(config): named printers`).

M3 (calibration labels and commands) and M4 (docs, skill, real calibration round) are detailed
once M2 is in.

## DETAILED: M3 tasks (calibration labels and commands)

M2 is done (ef43593). Layout rules learned on the printer: the Allegro 2 XORs overlapping
fields, so no two shapes on a calibration label may share a dot.

### Task 7: The two calibration labels (domain)

**Files:** Create `src/datamax_dpl/domain/calibration.py`; Test `tests/test_calibration.py`.

**Interfaces:** Produces `ALONG_LENGTH_MM = 200.0`, `TICK_SPACING_MM = 10.0`,
`across_label(*, width: float, height: float, title: str) -> Label` and
`along_label(*, length: float = ALONG_LENGTH_MM, title: str) -> Label`, both in mm, and
`calibration_labels(...)` re-exported from `datamax_dpl`.

- Across: a box frame at (0, 0) of `width` x `height`, thickness 0.25 mm - its OUTER width is the
  measured length; a horizontal bar 1 mm thick at mid-height from the frame's inner left edge to
  its inner right edge (touching, never overlapping); ticks every 10 mm from x = 10 to the last
  multiple below `width`, each 0.25 mm wide, 3 mm tall, starting 0.25 mm above the bar; text
  lines: `title`, "MEASURE THE FRAME OUTSIDE: <width> MM ACROSS".
- Along: label height `length + 5`; a vertical bar 0.5 mm wide from y = 2 to y = 2 + `length`
  at x = 40; ticks every 10 mm to its right (3 mm long, 0.25 mm gap); text: `title`,
  "MEASURE THE LINE: <length> MM ALONG THE FEED", placed right of the ticks.
- Refuse (ValueError) a width or height under 20 mm, or a length over 200 mm (the measured cutoff
  is ~201.5 mm on the Allegro 2; one format cannot be longer).
- [x] Tests: no two elements' bounding boxes overlap (XOR rule), frame outer size equals the
  given size, tick count and positions, rendered DPL for allegro-2 at scale 1.0 has the frame
  width 400 units; refusals.

As built (deviations from the text above):

- `along_label(*, width, length=ALONG_LENGTH_MM, title)` takes the label width: the text has to
  wrap to it, and on a narrow label x = 40 would be off the media, so the bar sits at x = 5.
- Text is word-wrapped (font 1, cell of a 203 dpi head, 2.5 mm lines); the across title sits above
  the ticks and the sentence below the bar; text that does not fit raises ValueError.
- `MIN_LABEL_MM = 20` also bounds the along length from below.
- The overlap test decodes the rendered DPL and intersects the record rectangles, for allegro-2
  and dmx-600 at 100 x 55, 20 x 20 and 57.3 x 31.7 mm. The preview is no oracle for it: it draws
  a thick line centred and past its ends (OPEN-WORK [115]).

### Task 8: `datamax-dpl calibrate print`

**Files:** Create `src/datamax_dpl/adapters/cli/commands/calibrate.py` (a click group
`calibrate` with `print` and `set`), register it in the CLI root; Test `tests/test_cli_calibrate.py`.

- `calibrate print --printer NAME|URI [--width MM] [--height MM] [--along-length MM]
  [--axis across|along|both]` renders both labels with the resolved settings (the printer's
  current factors) and sends them; width and height come from the printer's `label_width` /
  `label_height` unless given; missing both refuses with exit 2 naming the keys. The title line
  names the printer and the factors in use. `--json` envelope lists what was sent.
- [x] Tests with a `file:` URI printer: both formats are in the file; the factor in use shows in
  the title; a missing size is refused.

As built: each label's title is `<NAME> SCALE <factor>` with its own axis's factor (one title
holding both factors did not fit a 20 mm label); both formats go out in ONE send, since the PS301
refuses a reconnect for 1-2 s; `--axis along` needs only the width; a label with an error finding
(e.g. ticks past the print width) is refused with exit 1 and nothing sent; the JSON payload holds
printer, name, model, labels, both factors, bytes_sent and the findings.

### Task 9: `datamax-dpl calibrate set` and the config write

**Files:** Create `src/datamax_dpl/adapters/config/user_edit.py` (tomlkit), the `set` command in
`calibrate.py`; add `tomlkit>=0.15.2` to `pyproject.toml`; Tests `tests/test_config_user_edit.py`,
`tests/test_cli_calibrate.py`.

- `calibrate set --printer NAME (--along MM | --across MM | both) [--along-length MM]`:
  new factor = current factor x measured / nominal (nominal across = the printer's label width,
  along = `--along-length`, default 200), rounded to 4 places, refused outside 0.5-1.5 with both
  numbers named; refuses a URI (`--printer` must name a configured printer).
- `user_edit.set_printer_values(path, name, values)`: load the user `config.toml` with tomlkit
  (create it if absent), set `printers.<name>.<key>`, write it back; comments and key order
  survive. The path comes from `lib_layered_config.deploy_config_paths(..., targets=["user"])`
  for the active profile.
- After writing, clear the loader cache, re-read the config and warn on stderr when the
  effective value differs (a `.env` or environment variable wins).
- [ ] Tests: arithmetic (0.977 x 199 / 200), a commented file keeps its comments, a missing file is
  created, the precedence warning fires when `DATAMAX_DPL___PRINTERS__LAGER__SCALE_ALONG` is set,
  a URI is refused.
