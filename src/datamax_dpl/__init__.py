"""datamax-dpl: build, check and print Datamax DPL label jobs.

The library surface, in the order a caller usually needs it:

* the label model (:class:`Label` and its elements) and its enums;
* :func:`render` (raises on an error finding) and :func:`build_job` (reports every finding);
* :func:`render_batch` and :func:`fill_label` for one label per data record, :func:`iter_batch`
  and :func:`check_batch` for a batch too large to hold;
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
from .adapters.transport import open_printer, printer_uri_is_one_way
from .application.ports import Clock, JobSpool, OpenPrinter, PrinterEndpoint, PrinterTransport, Sleep
from .application.print_flow import PrintRefusedError, PrintRequest, print_label
from .application.upload_check import (
    CheckedJob,
    CheckedPrint,
    ResetCheck,
    UploadCheck,
    UploadRefusedError,
    print_checked,
)
from .application.use_cases import (
    AnswerWait,
    BatchEntry,
    BatchReport,
    BatchResult,
    RecordRender,
    ReportedFinding,
    ReportLimits,
    check_batch,
    iter_batch,
    record_renders,
    render_batch,
)
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
from .domain.decoder import DecodedCommand, Explanation, explain
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
    ModelFamily,
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
    BatchTooLargeError,
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
from .domain.profiles import (
    CalibrationDefaults,
    CalibrationKey,
    Feature,
    Firmware,
    PrinterProfile,
    SoftwareSwitchBits,
    Source,
    SpeedRange,
    Support,
    UndescribedBit,
    UnknownModelError,
    get_profile,
    list_profiles,
)
from .domain.qr import QrDataTooLongError
from .domain.render import RenderResult, build_job, render
from .domain.render_context import RenderOptions
from .domain.symbology import Symbology
from .domain.timing import TransportTuning
from .domain.units import Axis, DplUnit

__all__ = [
    "ACROSS_INSET_MM",
    "ALONG_LENGTH_MM",
    "AnswerNotEndedError",
    "AnswerWait",
    "Attribute",
    "Axis",
    "Barcode",
    "BarcodeProblemCode",
    "BatchEntry",
    "BatchReport",
    "BatchResult",
    "BatchTooLargeError",
    "Box",
    "CalibrationDefaults",
    "CalibrationKey",
    "CalibrationLabels",
    "CheckedJob",
    "CheckedPrint",
    "Circle",
    "Clock",
    "CommandKind",
    "ConfigurationError",
    "Counter",
    "Cut",
    "DataMatrixOptions",
    "DatamaxDplError",
    "DecodedCommand",
    "DotSize",
    "DplUnit",
    "Explanation",
    "FallbackPolicy",
    "Feature",
    "FillPattern",
    "Finding",
    "FindingCode",
    "Firmware",
    "HardwareOption",
    "HeldImages",
    "Image",
    "ImageModule",
    "InvalidLoggingConfigError",
    "JobSpool",
    "Label",
    "LabelRejectedError",
    "Line",
    "MediaSensor",
    "MissingFieldError",
    "ModelFamily",
    "OpenPrinter",
    "Pdf417Options",
    "Point",
    "Polygon",
    "PreviewTooLongError",
    "PrintRefusedError",
    "PrintRequest",
    "PrinterEndpoint",
    "PrinterProfile",
    "PrinterProtocolError",
    "PrinterSetting",
    "PrinterStop",
    "PrinterTimeoutError",
    "PrinterTransport",
    "Qr",
    "QrDataTooLongError",
    "QrError",
    "RecordRender",
    "RenderOptions",
    "RenderResult",
    "ReportLimits",
    "ReportedFinding",
    "ResetCheck",
    "Rotation",
    "ScalableText",
    "SentAs",
    "Severity",
    "Sleep",
    "SoftwareSwitchBits",
    "Source",
    "SpeedCode",
    "SpeedRange",
    "StatusProblem",
    "Support",
    "Symbology",
    "Text",
    "TransportError",
    "TransportTuning",
    "UndescribedBit",
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
    "check_batch",
    "explain",
    "fill_label",
    "get_config",
    "get_profile",
    "iter_batch",
    "list_profiles",
    "open_printer",
    "pillow_available",
    "preview_findings",
    "print_checked",
    "print_info",
    "print_label",
    "printer_uri_is_one_way",
    "record_renders",
    "render",
    "render_batch",
    "render_preview_png",
]
