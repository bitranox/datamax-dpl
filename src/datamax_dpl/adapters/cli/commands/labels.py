"""Label commands: render, validate, explain, schema, models, capabilities.

None of these talks to a printer; ``print`` and the printer commands live in :mod:`.printer`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import orjson
import rich_click as click
from pydantic import BaseModel, ConfigDict

from ....application.use_cases import BatchResult, render_batch, render_label
from ....domain.decoder import explain
from ....domain.label import Label
from ....domain.profiles import Feature, PrinterProfile, get_profile, list_profiles
from ..constants import CLICK_CONTEXT_SETTINGS
from ..label_io import STDIN, load_label, load_records, read_input
from ..payloads import RenderPayload, findings_text, render_payload
from ..runner import CommandResult, run_command
from ..settings import render_options
from ..typed_click import argument, get_current_context, option
from .model_options import render_options_decorator, resolve_settings

if TYPE_CHECKING:
    from ....domain.render import RenderResult
    from ..context import CLIContext

_LABEL_HELP = "Label JSON file ('-' for stdin); 'datamax-dpl schema' prints its format."
_DATA_HELP = "Data records (CSV, JSON array or JSON lines) filling {name} placeholders, one label each."


def build(
    label_source: str, data: str | None, settings_given: dict[str, Any], cli_ctx: CLIContext
) -> RenderResult | BatchResult:
    """Load the label (and records) and render it with the merged printer settings."""
    ctx = get_current_context()
    settings = resolve_settings(ctx, cli_ctx, **settings_given)
    label = load_label(label_source)
    options = render_options(settings)
    if data is None:
        return render_label(label, options)
    return render_batch(label, load_records(data), options)


def _human(payload: RenderPayload, *, show_dpl: bool) -> str:
    parts = [f"model {payload.model}, {payload.records} label(s), {payload.fields} field(s), {payload.bytes} bytes"]
    if payload.findings:
        parts.append(findings_text(payload.findings))
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
@click.pass_context
def cli_render(ctx: click.Context, label_source: str, data: str | None, output: str | None, **given: Any) -> None:
    """Render a label to DPL and report every finding.

    Without --output the DPL is shown readably (control bytes as <STX>, <CR>). Exit 1 when a
    finding is an error; nothing is written then.
    """

    def body(cli_ctx: CLIContext) -> CommandResult:
        result = build(label_source, data, given, cli_ctx)
        payload = render_payload(result, include_dpl=output is None)
        if output is not None and result.ok:
            _write_output(output, result.data, raw_stdout_allowed=not cli_ctx.mode.machine)
            payload = payload.model_copy(update={"output": output})
        human = "" if output == STDIN else _human(payload, show_dpl=output is None)
        return CommandResult(ok=result.ok, payload=payload, human=human)

    run_command(ctx, command="render", body=body)


def _write_output(output: str, data: bytes, *, raw_stdout_allowed: bool) -> None:
    if output == STDIN:
        if not raw_stdout_allowed:
            msg = "--output - cannot be combined with --json: stdout carries the envelope"
            raise ValueError(msg)
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
        return
    Path(output).write_bytes(data)


@click.command("validate", context_settings=CLICK_CONTEXT_SETTINGS)
@argument("label_source", metavar="LABEL")
@option("--data", default=None, help=_DATA_HELP)
@render_options_decorator
@click.pass_context
def cli_validate(ctx: click.Context, label_source: str, data: str | None, **given: Any) -> None:
    """Check a label against a printer model without printing; exit 1 on an error finding."""

    def body(cli_ctx: CLIContext) -> CommandResult:
        result = build(label_source, data, given, cli_ctx)
        payload = render_payload(result, include_dpl=False)
        verdict = "OK" if result.ok else "REFUSED"
        human = f"{verdict}: {_human(payload, show_dpl=False)}"
        return CommandResult(ok=result.ok, payload=payload, human=human)

    run_command(ctx, command="validate", body=body)


class ExplainPayload(BaseModel):
    """Result of ``explain``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    commands: list[dict[str, str]]
    label: dict[str, Any] | None
    problems: list[str]


@click.command("explain", context_settings=CLICK_CONTEXT_SETTINGS)
@argument("dpl_source", metavar="DPLFILE")
@click.pass_context
def cli_explain(ctx: click.Context, dpl_source: str) -> None:
    """Explain an existing DPL file command by command, and rebuild its label as JSON."""

    def body(_cli_ctx: CLIContext) -> CommandResult:
        explanation = explain(read_input(dpl_source))
        label = explanation.label.model_dump(mode="json") if explanation.label else None
        payload = ExplainPayload(
            commands=[c.model_dump() for c in explanation.commands], label=label, problems=list(explanation.problems)
        )
        lines = [f"{c.kind:9} {c.raw:40} {c.meaning}" for c in explanation.commands] + list(explanation.problems)
        return CommandResult(ok=not explanation.problems, payload=payload, human="\n".join(lines))

    run_command(ctx, command="explain", body=body)


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

    run_command(ctx, command="schema", body=body)


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

    run_command(ctx, command="models", body=body)


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
    features: dict[str, str]


def _speed(text: Any) -> str:
    return f"{text.low.value}-{text.high.value} (default {text.default.value})"


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
        features={feature.value: profile.support(feature).describe() for feature in Feature},
    )


@click.command("capabilities", context_settings=CLICK_CONTEXT_SETTINGS)
@argument("model")
@click.pass_context
def cli_capabilities(ctx: click.Context, model: str) -> None:
    """Show what one printer model can do and which features need firmware or options."""

    def body(_cli_ctx: CLIContext) -> CommandResult:
        payload = capabilities_payload(get_profile(model))
        lines = [f"{key:18} {value}" for key, value in payload.model_dump().items() if key != "features"]
        lines += [f"  {name:20} {state}" for name, state in payload.features.items()]
        return CommandResult(ok=True, payload=payload, human="\n".join(lines))

    run_command(ctx, command="capabilities", body=body)


__all__ = [
    "build",
    "capabilities_payload",
    "cli_capabilities",
    "cli_explain",
    "cli_models",
    "cli_render",
    "cli_schema",
    "cli_validate",
]
