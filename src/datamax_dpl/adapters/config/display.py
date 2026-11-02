"""Display configuration - delegates to lib_layered_config.

Thin wrapper around lib_layered_config's Rich-styled display_config,
adding log flush before output to prevent mixing log messages with
configuration display.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

import lib_log_rich.runtime
from lib_layered_config import Config
from lib_layered_config import OutputFormat as LibOutputFormat
from lib_layered_config import display_config as _lib_display
from lib_layered_config.domain.redaction import REDACTED_PLACEHOLDER

from datamax_dpl.domain.enums import OutputFormat

from .overrides import with_data
from .sections import CAMERA_SECTION

if TYPE_CHECKING:
    from rich.console import Console

_URL_KEY = "url"


def redact_url_password(url: str) -> str:
    """Return ``url`` with the password of its userinfo masked, every other part as written.

    Splits by hand the way ``urlsplit`` finds the password the HTTP camera uses, rather than by
    calling it: ``urlsplit`` raises on a malformed host, and a display must still mask that.

    Args:
        url: The configured value, which need not be a valid URL.

    Returns:
        The value with a non-empty password replaced by the redaction placeholder.

    Example:
        >>> redact_url_password("http://u:secret@192.0.2.9/snap.jpg")
        'http://u:***REDACTED***@192.0.2.9/snap.jpg'
        >>> redact_url_password("http://192.0.2.9/a:b@c")
        'http://192.0.2.9/a:b@c'
    """
    scheme, slashes, rest = url.partition("//")
    # The authority ends at the first of these; an "@" further on belongs to the path or the query.
    end = min((rest.find(mark) for mark in "/?#" if mark in rest), default=len(rest))
    userinfo, at, host = rest[:end].rpartition("@")
    user, colon, password = userinfo.partition(":")
    if not (slashes and at and colon and password):
        return url
    return f"{scheme}{slashes}{user}{colon}{REDACTED_PLACEHOLDER}{at}{host}{rest[end:]}"


def _shown_config(config: Config) -> Config:
    """``config`` with the camera URL's password masked; the library redacts by key name only."""
    camera: object = config.get(CAMERA_SECTION.dotted)
    if not isinstance(camera, Mapping):
        return config
    url = cast("Mapping[str, Any]", camera).get(_URL_KEY)
    if not isinstance(url, str) or redact_url_password(url) == url:
        return config
    data = config.as_dict()
    data[CAMERA_SECTION.dotted][_URL_KEY] = redact_url_password(url)
    return with_data(config, data)


def display_config(
    config: Config,
    *,
    output_format: OutputFormat = OutputFormat.HUMAN,
    section: str | None = None,
    console: Console | None = None,
    profile: str | None = None,
) -> None:
    """Display configuration using lib_layered_config's Rich display.

    Flushes any pending log output before displaying to prevent
    mixing log messages with configuration output.

    Args:
        config: Already-loaded layered configuration object to display.
        output_format: Output format: OutputFormat.HUMAN for TOML-like display or
            OutputFormat.JSON for JSON. Defaults to OutputFormat.HUMAN.
        section: Optional section name to display only that section. When None,
            displays all configuration.
        console: Optional Rich Console for output. When None, uses the module-level
            default. Primarily useful for testing.
        profile: Optional profile name to include in provenance comments.

    Side Effects:
        Flushes pending log messages before display.
        Writes formatted configuration to stdout.

    Raises:
        ValueError: If a section was requested that doesn't exist.
    """
    if lib_log_rich.runtime.is_initialised():
        lib_log_rich.runtime.flush()

    lib_format = LibOutputFormat(output_format.value)
    _lib_display(_shown_config(config), output_format=lib_format, section=section, profile=profile, console=console)


__all__ = ["display_config", "redact_url_password"]
