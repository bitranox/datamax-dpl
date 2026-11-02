"""datamax-dpl: build, check and print Datamax DPL label jobs.

The library surface, in the order a caller usually needs it:

* the label model (:class:`Label` and its elements) and its enums;
* :func:`render` (raises on an error finding) and :func:`build_job` (reports every finding);
* :func:`render_batch` and :func:`fill_label` for one label per data record;
* :func:`open_printer` to send the bytes, :func:`print_checked` to send them checking each label's
  image upload, :func:`explain` to read DPL back;
* :func:`get_profile` / :func:`list_profiles` for the printer models;
* :func:`render_preview_png` for a picture of the label, :func:`preview_findings` for what that
  picture approximates (text without Pillow, see :func:`pillow_available`).

Example:
    >>> from datamax_dpl import Label, RenderOptions, Text, render
    >>> job = render(Label(elements=[Text(x=5, y=5, text="Hello")]), RenderOptions(model="dmx-600"))
    >>> job.data[:8]
    b'\\x02n\\x02c0000'
"""

from __future__ import annotations

from .__init__conf__ import print_info
from .adapters.logging.setup import InvalidLoggingConfigError
from .adapters.preview import pillow_available, preview_findings, render_preview_png
from .adapters.transport import open_printer
from .application.ports import PrinterTransport
from .application.upload_check import CheckedJob, ResetCheck, UploadCheck, UploadRefusedError, print_checked
from .application.use_cases import AnswerWait, BatchResult, record_renders, render_batch
from .composition import get_config
from .domain.calibration import (
    ACROSS_INSET_MM,
    ALONG_LENGTH_MM,
    CalibrationLabels,
    across_frame_width,
    across_label,
    along_label,
    calibration_labels,
)
from .domain.decoder import Explanation, explain
from .domain.enums import (
    Attribute,
    BarcodeProblemCode,
    CommandKind,
    FallbackPolicy,
    FillPattern,
    FindingCode,
    HardwareOption,
    ImageModule,
    MediaSensor,
    MediaType,
    PrinterSetting,
    PrinterStop,
    QrError,
    Rotation,
    Severity,
    SpeedCode,
    StatusProblem,
    Unit,
)
from .domain.errors import (
    AnswerNotEndedError,
    CameraError,
    ConfigurationError,
    DatamaxDplError,
    LabelRejectedError,
    PreviewTooLongError,
    PrinterProtocolError,
    PrinterTimeoutError,
    TransportError,
    UnsupportedOperationError,
)
from .domain.findings import Finding
from .domain.held_images import HeldImages
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
from .domain.placeholders import MissingFieldError, fill_label
from .domain.preview import SentAs
from .domain.profiles import PrinterProfile, UnknownModelError, get_profile, list_profiles
from .domain.qr import QrDataTooLongError
from .domain.render import RenderResult, build_job, render
from .domain.render_context import RenderOptions
from .domain.symbology import Symbology
from .domain.timing import TransportTuning
from .domain.units import DplUnit

__all__ = [
    "ACROSS_INSET_MM",
    "ALONG_LENGTH_MM",
    "AnswerNotEndedError",
    "AnswerWait",
    "Attribute",
    "Barcode",
    "BarcodeProblemCode",
    "BatchResult",
    "Box",
    "CalibrationLabels",
    "CameraError",
    "CheckedJob",
    "Circle",
    "CommandKind",
    "ConfigurationError",
    "Counter",
    "Cut",
    "DataMatrixOptions",
    "DatamaxDplError",
    "DotSize",
    "DplUnit",
    "Explanation",
    "FallbackPolicy",
    "FillPattern",
    "Finding",
    "FindingCode",
    "HardwareOption",
    "HeldImages",
    "Image",
    "ImageModule",
    "InvalidLoggingConfigError",
    "Label",
    "LabelRejectedError",
    "Line",
    "MediaSensor",
    "MediaType",
    "MissingFieldError",
    "Pdf417Options",
    "Point",
    "Polygon",
    "PreviewTooLongError",
    "PrinterProfile",
    "PrinterProtocolError",
    "PrinterSetting",
    "PrinterStop",
    "PrinterTimeoutError",
    "PrinterTransport",
    "Qr",
    "QrDataTooLongError",
    "QrError",
    "RenderOptions",
    "RenderResult",
    "ResetCheck",
    "Rotation",
    "ScalableText",
    "SentAs",
    "Severity",
    "SpeedCode",
    "StatusProblem",
    "Symbology",
    "Text",
    "TransportError",
    "TransportTuning",
    "Unit",
    "UnknownModelError",
    "UnsupportedOperationError",
    "UploadCheck",
    "UploadRefusedError",
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
    "pillow_available",
    "preview_findings",
    "print_checked",
    "print_info",
    "record_renders",
    "render",
    "render_batch",
    "render_preview_png",
]
