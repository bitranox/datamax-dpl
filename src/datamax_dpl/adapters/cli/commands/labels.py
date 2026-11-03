"""Label commands: render, validate, explain, schema, models, capabilities.

None of these talks to a printer; ``print`` and the printer commands live in :mod:`.printer`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, overload

import orjson
import rich_click as click
from pydantic import BaseModel, ConfigDict

from ....application.use_cases import BatchReport, check_batch, render_label
from ....domain.decoder import DecodedCommand, explain
from ....domain.enums import ImageModule
from ....domain.errors import BatchTooLargeError, ConfigurationError
from ....domain.held_images import READ_AT_SEND, HeldImages
from ....domain.label import Label
from ....domain.profiles import Feature, PrinterProfile, SpeedRange, get_profile, list_profiles
from ...config.sections import printer_section
from ...transport.uri import is_one_way, parse_printer_uri
from ..constants import CLICK_CONTEXT_SETTINGS
from ..envelope import CommandName
from ..interrupt import raise_if_interrupted
from ..job_file import write_batch
from ..label_io import STDIN, LabelInputError, Records, load_label, load_records, read_input
from ..payloads import RenderPayload, ShownJob, payload_findings_text, refused_text, render_payload, went_out_text
from ..runner import CommandResult, run_command
from ..settings import PrinterOverrides, input_bounds, render_options, report_limits
from ..typed_click import argument, option
from .model_options import command_overrides, printer_choice_decorator, render_options_decorator, resolve_settings

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from ....application.ports import JobSpool
    from ....application.use_cases import BatchEntry
    from ....domain.render import RenderResult
    from ....domain.render_context import RenderOptions
    from ..context import CLIContext
    from ..settings import PrinterSettings

_LABEL_HELP = "Label JSON file ('-' for stdin); 'datamax-dpl schema' prints its format."
_DATA_HELP = "Data records (CSV, JSON array or JSON lines) filling {name} placeholders, one label each."


def load_batch(source: str, settings: PrinterSettings) -> Records:
    """Read the records of a batch, one label each, up to ``[printer] max_batch_records``.

    Every record is parsed once here, so a bad file is refused before anything is rendered, and
    none is kept: the bound is on time, since each record is rendered at least once. Memory does
    not grow with them.

    Raises:
        LabelInputError: The file cannot be read or parsed, or holds no record or too many.
    """
    limit = settings.max_batch_records
    records = load_records(source, limit=limit, bounds=input_bounds(settings))
    if len(records) > limit:
        key = printer_section(settings.name).setting("max_batch_records")
        raise LabelInputError(f"{source} holds more than {limit} data records; a larger batch needs {key}")
    return records


class JobRoute(NamedTuple):
    """What the way a checked job goes on decides about the check.

    Attributes:
        names: :data:`READ_AT_SEND` when the job goes to a two-way printer whose stored image
            names are read right before each label's downloads. Otherwise why they are not read,
            which a single label's ``image_names_unread`` finding quotes (None: the printer was not
            asked); a batch whose labels need them is then refused (``batch_image_names_unread``)
            unless the clear after each label reaches the module.
        spool: Where a batch keeps each record as it is checked, for the send to read back
            instead of rendering it again; None for a job that is not sent, or renders again.
        each: Called with each record of a batch as it is checked, for a caller writing or
            holding the job as it goes.
    """

    names: HeldImages | None = None
    spool: JobSpool | None = None
    each: Callable[[BatchEntry], None] | None = None


#: The route of a job checked and not sent: no printer asked, no spool.
NOT_SENT = JobRoute()


@overload
def check_loaded(
    label: Label, records: None, options: RenderOptions, settings: PrinterSettings, *, route: JobRoute = ...
) -> RenderResult: ...


@overload
def check_loaded(
    label: Label, records: Records, options: RenderOptions, settings: PrinterSettings, *, route: JobRoute = ...
) -> BatchReport: ...


@overload
def check_loaded(
    label: Label, records: Records | None, options: RenderOptions, settings: PrinterSettings, *, route: JobRoute = ...
) -> RenderResult | BatchReport: ...


def check_loaded(
    label: Label,
    records: Records | None,
    options: RenderOptions,
    settings: PrinterSettings,
    *,
    route: JobRoute = NOT_SENT,
) -> RenderResult | BatchReport:
    """Render an already loaded label, or go over its records one at a time keeping no job.

    A command that renders more than once, or needs the label itself as well as its rendering,
    loads LABEL and its data once and calls this: stdin ('-') can be read only once. Every command
    that writes or sends a job checks it here first, so a Ctrl-C noted while it was checked ends the
    command before any byte of the job goes out (see :mod:`..interrupt`).

    Args:
        label: The label to render.
        records: Data records filling the placeholders, or None for one label.
        options: How to render (``render_options`` of the merged settings, or what a command
            makes of them).
        settings: The merged settings, for how much of a batch its answer keeps.
        route: What the printer the job goes to can tell, and where a batch keeps its records.

    Returns:
        The single render, or the batch's report when ``records`` are given.

    Raises:
        KeyboardInterrupt: A Ctrl-C came while the job was checked, and its raise was lost.
    """
    names = route.names
    if records is None:
        result: RenderResult | BatchReport = render_label(label, options, held=names)
    else:
        read_at_send = names is not None and names.read_at_send
        limits = report_limits(settings)
        result = check_batch(
            label, records, options, read_names_at_send=read_at_send, limits=limits, each=route.each, spool=route.spool
        )
    # Records looks for a lost Ctrl-C at each record and at its end, and the last record is filled
    # and kept after that end; a single label has no pass at all. Nothing has gone out yet here.
    raise_if_interrupted()
    return result


def two_way_printer_named(settings: PrinterSettings) -> bool:
    """Whether ``settings`` name a printer whose stored image names can be read: a tcp or serial URI.

    A URI that does not parse names none: ``validate`` never opens it, and ``print`` refuses it.

    Example:
        >>> from datamax_dpl.adapters.cli.settings import PrinterSettings
        >>> [two_way_printer_named(PrinterSettings(uri=uri)) for uri in ("tcp://192.0.2.60:9100", "file:job.dpl", "")]
        [True, False, False]
    """
    if not settings.uri:
        return False
    try:
        return not is_one_way(parse_printer_uri(settings.uri))
    except ValueError:
        return False


class _RenderedJob(NamedTuple):
    """What ``render`` found, and the job its answer shows; None where it shows none."""

    result: RenderResult | BatchReport
    job: bytes | bytearray | None = None


class _JobHolder:
    """A batch's job as it is rendered, within ``max_batch_job_bytes``."""

    def __init__(self, settings: PrinterSettings) -> None:
        self._settings = settings
        self._records = 0
        self._counted = 0
        self.job = bytearray()

    def add(self, entry: BatchEntry) -> None:
        """Hold the next record's bytes; none at all once one is in error, as a refused job is not shown.

        Raises:
            BatchTooLargeError: The jobs passed the limit, batch cuts left out as ``render_batch``
                leaves them out; the message names the key and the way past it.
        """
        number = self._records
        self._records += 1
        self._counted += len(entry.result.data)
        limit = self._settings.max_batch_job_bytes
        if self._counted > limit:
            key = printer_section(self._settings.name).setting("max_batch_job_bytes")
            msg = (
                f"the batch passed {limit} bytes of job at data record {number}; holding a larger job needs {key}, "
                "and -o FILE needs no limit"
            )
            raise BatchTooLargeError(msg)
        if entry.result.ok:
            self.job.extend(entry.job)
        else:
            self.job.clear()


def _batch_held_whole(
    label: Label, records: Records, options: RenderOptions, settings: PrinterSettings
) -> _RenderedJob:
    """Render a batch whose answer is its job, the one path that holds it all.

    The job's bytes are all it holds, one record after another with nothing around each.

    Raises:
        BatchTooLargeError: The jobs passed ``max_batch_job_bytes``; the message names the key.
    """
    holder = _JobHolder(settings)
    report = check_loaded(label, records, options, settings, route=JobRoute(each=holder.add))
    return _RenderedJob(result=report, job=holder.job if report.ok else None)


def _human(payload: RenderPayload, *, show_dpl: bool) -> str:
    parts = [f"model {payload.model}, {payload.records} label(s), {payload.fields} field(s), {payload.bytes} bytes"]
    if payload.findings:
        parts.append(payload_findings_text(payload))
    if show_dpl and payload.dpl:
        parts.append(payload.dpl.replace("<CR>", "<CR>\n").rstrip("\n"))
    if payload.output:
        parts.append(f"written to {payload.output}")
    return "\n".join(parts)


@click.command("render", context_settings=CLICK_CONTEXT_SETTINGS)
@argument("label_source", metavar="LABEL")
@option("--data", default=None, help=_DATA_HELP)
@option("-o", "--output", default=None, help="Write the DPL bytes to this file ('-' for raw bytes on stdout).")
@render_options_decorator
@printer_choice_decorator
@click.pass_context
def cli_render(ctx: click.Context, label_source: str, data: str | None, output: str | None, **given: Any) -> None:
    """Render a label to DPL and report every finding.

    Without --output the DPL is shown readably (control bytes as <STX>, <CR>). Exit 1 when a
    finding is an error; nothing is written or shown of the job then.
    """

    def body(cli_ctx: CLIContext) -> CommandResult:
        machine = cli_ctx.mode.machine
        if output == STDIN and machine:
            # Refused before the label and the records are read: no record can change the answer.
            msg = "--output - cannot be combined with --json: stdout carries the envelope"
            raise ConfigurationError(msg)
        settings = resolve_settings(cli_ctx, command_overrides(given))
        label = load_label(label_source, bounds=input_bounds(settings))
        records = None if data is None else load_batch(data, settings)
        rendered = _rendered(label, records, settings, output=output, cli_ctx=cli_ctx)
        result = rendered.result
        written = output if output is not None and result.ok else None
        shown = None if rendered.job is None else ShownJob(rendered.job, with_base64=machine)
        payload = render_payload(result, shown=shown, output=written, settings=settings)
        if output == STDIN:
            # The job owns stdout, so what a reader must know about it goes to stderr, refused or not.
            notes = went_out_text(payload) if result.ok else refused_text(payload)
            return CommandResult(ok=result.ok, payload=payload, notes=notes)
        return CommandResult(ok=result.ok, payload=payload, human=_human(payload, show_dpl=output is None))

    run_command(ctx, command=CommandName.RENDER, body=body)


def _rendered(
    label: Label, records: Records | None, settings: PrinterSettings, *, output: str | None, cli_ctx: CLIContext
) -> _RenderedJob:
    """Render for ``render``, writing the job when it is ok: as it goes for a batch that has somewhere to go."""
    options = render_options(settings)
    open_spool = cli_ctx.services.open_spool
    if records is None:
        result = check_loaded(label, None, options, settings)
        if output is not None and result.ok:
            _write_output(output, (result.data,))
        return _RenderedJob(result=result, job=result.data if output is None and result.ok else None)
    if output is None:
        return _batch_held_whole(label, records, options, settings)
    if output != STDIN:

        def check(*, each: Callable[[BatchEntry], None] | None = None, spool: JobSpool | None = None) -> BatchReport:
            return check_loaded(label, records, options, settings, route=JobRoute(each=each, spool=spool))

        return _RenderedJob(result=write_batch(output, check, open_spool=open_spool))
    with open_spool() as spool:
        report = check_loaded(label, records, options, settings, route=JobRoute(spool=spool))
        if report.ok:
            _write_output(output, report.record_jobs())
    return _RenderedJob(result=report)


def _write_output(output: str, jobs: Iterable[bytes]) -> None:
    """Write ``jobs`` to ``output``; ``render`` has refused ``-`` under ``--json`` before anything was read."""
    if output == STDIN:
        for job in jobs:
            sys.stdout.buffer.write(job)
        sys.stdout.buffer.flush()
        return
    Path(output).write_bytes(b"".join(jobs))


@click.command("validate", context_settings=CLICK_CONTEXT_SETTINGS)
@argument("label_source", metavar="LABEL")
@option("--data", default=None, help=_DATA_HELP)
@render_options_decorator
@printer_choice_decorator
@click.pass_context
def cli_validate(ctx: click.Context, label_source: str, data: str | None, **given: Any) -> None:
    """Check a label against a printer model without printing; exit 1 on an error finding."""

    def body(cli_ctx: CLIContext) -> CommandResult:
        settings = resolve_settings(cli_ctx, command_overrides(given))
        label = load_label(label_source, bounds=input_bounds(settings))
        records = None if data is None else load_batch(data, settings)
        # A batch is judged by the link print would take: a two-way printer is read before each
        # label, so a batch needing that passes; a single label says its names were not read.
        readable = records is not None and two_way_printer_named(settings)
        names = READ_AT_SEND if readable else None
        result = check_loaded(label, records, render_options(settings), settings, route=JobRoute(names=names))
        payload = render_payload(result, settings=settings)
        verdict = "OK" if result.ok else "REFUSED"
        human = f"{verdict}: {_human(payload, show_dpl=False)}"
        return CommandResult(ok=result.ok, payload=payload, human=human)

    run_command(ctx, command=CommandName.VALIDATE, body=body)


class ExplainPayload(BaseModel):
    """Result of ``explain``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    commands: list[DecodedCommand]
    label: Label | None
    problems: list[str]


@click.command("explain", context_settings=CLICK_CONTEXT_SETTINGS)
@argument("dpl_source", metavar="DPLFILE")
@click.pass_context
def cli_explain(ctx: click.Context, dpl_source: str) -> None:
    """Explain an existing DPL file command by command, and rebuild its label as JSON."""

    def body(cli_ctx: CLIContext) -> CommandResult:
        # The [printer] section's bound: explain names no printer, so no other section applies.
        bounds = input_bounds(resolve_settings(cli_ctx, PrinterOverrides()))
        explanation = explain(read_input(dpl_source, bounds=bounds))
        payload = ExplainPayload(
            commands=list(explanation.commands), label=explanation.label, problems=list(explanation.problems)
        )
        lines = [f"{c.kind.value:9} {c.raw:40} {c.meaning}" for c in explanation.commands] + list(explanation.problems)
        return CommandResult(ok=not explanation.problems, payload=payload, human="\n".join(lines))

    run_command(ctx, command=CommandName.EXPLAIN, body=body)


class SchemaPayload(BaseModel):
    """Result of ``schema``: the JSON Schema of a label document."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label_schema: dict[str, Any]


@click.command("schema", context_settings=CLICK_CONTEXT_SETTINGS)
@click.pass_context
def cli_schema(ctx: click.Context) -> None:
    """Print the JSON Schema of the label format (what 'render' and 'print' read)."""

    def body(_cli_ctx: CLIContext) -> CommandResult:
        schema = Label.model_json_schema()
        human = orjson.dumps(schema, option=orjson.OPT_INDENT_2).decode("utf-8")
        return CommandResult(ok=True, payload=SchemaPayload(label_schema=schema), human=human)

    run_command(ctx, command=CommandName.SCHEMA, body=body)


class ModelSummary(BaseModel):
    """One printer profile in ``models``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str
    dpi: float
    print_width_in: float
    synthetic: bool


class ModelsPayload(BaseModel):
    """Result of ``models``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    models: list[ModelSummary]


def _summary(profile: PrinterProfile) -> ModelSummary:
    return ModelSummary(
        id=profile.id,
        name=profile.name,
        dpi=profile.dpi,
        print_width_in=profile.print_width_in,
        synthetic=profile.synthetic,
    )


@click.command("models", context_settings=CLICK_CONTEXT_SETTINGS)
@click.pass_context
def cli_models(ctx: click.Context) -> None:
    """List the printer model profiles."""

    def body(_cli_ctx: CLIContext) -> CommandResult:
        summaries = [_summary(p) for p in list_profiles()]
        lines = [f"{s.id:18} {s.name:28} {s.dpi:>7g} dpi  {s.print_width_in:>5g} in" for s in summaries]
        return CommandResult(ok=True, payload=ModelsPayload(models=summaries), human="\n".join(lines))

    run_command(ctx, command=CommandName.MODELS, body=body)


class CapabilitiesPayload(BaseModel):
    """Result of ``capabilities``: one profile in full."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str
    dpi: float
    max_column: int
    print_width_in: float
    max_fields: int
    max_field_chars: int
    heat_max: int
    print_speed: str
    slew_speed: str
    backup_speed: str | None
    font9_points: list[int]
    # The built-in module image downloads go to unless [printer] image_module names another;
    # None on a model with no built-in module, which refuses downloads until one is configured.
    image_module: ImageModule | None
    # Dot rows one format reaches at dot height 1 unless [printer] max_format_rows sets another;
    # None where the model has no known limit.
    max_format_rows: int | None
    features: dict[Feature, str]


def _speed(speeds: SpeedRange) -> str:
    return f"{speeds.low.value}-{speeds.high.value} (default {speeds.default.value})"


def capabilities_payload(profile: PrinterProfile) -> CapabilitiesPayload:
    """Describe ``profile`` for people and scripts."""
    return CapabilitiesPayload(
        id=profile.id,
        name=profile.name,
        dpi=profile.dpi,
        max_column=profile.max_column,
        print_width_in=profile.print_width_in,
        max_fields=profile.max_fields,
        max_field_chars=profile.max_field_chars,
        heat_max=profile.heat_max,
        print_speed=_speed(profile.print_speed),
        slew_speed=_speed(profile.slew_speed),
        backup_speed=_speed(profile.backup_speed) if profile.backup_speed else None,
        font9_points=sorted(set(profile.a_series_points) | set(profile.old_series_points)),
        image_module=profile.image_module,
        max_format_rows=profile.max_format_rows,
        features={feature: profile.support(feature).describe() for feature in Feature},
    )


def _capabilities_text(payload: CapabilitiesPayload) -> str:
    """The human form of ``capabilities``: one line per field, then one per feature."""
    rows: list[tuple[str, object]] = [
        ("id", payload.id),
        ("name", payload.name),
        ("dpi", payload.dpi),
        ("max_column", payload.max_column),
        ("print_width_in", payload.print_width_in),
        ("max_fields", payload.max_fields),
        ("max_field_chars", payload.max_field_chars),
        ("heat_max", payload.heat_max),
        ("print_speed", payload.print_speed),
        ("slew_speed", payload.slew_speed),
        ("backup_speed", payload.backup_speed),
        ("font9_points", payload.font9_points),
        ("image_module", None if payload.image_module is None else payload.image_module.value),
        ("max_format_rows", payload.max_format_rows),
    ]
    lines = [f"{key:18} {value}" for key, value in rows]
    lines += [f"  {feature.value:20} {state}" for feature, state in payload.features.items()]
    return "\n".join(lines)


@click.command("capabilities", context_settings=CLICK_CONTEXT_SETTINGS)
@argument("model")
@click.pass_context
def cli_capabilities(ctx: click.Context, model: str) -> None:
    """Show what one printer model can do and which features need firmware or options."""

    def body(_cli_ctx: CLIContext) -> CommandResult:
        payload = capabilities_payload(get_profile(model))
        return CommandResult(ok=True, payload=payload, human=_capabilities_text(payload))

    run_command(ctx, command=CommandName.CAPABILITIES, body=body)


__all__ = [
    "NOT_SENT",
    "JobRoute",
    "capabilities_payload",
    "check_loaded",
    "cli_capabilities",
    "cli_explain",
    "cli_models",
    "cli_render",
    "cli_schema",
    "cli_validate",
    "load_batch",
    "two_way_printer_named",
]
