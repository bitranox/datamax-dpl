"""The feed that returns the printer from continuous media to a label sensor.

A run of labels on continuous media leaves the printer in continuous mode, so the stock stops
wherever the last label ended. On one Allegro 2 (firmware CA02 03.04, 2026-11-05) ending continuous
mode (``<STX>c0000``), choosing the gap sensor (``<STX>e``), sending the tear-off stop
(``<STX>f263``) and feeding once (``<STX>F``) left the stock at the tear-off position, in both
prints. :func:`media_feed` builds that sequence with the commands a label's own job would send for
the same media and stop, so a model without a sensor or stop command gets the same findings too.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .enums import MediaSensor, Severity
from .job import media_and_stop
from .label import Label, Text
from .render import job_context
from .render_context import RenderOptions
from .status import FORM_FEED

if TYPE_CHECKING:
    from .findings import Finding

#: The sensors the feed can return to; continuous media has no edge to feed to.
FEED_MEDIA = (MediaSensor.GAP, MediaSensor.REFLECTIVE)


@dataclass(frozen=True)
class MediaFeed:
    """The bytes of a media feed and what building them noticed.

    Attributes:
        data: The commands to send, the form feed last.
        findings: Every finding, in order: the model defaults used, a sensor or stop the model
            cannot set.
    """

    data: bytes
    findings: tuple[Finding, ...]

    @property
    def errors(self) -> tuple[Finding, ...]:
        """The findings that stop the feed from being sent."""
        return tuple(finding for finding in self.findings if finding.severity is Severity.ERROR)


def media_feed(media: MediaSensor, options: RenderOptions | None = None) -> MediaFeed:
    """Return the feed that ends continuous mode, selects ``media`` and stops where configured.

    The media goes out whatever ``send_settings`` holds: setting it is what the feed is for. The
    stop position is the configured one (``stop_position_mm``, else the model's default), as in a
    job that does not cut; ``"printer"`` sends none.

    Args:
        media: The sensor to return to, gap or reflective.
        options: Render options: the model and its stop position.

    Returns:
        The commands and the findings.

    Raises:
        ValueError: ``media`` is continuous.
        UnknownModelError: ``options.model`` names no profile.

    Example:
        >>> media_feed(MediaSensor.GAP, RenderOptions(model="allegro-2")).data
        b'\\x02n\\x02c0000\\x02e\\x02f263\\x02F'
    """
    if media not in FEED_MEDIA:
        raise ValueError(f"a feed returns to the gap or reflective sensor, not to {media.value} media")
    chosen = (options or RenderOptions()).model_copy(update={"media": media, "send_settings": None})
    # A label stands in for the job: the media and the stop come from the options, as for every
    # job, and the label asks for neither, so a model that cannot set them only reports it.
    label = Label(elements=[Text(x=0, y=0, text="0")])
    ctx = job_context(label, chosen)
    commands = media_and_stop(label, ctx)
    return MediaFeed(data=b"".join([*commands, FORM_FEED]), findings=ctx.findings.findings())


__all__ = ["FEED_MEDIA", "MediaFeed", "media_feed"]
