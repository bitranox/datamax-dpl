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
# What Config.get returns for a missing key, as a key may hold None.
_ABSENT = object()
#: A query parameter whose name holds one of these is taken for a secret.
_SECRET_QUERY_WORDS = ("pass", "pwd", "secret", "token", "key")


def _redact_query(rest: str) -> str:
    """Mask the values of the query parameters of ``rest`` whose name holds a secret's word."""
    before, mark, tail = rest.partition("?")
    query, hash_mark, fragment = tail.partition("#")
    if not mark:
        return rest
    pairs = []
    for pair in query.split("&"):
        name, equals, value = pair.partition("=")
        secret = equals and value and any(word in name.lower() for word in _SECRET_QUERY_WORDS)
        pairs.append(f"{name}{equals}{REDACTED_PLACEHOLDER}" if secret else pair)
    return f"{before}{mark}{'&'.join(pairs)}{hash_mark}{fragment}"


def redact_url_secrets(url: str) -> str:
    """Return ``url`` with the password of its userinfo and secret query values masked, the rest as written.

    Splits by hand the way ``urlsplit`` finds the password the HTTP camera uses, rather than by
    calling it: ``urlsplit`` raises on a malformed host, and a display must still mask that.

    A query parameter counts as secret when its name holds one of ``_SECRET_QUERY_WORDS``: a camera
    behind a token takes it there, and a wrongly masked value costs only what ``config`` shows.

    Args:
        url: The configured value, which need not be a valid URL.

    Returns:
        The value with each non-empty secret replaced by the redaction placeholder.

    Example:
        >>> redact_url_secrets("http://u:secret@192.0.2.9/snap.jpg")
        'http://u:***REDACTED***@192.0.2.9/snap.jpg'
        >>> redact_url_secrets("http://192.0.2.9/a:b@c")
        'http://192.0.2.9/a:b@c'
        >>> redact_url_secrets("http://192.0.2.9/s.jpg?size=1&api_token=abc#top")
        'http://192.0.2.9/s.jpg?size=1&api_token=***REDACTED***#top'
    """
    scheme, slashes, rest = url.partition("//")
    # The authority ends at the first of these; an "@" further on belongs to the path or the query.
    end = min((rest.find(mark) for mark in "/?#" if mark in rest), default=len(rest))
    userinfo, at, host = rest[:end].rpartition("@")
    user, colon, password = userinfo.partition(":")
    tail = _redact_query(rest[end:])
    if not (slashes and at and colon and password):
        return f"{scheme}{slashes}{rest[:end]}{tail}"
    return f"{scheme}{slashes}{user}{colon}{REDACTED_PLACEHOLDER}{at}{host}{tail}"


def _shown_config(config: Config) -> Config:
    """``config`` with the camera URL's secrets masked; the library redacts by key name only."""
    camera: object = config.get(CAMERA_SECTION.dotted)
    if not isinstance(camera, Mapping):
        return config
    url = cast("Mapping[str, Any]", camera).get(_URL_KEY)
    if not isinstance(url, str) or redact_url_secrets(url) == url:
        return config
    data = config.as_dict()
    data[CAMERA_SECTION.dotted][_URL_KEY] = redact_url_secrets(url)
    return with_data(config, data)


def config_view(config: Config, *, section: str | None = None) -> dict[str, Any]:
    """What ``config --format json`` prints, as data: redacted, the whole tree or one section.

    Args:
        config: The loaded configuration.
        section: A dotted section or key to show only that; None shows everything.

    Returns:
        The redacted tree, or ``{section: value}``.

    Raises:
        ValueError: If a section was requested that doesn't exist.

    Example:
        >>> config_view(Config({"camera": {"password": "x", "kind": "http"}}, {}), section="camera")
        {'camera': {'password': '***REDACTED***', 'kind': 'http'}}
    """
    shown = _shown_config(config)
    if section is None:
        return shown.as_dict(redact=True)
    if shown.get(section, default=_ABSENT) is _ABSENT:
        raise ValueError(f"Section '{section}' not found")
    return {section: shown.get_redacted(section)}


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
    # The library reads an empty name as "everything"; config_view calls it not found.
    if section == "":
        raise ValueError("Section '' not found")
    if lib_log_rich.runtime.is_initialised():
        lib_log_rich.runtime.flush()

    lib_format = LibOutputFormat(output_format.value)
    _lib_display(_shown_config(config), output_format=lib_format, section=section, profile=profile, console=console)


__all__ = ["config_view", "display_config", "redact_url_secrets"]
