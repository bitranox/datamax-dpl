"""datamax-dpl: build, check and print Datamax DPL label jobs.

The library surface, in the order a caller usually needs it:

* the label model (:class:`Label` and its elements) and its enums;
* :func:`render` (raises on an error finding) and :func:`build_job` (reports every finding);
* :func:`render_batch` and :func:`fill_label` for one label per data record;
* :func:`open_printer` to send the bytes, :func:`explain` to read DPL back;
* :func:`get_profile` / :func:`list_profiles` for the printer models;
* :func:`render_preview_png` for a picture of the label.

Example:
    >>> from datamax_dpl import Label, RenderOptions, Text, render
    >>> job = render(Label(elements=[Text(x=5, y=5, text="Hello")]), RenderOptions(model="dmx-600"))
    >>> job.data[:3]
    b'\\x02L\\r'
"""

from __future__ import annotations

from .__init__conf__ import print_info
from .adapters.preview import render_preview_png
from .adapters.transport import open_printer
from .application.use_cases import BatchResult, render_batch
from .composition import get_config
from .domain.calibration import (
    ACROSS_INSET_MM,
    ALONG_LENGTH_MM,
    across_frame_width,
    across_label,
    along_label,
    calibration_labels,
)
from .domain.decoder import explain
from .domain.enums import (
    Attribute,
    BarcodeProblemCode,
    CommandKind,
    FallbackPolicy,
    FillPattern,
    FindingCode,
    HardwareOption,
    MediaSensor,
    MediaType,
    QrError,
    Rotation,
    Severity,
    SpeedCode,
    StatusProblem,
    Unit,
)
from .domain.errors import LabelRejectedError, TransportError
from .domain.findings import Finding
from .domain.label import (
    Barcode,
    Box,
    Circle,
    Counter,
    Cut,
    DataMatrixOptions,
    DotSize,
    Image,
    Label,
    Line,
    Pdf417Options,
    Point,
    Polygon,
    Qr,
    ScalableText,
    Text,
)
from .domain.placeholders import fill_label
from .domain.profiles import PrinterProfile, get_profile, list_profiles
from .domain.render import RenderResult, build_job, render
from .domain.render_context import RenderOptions
from .domain.symbology import Symbology

__all__ = [
    "ACROSS_INSET_MM",
    "ALONG_LENGTH_MM",
    "Attribute",
    "Barcode",
    "BarcodeProblemCode",
    "BatchResult",
    "Box",
    "Circle",
    "CommandKind",
    "Counter",
    "Cut",
    "DataMatrixOptions",
    "DotSize",
    "FallbackPolicy",
    "FillPattern",
    "Finding",
    "FindingCode",
    "HardwareOption",
    "Image",
    "Label",
    "LabelRejectedError",
    "Line",
    "MediaSensor",
    "MediaType",
    "Pdf417Options",
    "Point",
    "Polygon",
    "PrinterProfile",
    "Qr",
    "QrError",
    "RenderOptions",
    "RenderResult",
    "Rotation",
    "ScalableText",
    "Severity",
    "SpeedCode",
    "StatusProblem",
    "Symbology",
    "Text",
    "TransportError",
    "Unit",
    "across_frame_width",
    "across_label",
    "along_label",
    "build_job",
    "calibration_labels",
    "explain",
    "fill_label",
    "get_config",
    "get_profile",
    "list_profiles",
    "open_printer",
    "print_info",
    "render",
    "render_batch",
    "render_preview_png",
]
