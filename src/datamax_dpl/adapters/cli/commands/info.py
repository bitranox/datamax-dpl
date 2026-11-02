"""The ``info`` command: print the package metadata.

Contents:
    * :func:`cli_info` - Display package metadata.
"""

from __future__ import annotations

import logging

import lib_log_rich.runtime
import rich_click as click
from pydantic import BaseModel, ConfigDict

from datamax_dpl import __init__conf__

from ..constants import CLICK_CONTEXT_SETTINGS
from ..context import output_mode
from ..envelope import CommandName, Envelope, write_envelope

logger = logging.getLogger(__name__)


class InfoPayload(BaseModel):
    """Result of ``info``: the fields its text shows."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    title: str
    version: str
    homepage: str
    author: str
    author_email: str
    shell_command: str


def _info_payload() -> InfoPayload:
    return InfoPayload(
        name=__init__conf__.name,
        title=__init__conf__.title,
        version=__init__conf__.version,
        homepage=__init__conf__.homepage,
        author=__init__conf__.author,
        author_email=__init__conf__.author_email,
        shell_command=__init__conf__.shell_command,
    )


@click.command("info", context_settings=CLICK_CONTEXT_SETTINGS)
@click.pass_context
def cli_info(ctx: click.Context) -> None:
    """Print resolved metadata so users can inspect installation details.

    \f
    Example:
        >>> from click.testing import CliRunner
        >>> runner = CliRunner()
        >>> result = runner.invoke(cli_info)
        >>> result.exit_code == 0
        True
    """
    with lib_log_rich.runtime.bind(job_id="cli-info", extra={"command": "info"}):
        logger.info("Displaying package information")
        mode = output_mode(ctx)
        if mode.machine:
            envelope = Envelope[InfoPayload](ok=True, command=CommandName.INFO, data=_info_payload())
            write_envelope(envelope, mode=mode)
        else:
            __init__conf__.print_info()


__all__ = ["cli_info"]
