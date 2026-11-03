"""Check that a job's images reached the printer before the format that places them goes out.

A download into a full image module is lost with no error, and the printer answered nothing to the
next directory request on that connection (one Allegro 2, 2026-10-31). How much of a module an
image takes depends on its content, not on its dot count (a random 4096-byte image took 16582
bytes, a striped one of the same size 281), so no count made before sending can tell. On a two-way
link ``print`` and ``verify`` therefore send each label that downloads an image in two parts: first
everything up to and including its downloads, then, once the printer's graphic directory
(``<STX>W``) lists every name the label downloaded, the rest (its format, any cut, the clear
after). A name missing, or no answer within ``stored_names_wait``, starts the next round, up to
:attr:`UploadCheck.rounds`:

1. upload and read;
2. clear the module where a clear may go (:attr:`~datamax_dpl.domain.render.RenderResult.retry_clear`:
   never a plug-in module), upload again and read again;
3. clear again, halve the images 2x2 (the image memory budget's own halving), upload and read.

A label no clear precedes reads the printer's directory right before its downloads, and is
rendered again from what it lists, so a name the module holds is placed rather than downloaded
again, which would print nothing. Where no clear may go (a plug-in module, a model with no clear) a
round renders the label again with what the last read listed, for the same reason. Where the read
got no answer, each name sent before it may be held: round 2 then reads the names again before it
uploads anything; with an answer the label is rendered again from that listing and the round goes
on (a label left with nothing to download passes, at full size), with none round 2 is left out.
Round 3 runs after a read with no answer only where every download gets a new name by halving,
which a QR Code never does. On the plain
Prodigy, whose removes take out only the label's own names, round 3 renders from what the last
read that answered listed less those names, so a halved name the module holds is placed, not
downloaded again. A label that, rendered again for a round, has an error finding (the module it
now holds leaves too little memory, a held name in error) is refused there
(``image_upload_render_refused``), before anything of that round is sent. One that, rendered
again, downloads nothing (each image it places is one the module holds) sends no upload and makes
no read after it in that round: nothing of it can be missing.

Before the first label's downloads, once it is known not to be refused (where no clear precedes
them, after the names read before them), the printer is asked for its status once
(:class:`ResetCheck`): its first answer after a power cycle reports the reset, and one Allegro 2
printed the first label after one about 12 mm shifted. On such a report one calibration label goes
first, so that the shift lands on it (``printer_reset_calibrated``); a names read that already
carried the report counts, and nothing more is asked. A status read that stays silent can still be
followed by a directory read that answers with the report
(:attr:`~datamax_dpl.domain.held_images.HeldImages.reset_reported`): the first label's format has
not gone yet then, so the calibration label goes at that point, and a label those rounds refuse
says the report was taken with nothing printed (``printer_reset_not_calibrated``). Nothing here
reads a reset as an empty module, and a label the upload check refuses later still had its
calibration label: only uploading can tell.

Still missing: the label is refused (``image_upload_refused``), its format is never sent, nothing
after it is sent, and the module is cleared again where a clear may go. Each round after the
first adds an ``image_upload_retried`` finding saying what it did and what the printer then listed.

A job whose images go to module C, the printer's default module, which no directory lists under
that letter, is refused before anything is sent (``image_module_unlisted``): the check could never
pass, and the format may not go out before it has.

In a batch each label is checked on its own, and nothing passes from one label to the next: the
printer may have been reset, or another program may have printed, in between, so its directory is
the one source of what it holds. Each label no clear precedes reads it right before its downloads,
so such a batch costs one read more per label than its upload check, about a second. Where that
read gets no answer, the job's first label downloads every image and says the names were not read
(``image_names_unread``), as a single label does; a later label, which a label before it may have
left any of its names for, has its round 1 left out, and round 2 reads the names again before it
uploads any of them (where no clear may go; where one may, round 2 clears first). A label that,
rendered again with what a read listed, has an error finding stops the batch there: none of it and
nothing after it is sent.
"""

from __future__ import annotations

from collections.abc import Sized
from dataclasses import dataclass, field
from itertools import islice
from typing import TYPE_CHECKING, Generic, NamedTuple, Protocol, TypedDict, TypeVar, overload

from ..domain._invariants import require_update_fields
from ..domain.batch import with_cut
from ..domain.enums import FindingCode, ImageModule, Severity
from ..domain.errors import ConfigurationError, LabelRejectedError
from ..domain.findings import Finding
from ..domain.held_images import EMPTY_MODULE, NOT_ASKED, HeldImages
from ..domain.profiles import get_profile
from ..domain.render import RenderResult
from ..domain.reset_label import (
    CALIBRATE_AFTER_RESET,
    CALIBRATION_LABEL_TEXT,
    RESET_CHECK_WAIT_SECONDS,
    check_label_text,
)
from ..domain.timing import TimeoutName, check_timeout
from .use_cases import (
    NO_UNKEPT,
    AnswerWait,
    BatchReport,
    BatchResult,
    BatchTally,
    error_findings,
    open_within,
    read_reset_report,
    read_stored_names,
    unkept_of,
    unlisted_download,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from .ports import PrinterEndpoint, PrinterTransport
    from .use_cases import RecordRender

_Sent_co = TypeVar("_Sent_co", bound="RenderResult | BatchResult | BatchReport", covariant=True)


class _FindingsUpdate(TypedDict):
    """The field a checked label's result gets as it went out: its findings, the check's added."""

    findings: tuple[Finding, ...]


require_update_fields(_FindingsUpdate, RenderResult)

#: Default of ``[printer] upload_check_rounds``: upload, then a clear and a second upload, then the
#: images halved and a third upload, before a label is refused (user decision, 2026-11-01).
UPLOAD_CHECK_ROUNDS = 3
#: The round that clears the module and uploads again, and the round that uploads the images halved.
CLEAR_ROUND = 2
HALVING_ROUND = 3
#: The fewest and the most rounds ``upload_check_rounds`` takes.
MIN_UPLOAD_CHECK_ROUNDS = 1
MAX_UPLOAD_CHECK_ROUNDS = HALVING_ROUND


class UploadCheck(NamedTuple):
    """How ``print`` and ``verify`` check each label's image upload on a two-way link.

    Attributes:
        wait: How long each read of the directory keeps trying (``stored_names_wait``).
        rounds: How many rounds run before a label is refused: 1 uploads and reads once, 2 adds a
            clear and a second upload, 3 (the default) adds the images halved and a third upload.
    """

    wait: AnswerWait
    rounds: int = UPLOAD_CHECK_ROUNDS


@dataclass(frozen=True)
class ResetCheck:
    """How ``print`` and ``verify`` ask for a reset report before a job, and what they do on one.

    Attributes:
        wait: Seconds the one status read waits (``reset_check_wait``). It runs before every job.
        calibrate: True prints one calibration label on a report; False only reports it
            (``calibrate_after_reset``).
        text: What the calibration label says (``calibration_label_text``).
        off_because: Why no label goes when ``calibrate`` is False, quoted in the finding: a caller
            with a setting for it names the setting.

    Raises:
        ValueError: ``wait`` is not a usable number of seconds, or ``text`` is no text field's
            value; refused here, before anything is sent.

    Example:
        >>> ResetCheck().wait, ResetCheck().text
        (3.0, 'Printer Reset - Calibration Feed')
    """

    wait: float = RESET_CHECK_WAIT_SECONDS
    calibrate: bool = CALIBRATE_AFTER_RESET
    text: str = CALIBRATION_LABEL_TEXT
    off_because: str = "calibration labels are turned off"

    def __post_init__(self) -> None:
        """Refuse a value that could only fail once a job is on its way."""
        check_timeout(self.wait, name=TimeoutName.RESET_CHECK_WAIT)
        check_label_text(self.text)


@dataclass(frozen=True)
class CheckedPrint(Generic[_Sent_co]):
    """What a checked send sent where, and the job as it went out, of the kind that was given.

    Attributes:
        uri: The printer.
        bytes_sent: Job bytes written, every round's uploads and clears included, and a calibration
            label's.
        result: The job as it went out: each label with its rounds' findings, a halved label as halved.
    """

    uri: str
    bytes_sent: int
    result: _Sent_co


class CheckedJob(NamedTuple):
    """A job to send with its upload check, and how to render each of its labels again.

    The renders and the job they rebuild are rendered with the same options; with
    ``model_image_budget`` False, as ``print`` and ``verify`` render for a two-way link, the images
    go at full size and the check, not the model's budget, decides whether they are halved.

    Attributes:
        renders: One render per label, in the order they are sent: ``record_renders``, or
            :meth:`~datamax_dpl.application.use_cases.BatchReport.renders`, which builds each one
            only when its turn comes and holds only the current one.
        check: How the upload is checked.
        reset: How a reset report is asked for before the first label; None asks nothing.
    """

    renders: Iterable[RecordRender]
    check: UploadCheck
    reset: ResetCheck | None = ResetCheck()


@dataclass(frozen=True)
class CheckedSend(Generic[_Sent_co]):
    """A job sent label by label with its upload check, as it went out.

    Attributes:
        result: The job as it went out, each label reached with the rounds' findings.
        all_sent: True when every label was sent; False when a label was refused, which nothing
            after it follows.
    """

    result: _Sent_co
    all_sent: bool


class UploadRefusedError(LabelRejectedError):
    """The printer did not list a label's images after every round, or never could; that label's format was never sent.

    Or the label, rendered again for what the printer then held, has an error finding
    (``image_upload_render_refused``); its format was never sent either.

    Attributes:
        result: The job as far as it went, the refused label's ``image_upload_refused`` (or
            ``image_upload_render_refused``) included.
        bytes_sent: Bytes of the job written before the refusal (labels before it, the refused
            label's uploads and clears).
    """

    def __init__(self, result: RenderResult | BatchResult | BatchReport, *, bytes_sent: int) -> None:
        """Keep the job as far as it went and the bytes it wrote."""
        errors = tuple(f for f in _all_findings(result) if f.severity is Severity.ERROR)
        super().__init__(_refusal_text({f.code for f in errors}), errors)
        self.result = result
        self.bytes_sent = bytes_sent


def _refusal_text(codes: set[FindingCode]) -> str:
    """Say why a checked job stopped, from the codes of its error findings."""
    if FindingCode.IMAGE_MODULE_UNLISTED in codes:
        return "no directory lists module C, so a label's images could not be checked; nothing was sent"
    if FindingCode.IMAGE_UPLOAD_RENDER_REFUSED in codes:
        return (
            "a label, rendered again during its upload check for what the printer then held, has errors; "
            "its format was not sent, nor anything after it"
        )
    if FindingCode.IMAGE_UPLOAD_REFUSED in codes:
        return "the printer did not store a label's images; its format was not sent"
    return (
        "a label of the batch, rendered again with what the printer then held, has errors; "
        "it and the labels after it were not sent"
    )


def _all_findings(result: RenderResult | BatchResult | BatchReport) -> tuple[Finding, ...]:
    if isinstance(result, BatchReport):
        return tuple(result.all_findings())
    results = result.results if isinstance(result, BatchResult) else (result,)
    return tuple(f for r in results for f in r.findings)


@dataclass
class Link:
    """The channel a checked job goes out on, opened again when a read lost it.

    Attributes:
        printer: The open channel, None after a read that failed closed it.
        endpoint: The printer, through which a new channel opens.
        reconnect: How long a send keeps trying to open a new channel where a failed read left
            none (``stored_names_wait``, as the read itself).
        bytes_sent: Job bytes written so far, the directory requests not counted.
        reset_seen: A read of the directory carried a reset report.
    """

    printer: PrinterTransport | None
    endpoint: PrinterEndpoint
    reconnect: AnswerWait
    bytes_sent: int = 0
    reset_seen: bool = False
    _opened: PrinterTransport | None = None

    def send(self, data: bytes) -> None:
        """Write ``data`` on the open channel, opening one first when a failed read left none.

        A channel the far end has closed is given up first: a print server that drops a
        connection idle for a moment does so during the directory read, the one pause in a checked
        job, and the rest written there would be lost while every write succeeds. A close that
        comes after this write is not seen.
        """
        if not data:
            return
        if self.printer is not None and self.printer.peer_closed():
            self.printer.close()
            self.printer = None
        self.channel().send(data)
        self.bytes_sent += len(data)

    def channel(self) -> PrinterTransport:
        """The open channel, a new one when a failed read left none; :meth:`close_opened` closes that one."""
        if self.printer is None:
            self.printer = open_within(endpoint=self.endpoint, wait=self.reconnect)
            self._hold_opened(self.printer)
        return self.printer

    def read(self, module: ImageModule, *, wait: AnswerWait) -> HeldImages:
        """Read what ``module`` holds on the open channel; keep the channel that answered."""
        before = self.printer
        read = read_stored_names(self.printer, module=module, endpoint=self.endpoint, wait=wait)
        self.printer = read.channel
        if self.printer is not None and self.printer is not before:
            self._hold_opened(self.printer)
        self.reset_seen = self.reset_seen or read.held.reset_reported
        return read.held

    def _hold_opened(self, printer: PrinterTransport) -> None:
        """Hold ``printer`` as the channel this link opened, closing the one it replaces.

        A print server can drop a connection at each read, so a list of them would grow with the labels.
        """
        if self._opened is not None and self._opened is not printer:
            self._opened.close()
        self._opened = printer

    def close_opened(self) -> None:
        """Close the channel this link opened itself, if any; the one it was given stays the caller's."""
        if self._opened is not None:
            self._opened.close()


class _Found(NamedTuple):
    """What a read found of a label's downloads.

    Attributes:
        missing: The names the label downloads that the read did not list: all of them when it
            got no answer.
        text: What the read found, for a finding's message.
    """

    missing: tuple[str, ...]
    text: str


def _found(result: RenderResult, held: HeldImages, module: ImageModule) -> _Found:
    """What the read that gave ``held`` found of ``result``'s downloads into ``module``."""
    names = tuple(dict.fromkeys(result.downloads))
    if held.listed is None:
        return _Found(missing=names, text=f"module {module.value} could not be read ({held.unread_because})")
    missing = tuple(name for name in names if name not in held.listed)
    if not missing:
        return _Found(missing=(), text=f"module {module.value} then listed every image")
    return _Found(missing=missing, text=f"module {module.value} did not list {', '.join(missing)}")


def _clear_text(result: RenderResult, module: ImageModule) -> str:
    """Say which clear went before an upload again into ``module``, for a finding's message."""
    if result.retry_clear_empties:
        return f"cleared module {module.value}"
    if result.retry_clear:
        return "removed the label's own image names"
    return f"sent no clear (module {module.value} may not be cleared)"


@dataclass
class _Record:
    """One label's rounds: its render, what the reads made for it found, and the rounds' findings.

    Nothing comes in from the label before it: what the printer holds is what a read of it says.
    """

    render: RecordRender
    link: Link
    check: UploadCheck
    # The job's first label: nothing of this job went out before it.
    first: bool
    result: RenderResult = field(init=False)
    findings: list[Finding] = field(default_factory=list[Finding])
    _last_read: str = field(default="", init=False)
    # What the last read of this label that answered listed, less what every clear since took out
    # (see _clear). Unknown where no read has answered yet: then nothing is known to leave out.
    _known: frozenset[str] | None = field(default=None, init=False)
    # Names downloaded since the last read that answered: each may be held, or may be lost.
    _unsure: frozenset[str] = field(default=frozenset(), init=False)
    # The read before the downloads got no answer, on a label after the first, and none has since:
    # a label before it may have left any name it downloads, so each one may be held.
    _blind: bool = field(default=False, init=False)
    # Why the names are unknown, from the last read that got no answer.
    _unread_because: str = field(default="", init=False)
    # Names this label's own uploads sent: listed later, each holds the label's own picture.
    _own_names: frozenset[str] = field(default=frozenset(), init=False)
    # The label as first sent, whose findings a spool may have kept only some of (see unkept).
    _first_result: RenderResult = field(init=False)

    def __post_init__(self) -> None:
        """Take the label as it is first sent."""
        self.result = self.render.first()
        self._first_result = self.result

    def read_before(self) -> _Skipped | _Refused | None:
        """Read the printer's names right before the downloads of a label no clear precedes.

        With an answer the label is rendered again from it, so each name the printer lists is
        placed rather than downloaded again, which would print nothing. With none, the job's first
        label downloads every image and says the names were not read (``image_names_unread``), as
        a single label does; a later label has round 1 left out, since a label before it may have
        left any of its names, and round 2 reads again before it uploads any of them.

        Returns:
            None where round 1 may run; :class:`_Skipped` where it is left out; :class:`_Refused`
            where the label, rendered again, has an error.
        """
        if not self.result.names_unread:
            return None
        listed = self.link.read(self._module(), wait=self.check.wait)
        if listed.listed is not None:
            return self._read_answered(listed.listed, when="from the names read before its downloads")
        self._unread_because = listed.unread_because
        if self.first:
            again = self.render(listed, halve=False)
            if not again.ok:
                return _Refused.of(again, when="with its names unread")
            self.result = again
            return None
        self._blind = True
        skipped = _Skipped(
            f"round 1 skipped: the names read before the label's downloads got no answer ({listed.unread_because}), "
            "and a label before it in the job may have left any name it downloads, which downloaded again prints "
            "nothing"
        )
        self._retried(skipped.reason)
        return skipped

    def _read_answered(self, listed: frozenset[str], *, when: str) -> _Refused | None:
        """Take ``listed`` as what the module holds and render the label again from it."""
        self._known, self._unsure, self._blind = listed, frozenset(), False
        again = self.render(self._as_listed(listed), halve=False)
        if not again.ok:
            return _Refused.of(again, when=when)
        self.result = again
        return None

    def _module(self) -> ImageModule:
        """The module the label's images go to.

        Raises:
            ValueError: The label has no module. A label is checked only when it downloads, and a
                download needs a module: a job without one is refused (``image_module_unknown``)
                before its check, so this names a broken caller, not a printer or a label.
        """
        module = self.result.image_module
        if module is None:
            msg = "a label whose upload is checked downloads its images into a module"
            raise ValueError(msg)
        return module

    def upload(self) -> tuple[str, ...]:
        """Send the label's upload, read the directory and return the names it does not list.

        A label that, rendered again for a round, downloads nothing (each image it places is one
        the module holds) sends no upload and reads nothing: nothing is missing, and a read would
        tell nothing about this label.
        """
        if not self.result.downloads:
            return ()
        module = self._module()
        self.link.send(self.result.data[: self.result.upload_end])
        self._own_names |= frozenset(self.result.downloads)
        listed = self.link.read(module, wait=self.check.wait)
        if listed.listed is None:
            self._unsure |= frozenset(self.result.downloads)
            self._unread_because = listed.unread_because
        else:
            self._known, self._unsure, self._blind = listed.listed, frozenset(), False
        found = _found(self.result, listed, module)
        self._last_read = found.text
        return found.missing

    def _uncleared(self) -> HeldImages | None:
        """What the module holds before an upload again where no clear went, from the last read that answered.

        None where a clear went (the module is known empty, or the Prodigy removed the label's own
        names, so its downloads go again as they were), or where no read of the label answered.
        A name a read listed is held: downloading it again would print nothing (one Allegro 2,
        2026-11-01), so the label is rendered again leaving it out.
        """
        if self.result.retry_clear_may_go or self._known is None:
            return None
        return self._as_listed(self._known)

    def _before_halving(self) -> HeldImages | None:
        """What the module holds before the halved upload, once the round's clear went; None where nothing is known.

        ``<STX>q`` leaves it empty. The plain Prodigy's removes take out only the label's own
        names, the full-size ones: a halved name the last read that answered listed stays held,
        and downloading it again, with no clear before it, would print nothing.
        """
        if self.result.retry_clear_empties:
            return EMPTY_MODULE
        if self.result.retry_clear_may_go and self._known is not None:
            return self._as_listed(self._known - frozenset(self.result.downloads))
        return self._uncleared()

    def _as_listed(self, listed: frozenset[str]) -> HeldImages:
        """The module as a read listed it, each name this label's uploads sent marked as its own."""
        return HeldImages(listed=listed, uploaded=self._own_names)

    def _unknown(self) -> HeldImages:
        """What a render knows of the module where no read of the label answered: nothing, and why."""
        return HeldImages(unread_because=self._unread_because or NOT_ASKED, uploaded=self._own_names)

    def _downloaded_again(self, result: RenderResult) -> tuple[str, ...]:
        """The names ``result`` would download that may be held already, with no clear before them.

        A name sent since the last read that answered may have arrived, and where the read before
        the downloads got no answer any name may have been left by a label before this one;
        downloading it again, with no clear to empty the module first, would print nothing (one
        Allegro 2, 2026-11-01).
        """
        if self.result.retry_clear_may_go:
            return ()
        if self._blind:
            return tuple(dict.fromkeys(result.downloads))
        return tuple(name for name in result.downloads if name in self._unsure)

    @staticmethod
    def _unread(names: tuple[str, ...]) -> str:
        """Why ``names`` may be held: no clear may empty the module, and no read since said what it holds."""
        held = "it may be held" if len(names) == 1 else "they may be held"
        return (
            f"{', '.join(names)}: {held} already, since no clear may empty the module and the last read "
            "got no answer, and a download of a held name prints nothing"
        )

    def clear_and_upload(self) -> tuple[str, ...] | _Refused | _Skipped:
        """Round 2: clear where a clear may go, render again for what the module then holds, upload again.

        Where the upload again would download a name the module may hold (no clear may go, and the
        last read got no answer), the round first reads the names again, uploading nothing before
        that read: with an answer the label is rendered again from that listing and the round goes
        on (a label left with nothing to download passes, at full size); with none the round is
        left out.

        Returns the names the read after it does not list; :class:`_Refused` instead where the
        label, rendered again, has an error, and :class:`_Skipped` where the names read again got
        no answer either: nothing of the round is sent then.
        """
        again = self._rendered_for_round_2()
        if isinstance(again, _Refused):
            return again
        read_again = ""
        if twice := self._downloaded_again(self.result if again is None else again):
            listed = self.link.read(self._module(), wait=self.check.wait)
            if listed.listed is None:
                skipped = _Skipped(
                    f"round 2 skipped, it would download again {self._unread(twice)}; the names read again got no "
                    f"answer either ({listed.unread_because})"
                )
                self._retried(skipped.reason)
                return skipped
            self._known, self._unsure, self._blind = listed.listed, frozenset(), False
            found = _found(self.result, listed, self._module())
            module = self._module().value
            read_again = f", read module {module} again first as the read before it gave no listing ({found.text})"
            again = self._rendered_for_round_2()
            if isinstance(again, _Refused):
                return again
        cleared = self._clear()
        if again is not None:
            self.result = again
        missing = self.upload()
        self._retried(f"round 2: {cleared}{read_again}, then {self._uploaded('the images again', read=not read_again)}")
        return missing

    def _rendered_for_round_2(self) -> RenderResult | _Refused | None:
        """The label rendered again for what the module holds once round 2's clear went.

        None where it goes again as it was: a clear runs before its downloads anyway, so what the
        module holds changes nothing in it; the plain Prodigy's removes take out its own names; or
        no read of it answered where no clear may go. :class:`_Refused` where it has an error.
        """
        if not self.result.uses_held_names:
            return None
        base = EMPTY_MODULE if self.result.retry_clear_empties else self._uncleared()
        if base is None:
            return None
        again = self.render(base, halve=False)
        return again if again.ok else _Refused.of(again, when="for round 2")

    def halve_and_upload(self) -> tuple[str, ...] | _Skipped:
        """Round 3: clear, upload the images halved, and return the names the read after it does not list.

        :class:`_Skipped` instead, with nothing of the round sent, where the images cannot be
        halved, halved they are refused, or the halved upload would download a name the module
        may hold.
        """
        # An accepted limit: where round 2's read after its upload is the one that gets no answer,
        # this round halves with no read of its own first, though the full-size names may have
        # arrived and a read might print the label at full size. Round 2 reads again in that case
        # and this round does not, because a further read adds one stored_names_wait to a worst
        # case already past the "about 1.5 minutes" of the upload check decision, and it takes a
        # lost upload and then a silence on one label, which still prints.
        base = self._before_halving()
        halved = self.render(self._unknown() if base is None else base, halve=True)
        if not halved.halved_elements or self.result.halved_elements:
            return _Skipped("no image of the label can be halved (a QR Code never is, nor an image halved already)")
        if not halved.ok:
            return _Skipped("halving is refused: " + "; ".join(f.message for f in halved.errors))
        if twice := self._downloaded_again(halved):
            stays = "a QR Code is never halved, so its name stays" if not self._blind else "no read of it answered"
            return _Skipped(f"halving is skipped, it would download again ({stays}) {self._unread(twice)}")
        cleared = self._clear()
        self.result = halved
        missing = self.upload()
        self._retried(f"round 3: {cleared}, then {self._uploaded('the images halved 2x2')}")
        return missing

    def _clear(self) -> str:
        """Send the label's retry clear, apply it to what the last read that answered listed, and say what it did.

        The one place a clear goes out during the rounds, so what a later round takes the module to
        hold never keeps a name the clear took out: ``<STX>q`` empties the listing, the plain
        Prodigy's removes take out the label's own names.
        """
        self.link.send(self.result.retry_clear)
        if self._known is not None and self.result.retry_clear:
            emptied = self.result.retry_clear_empties
            self._known = frozenset() if emptied else self._known - frozenset(self.result.downloads)
        return _clear_text(self.result, self._module())

    def _uploaded(self, images: str, *, read: bool = True) -> str:
        """Say what a round's upload of ``images`` did and what the read after it found, for its finding.

        ``read`` False: the round read the names before its upload, which that read then says.
        """
        if not self.result.downloads:
            unread = ", so no read was made" if read else ""
            return f"sent nothing of {images}: the module holds each one already{unread}"
        return f"uploaded {images}; {self._last_read}"

    def _retried(self, message: str) -> None:
        self.findings.append(Finding(code=FindingCode.IMAGE_UPLOAD_RETRIED, severity=Severity.INFO, message=message))

    def refuse(self, missing: tuple[str, ...], *, rounds: _Rounds) -> None:
        """Refuse the label: name the missing images, the rounds run and why each other did not, then clear."""
        if rounds.ran:
            outcome = f"not stored after {rounds.tried} ({self._last_read})"
            reason = "".join(f"; {why}" for why in rounds.skipped_before_the_last)
            later = rounds.skipped_after_the_last
            reason += f"; no further round: {'; '.join(later)}" if later else ""
        else:
            outcome, reason = "not sent", f": no round ran: {'; '.join(why for _, why in rounds.skipped)}"
        self.findings.append(
            Finding(
                code=FindingCode.IMAGE_UPLOAD_REFUSED,
                severity=Severity.ERROR,
                message=(
                    f"images {', '.join(missing)} {outcome}{reason}; the label's format was not sent, since it would "
                    "print without them"
                ),
            )
        )
        self._clear()

    def refuse_rendered(self, refused: _Refused) -> None:
        """Refuse the label that, rendered again for a round, has an error: nothing of that round was sent.

        Its uploads so far are cleared again where a clear may go, as for :meth:`refuse`.
        """
        if self._last_read:
            outcome = f"its format was not sent (the read after its upload: {self._last_read})"
        else:
            outcome = "nothing of it was sent"
        self.findings.append(
            Finding(
                code=FindingCode.IMAGE_UPLOAD_RENDER_REFUSED,
                severity=Severity.ERROR,
                message=f"{refused.reason}; {outcome}",
            )
        )
        self._clear()

    def as_sent(self) -> RenderResult:
        """The label's result as it went out, with the rounds' findings."""
        return self.result.model_copy(update=_FindingsUpdate(findings=self.result.findings + tuple(self.findings)))

    def unkept(self) -> Mapping[FindingCode, int]:
        """The counts of findings :meth:`as_sent` leaves out: the first render's, unless a round rendered it again."""
        return unkept_of(self.render) if self.result is self._first_result else NO_UNKEPT


# Dataclasses, not NamedTuples: a round's outcome is told apart from the tuple of missing names
# by type, and a NamedTuple of one str would itself be a tuple[str, ...].
@dataclass(frozen=True)
class _Skipped:
    """A round left out, with why; the next round may still run."""

    reason: str


@dataclass(frozen=True)
class _Refused:
    """A round whose label, rendered again for it, has an error: the label is refused, nothing of the round sent."""

    reason: str

    @classmethod
    def of(cls, rendered: RenderResult, *, when: str) -> _Refused:
        """The refusal of a label that, rendered again ``when``, gave ``rendered``, quoting its errors."""
        errors = "; ".join(f.message for f in rendered.errors)
        return cls(f"the label, rendered again {when}, has errors ({errors})")


@dataclass(frozen=True)
class _Rounds:
    """The rounds of one label's check so far: the numbers of those that ran, and each one skipped with why.

    Example:
        >>> rounds = _Rounds(ran=(1, 3), skipped=((2, "round 2 skipped"),))
        >>> rounds.tried, rounds.skipped_before_the_last, rounds.skipped_after_the_last
        ('2 rounds, 1 and 3', ('round 2 skipped',), ())
        >>> _Rounds(ran=(1, 2, 3)).tried, _Rounds(ran=(2,)).tried
        ('3 rounds', 'round 2')
    """

    ran: tuple[int, ...] = ()
    skipped: tuple[tuple[int, str], ...] = ()

    def run(self, number: int) -> _Rounds:
        """These rounds and round ``number``, which ran."""
        return _Rounds(ran=(*self.ran, number), skipped=self.skipped)

    def skip(self, number: int, reason: str) -> _Rounds:
        """These rounds and round ``number``, skipped for ``reason``."""
        return _Rounds(ran=self.ran, skipped=(*self.skipped, (number, reason)))

    @property
    def tried(self) -> str:
        """How many rounds ran, naming them where they are not rounds 1 to N: a round skipped between them ran none."""
        count = "1 round" if len(self.ran) == 1 else f"{len(self.ran)} rounds"
        if self.ran == tuple(range(1, len(self.ran) + 1)):
            return count
        if len(self.ran) == 1:
            return f"round {self.ran[0]}"
        return f"{count}, {' and '.join(str(number) for number in self.ran)}"

    @property
    def skipped_before_the_last(self) -> tuple[str, ...]:
        """Why each round skipped before the last one that ran did not run; a later round still did."""
        last = max(self.ran, default=0)
        return tuple(why for number, why in self.skipped if number < last)

    @property
    def skipped_after_the_last(self) -> tuple[str, ...]:
        """Why each round after the last one that ran did not run: why no further round came."""
        last = max(self.ran, default=0)
        return tuple(why for number, why in self.skipped if number > last)


def _check_label(record: _Record, before: _Skipped | _Refused | None) -> bool:
    """Run a label's rounds; return True when its images are listed and the rest may follow.

    ``before`` is what the read before the label's downloads (:meth:`_Record.read_before`) made of it.
    """
    if isinstance(before, _Refused):
        record.refuse_rendered(before)
        return False
    if isinstance(before, _Skipped):
        missing, rounds = tuple(dict.fromkeys(record.result.downloads)), _Rounds().skip(1, before.reason)
    else:
        # Rendered again from the names read before its downloads, a label may download nothing:
        # its upload then sends nothing and finds nothing missing.
        missing, rounds = record.upload(), _Rounds().run(1)
    if missing and record.check.rounds >= CLEAR_ROUND:
        outcome = record.clear_and_upload()
        if isinstance(outcome, _Skipped):
            rounds = rounds.skip(CLEAR_ROUND, outcome.reason)
        elif isinstance(outcome, _Refused):
            record.refuse_rendered(outcome)
            return False
        else:
            missing, rounds = outcome, rounds.run(CLEAR_ROUND)
    if missing and record.check.rounds >= HALVING_ROUND:
        halving = record.halve_and_upload()
        if isinstance(halving, _Skipped):
            rounds = rounds.skip(HALVING_ROUND, halving.reason)
        else:
            missing, rounds = halving, rounds.run(HALVING_ROUND)
    if missing:
        record.refuse(missing, rounds=rounds)
        return False
    return True


def _unlisted_module(result: RenderResult) -> RenderResult | None:
    """The label refused with ``image_module_unlisted`` when it downloads into a module no directory lists.

    None for a label that downloads nothing, or whose module a directory lists.
    """
    if not unlisted_download(result):
        return None
    built_in = get_profile(result.model).image_module
    letter = "" if built_in is None else f" (module {built_in.value} on the {result.model})"
    refusal = Finding(
        code=FindingCode.IMAGE_MODULE_UNLISTED,
        severity=Severity.ERROR,
        message=(
            f"image_module {ImageModule.C.value} is the printer's default module, which no directory lists by that "
            "letter, so the upload check can never see the images; nothing was sent. Set image_module to a "
            f"module letter the printer lists{letter}, or print over a one-way link"
        ),
    )
    return result.model_copy(update=_FindingsUpdate(findings=(*result.findings, refusal)))


class _LabelSink(Protocol):
    """Where a checked send puts each label reached, as it went out.

    A label refused (or, rendered again, found in error) is the last one reached, and none of its
    format went out.
    """

    def add(self, result: RenderResult, *, cut: bool, unkept: Mapping[FindingCode, int] = ...) -> object:
        """Take the next label, ``cut`` when a batch cut follows it, ``unkept`` counting findings it leaves out."""
        ...


class _SentLabels:
    """Holds every label reached, for a job whose result holds every label anyway."""

    def __init__(self) -> None:
        self.sent: list[RenderResult] = []

    def add(self, result: RenderResult, *, cut: bool, unkept: Mapping[FindingCode, int] = NO_UNKEPT) -> None:
        """Keep the next label; a :class:`BatchResult` holds its cuts apart, and its results every finding."""
        del cut, unkept
        self.sent.append(result)


def _labels_in(result: RenderResult | BatchResult | BatchReport) -> int:
    if isinstance(result, BatchReport):
        return result.records
    return len(result.results) if isinstance(result, BatchResult) else 1


def _refuse_mismatch(result: RenderResult | BatchResult | BatchReport, job: CheckedJob) -> None:
    """Refuse a result and renders describing different batches, before a label of either goes out.

    Renders that are not sized (a generator) can only be counted as they run: see :func:`_send_report`.

    Raises:
        ConfigurationError: Their label counts differ.
    """
    labels = _labels_in(result)
    if isinstance(job.renders, Sized) and labels != len(job.renders):
        msg = f"the job holds {labels} labels but {len(job.renders)} renders; nothing was sent"
        raise ConfigurationError(msg)


def _after_reset(link: Link, render: RecordRender, job: CheckedJob) -> tuple[Finding, ...]:
    """Act on a reset report before the first label's downloads; return what the job should say.

    The names read right before those downloads may already have taken the report, which the
    printer gives once: nothing more is asked then. Otherwise the status is asked for it. A read
    with no answer, like a printer that reports nothing, returns nothing: the job goes on exactly
    as it would have. Nothing is sent but the status request unless a reset was reported.
    """
    reset = job.reset
    if reset is None:
        return ()
    if link.reset_seen or read_reset_report(link.channel(), wait=reset.wait):
        return _on_reset(link, render, reset)
    return ()


def _reset_in_rounds(link: Link, render: RecordRender, job: CheckedJob, *, passed: bool) -> tuple[Finding, ...]:
    """Act on a report that came with a directory read of the first label, the status read having told nothing.

    The printer gives a report once, so this one is the only one: the label's format has not gone
    yet, so the calibration label can still take the shift. A label those rounds refused sends
    no format, and nothing else tells the next job.
    """
    if job.reset is None or not link.reset_seen:
        return ()
    if not passed:
        why = "the upload check refused the label before a calibration label could go; the report is used up"
        return (_not_calibrated(why, severity=Severity.WARNING),)
    return _on_reset(link, render, job.reset)


def _on_reset(link: Link, render: RecordRender, reset: ResetCheck) -> tuple[Finding, ...]:
    """Send the calibration label a reset report asks for, or say why none went; return what the job should say."""
    if not reset.calibrate:
        return (_not_calibrated(reset.off_because, severity=Severity.INFO),)
    calibration = render.calibration(reset.text)
    if not calibration.ok:
        reasons = "; ".join(f.message for f in calibration.errors)
        return (_not_calibrated(f"the calibration label has errors ({reasons})", severity=Severity.WARNING),)
    link.send(calibration.data)
    message = (
        "the printer reported a reset, so one extra label came out first: the calibration label, "
        "which takes the shift the first label after a reset can print with"
    )
    return (Finding(code=FindingCode.PRINTER_RESET_CALIBRATED, severity=Severity.INFO, message=message),)


def _not_calibrated(why: str, *, severity: Severity) -> Finding:
    """The finding for a reported reset that no calibration label followed, saying why."""
    message = f"the printer reported a reset and no calibration label went first: {why}; the first label may be shifted"
    return Finding(code=FindingCode.PRINTER_RESET_NOT_CALIBRATED, severity=severity, message=message)


def _send_labels(link: Link, job: CheckedJob, *, cuts: Iterable[bool], sink: _LabelSink) -> bool:
    """Send each label of ``job`` in turn, with a batch cut after each label ``cuts`` marks.

    Holds only the current label: each one goes to ``sink`` as it went out. Stops at the shorter of
    ``cuts`` and ``job.renders``, taking no render past the last cut.

    Returns:
        True when every label was sent; False when one was refused, which nothing after it follows.
    """
    first = True
    for cut, render in zip(cuts, job.renders, strict=False):
        record = _Record(render=render, link=link, check=job.check, first=first)
        if (refused := _refused_as_rendered(record.result)) is not None:
            sink.add(refused, cut=cut)
            return False
        if not _checked_with_reset(link, job, record, first=first):
            sink.add(record.as_sent(), cut=cut, unkept=record.unkept())
            return False
        # Built after the check: a round may have rendered the label again.
        whole = with_cut(record.result.data, record.result.closing, cut=cut)
        link.send(whole[record.result.upload_end :] if record.result.downloads else whole)
        sink.add(record.as_sent(), cut=cut, unkept=record.unkept())
        first = False
    return True


def _refused_as_rendered(result: RenderResult) -> RenderResult | None:
    """``result`` refused as rendered, before anything of it goes out: its errors, or a module no directory lists."""
    if not result.ok:
        return result
    return _unlisted_module(result)


def _checked_with_reset(link: Link, job: CheckedJob, record: _Record, *, first: bool) -> bool:
    """Run the upload check of ``record``, and for the first label the reset check; True when it passed.

    The first label's names are read before the reset check: a label that the listing makes an
    error is refused before anything, a calibration label included, goes out.
    """
    checked = bool(record.result.downloads)
    before = record.read_before() if checked else None
    told: tuple[Finding, ...] = ()
    if first and not isinstance(before, _Refused):
        told = _after_reset(link, record.render, job)
        record.findings.extend(told)
    passed = not checked or _check_label(record, before)
    if first and not told:
        record.findings.extend(_reset_in_rounds(link, record.render, job, passed=passed))
    return passed


def send_checked_label(link: Link, job: CheckedJob) -> CheckedSend[RenderResult]:
    """Send the one label of ``job`` on ``link`` as :func:`send_checked` sends each label of a batch."""
    labels = _SentLabels()
    all_sent = _send_labels(link, job, cuts=(False,), sink=labels)
    return CheckedSend(result=labels.sent[0], all_sent=all_sent)


def _send_report(link: Link, report: BatchReport, job: CheckedJob) -> CheckedSend[BatchReport]:
    """Send a checked batch label by label, holding only the current one, and report it as it went out.

    A label refused leaves the labels after it as first rendered: they are read back from the
    report's spool (or, with none, rendered once more) for the report, as a :class:`BatchResult`
    would have held them all along.

    Raises:
        ConfigurationError: ``job.renders`` held another number of labels than ``report``, which only
            shows once they have gone out.
    """
    tally = BatchTally(report.limits, plan=report.plan)
    renders = iter(job.renders)
    all_sent = _send_labels(link, job._replace(renders=renders), cuts=report.cuts(), sink=tally)
    if all_sent and (tally.records != report.records or next(renders, None) is not None):
        msg = f"the job holds {report.records} labels, and the renders given hold another number; {tally.records} went"
        raise ConfigurationError(msg)
    if not all_sent:
        reached = tally.records
        for entry in islice(report.entries(), reached, None):
            tally.add(entry.result, cut=entry.cut_after, unkept=entry.unkept)
    return CheckedSend(result=tally.report(report.job), all_sent=all_sent)


@overload
def send_checked(link: Link, result: RenderResult, job: CheckedJob) -> CheckedSend[RenderResult]: ...


@overload
def send_checked(link: Link, result: BatchResult, job: CheckedJob) -> CheckedSend[BatchResult]: ...


@overload
def send_checked(link: Link, result: BatchReport, job: CheckedJob) -> CheckedSend[BatchReport]: ...


def send_checked(
    link: Link, result: RenderResult | BatchResult | BatchReport, job: CheckedJob
) -> CheckedSend[RenderResult | BatchResult | BatchReport]:
    """Send ``result`` label by label on ``link``, each label that downloads an image after its upload check.

    ``result`` is the job as first rendered, with its batch cuts and findings and the result of
    each label not reached. Each label is rendered again from ``job.renders`` with what the printer
    holds before it, which gives the render ``result`` holds until a round changes what is held. A
    :class:`~datamax_dpl.application.use_cases.BatchReport` holds no label: only the current one is
    in memory then.

    Returns:
        The job as it went out, each label reached with the rounds' findings, and whether every
        label was sent (see :class:`CheckedSend`).

    Raises:
        ConfigurationError: ``result`` and ``job.renders`` hold different numbers of labels.
    """
    _refuse_mismatch(result, job)
    if isinstance(result, BatchReport):
        return _send_report(link, result, job)
    if not isinstance(result, BatchResult):
        return send_checked_label(link, job)
    # Refused before the first record goes out: a record that downloads nothing would print, and
    # the check of the first one that downloads could never pass.
    labels = _SentLabels()
    for index, record in enumerate(result.results):
        if (unlisted := _unlisted_module(record)) is not None:
            labels.sent = [*result.results[:index], unlisted]
            all_sent = False
            break
    else:
        all_sent = _send_labels(link, job, cuts=result.cut_after or (False,) * len(result.results), sink=labels)
    whole = BatchResult(
        results=(*labels.sent, *result.results[len(labels.sent) :]),
        batch_findings=result.batch_findings,
        cut_after=result.cut_after,
    )
    return CheckedSend(result=whole, all_sent=all_sent)


def _refuse_bad_check(check: UploadCheck) -> None:
    """Refuse an upload check that could only fail once part of the job is out.

    Raises:
        ValueError: A wait is not a usable number of seconds, or the rounds are out of range.
    """
    check_timeout(check.wait.seconds, name=TimeoutName.WAIT)
    check_timeout(check.wait.retry_interval, name=TimeoutName.RETRY_INTERVAL)
    if not MIN_UPLOAD_CHECK_ROUNDS <= check.rounds <= MAX_UPLOAD_CHECK_ROUNDS:
        msg = f"rounds must be {MIN_UPLOAD_CHECK_ROUNDS} to {MAX_UPLOAD_CHECK_ROUNDS}, got {check.rounds}"
        raise ValueError(msg)


@overload
def print_checked(
    result: RenderResult, job: CheckedJob, *, endpoint: PrinterEndpoint
) -> CheckedPrint[RenderResult]: ...


@overload
def print_checked(result: BatchResult, job: CheckedJob, *, endpoint: PrinterEndpoint) -> CheckedPrint[BatchResult]: ...


@overload
def print_checked(result: BatchReport, job: CheckedJob, *, endpoint: PrinterEndpoint) -> CheckedPrint[BatchReport]: ...


def print_checked(
    result: RenderResult | BatchResult | BatchReport,
    job: CheckedJob,
    *,
    endpoint: PrinterEndpoint,
) -> CheckedPrint[RenderResult | BatchResult | BatchReport]:
    """Send a rendered job, checking each label's image upload before its format goes out.

    On a two-way link the printer is asked for its status once first (``job.reset``, None asks
    nothing), after the first label's names read where one goes, which can carry the report
    itself. When it reports a reset, one extra label comes out ahead of the job: the calibration
    label, which takes the shift a first label can print with. A channel that cannot read answers
    gets the whole job at once, as :func:`print_result` sends it.

    Each label no clear precedes reads the printer's directory right before its downloads and is
    rendered again from what it lists; nothing about the printer's images is kept from one label
    to the next.

    Args:
        result: The job as first rendered. For a large batch, the report of
            :func:`~datamax_dpl.application.use_cases.check_batch` with its ``renders()`` in ``job``:
            memory does not grow with the records then.
        job: How to render each label again, the upload check and the reset check.
        endpoint: The printer, its timeout for each connect and write, and how to open it.

    Returns:
        What was sent where, and the job as it went out with the checks' findings.

    Raises:
        LabelRejectedError: The job has an error finding; nothing was sent. Its ``findings`` name them.
        UploadRefusedError: A label's images were still not listed after every round, or the
            label, rendered again for what the printer then held, has an error finding
            (``image_upload_render_refused``); its format and everything after it were not sent.
            Or the job downloads into module C, which no directory lists
            (``image_module_unlisted``); nothing was sent.
        ConfigurationError: ``result`` and ``job.renders`` hold different numbers of labels; nothing
            was sent, unless the renders are not sized (a generator) and only their running out told.
            The same when a report's records, read again, come to another number than were checked.
        MissingFieldError: A report's record, read again, lacks a field a placeholder names.
        TransportError: The printer could not be reached or written.
        ValueError: A value of ``job.check`` is not usable; nothing was sent.
        pydantic.ValidationError: A report's record, read again, breaks a field limit.

    Examples:
        >>> from datamax_dpl import AnswerWait, CheckedJob, Label, PrinterEndpoint, RenderOptions, Text, UploadCheck
        >>> from datamax_dpl import build_job, record_renders
        >>> from datamax_dpl.adapters.memory import RecordingPrinter, open_recording_printer
        >>> label, options = Label(elements=[Text(x=1, y=1, text="HELLO")]), RenderOptions(model="dmx-600")
        >>> check = UploadCheck(wait=AnswerWait(seconds=1))
        >>> printer = RecordingPrinter()
        >>> sent = print_checked(
        ...     build_job(label, options),
        ...     CheckedJob(renders=record_renders(label, None, options), check=check),
        ...     endpoint=PrinterEndpoint(
        ...         uri="tcp://192.0.2.1:9100", timeout=5.0, open_printer=open_recording_printer(printer)
        ...     ),
        ... )
        >>> sent.result.ok, b"HELLO" in b"".join(printer.sent)
        (True, True)
    """
    if not result.ok:
        msg = "label has errors; nothing was sent"
        raise LabelRejectedError(msg, error_findings(result))
    _refuse_mismatch(result, job)
    _refuse_bad_check(job.check)
    printer = endpoint.open()
    link = Link(printer=printer, endpoint=endpoint, reconnect=job.check.wait)
    try:
        if not printer.bidirectional:
            for piece in (result.data,) if isinstance(result, RenderResult) else result.record_jobs():
                link.send(piece)
            return CheckedPrint(uri=endpoint.uri, bytes_sent=link.bytes_sent, result=result)
        sent = send_checked(link, result, job)
    finally:
        printer.close()
        link.close_opened()
    if not sent.all_sent:
        raise UploadRefusedError(sent.result, bytes_sent=link.bytes_sent)
    return CheckedPrint(uri=endpoint.uri, bytes_sent=link.bytes_sent, result=sent.result)


__all__ = [
    "MAX_UPLOAD_CHECK_ROUNDS",
    "MIN_UPLOAD_CHECK_ROUNDS",
    "UPLOAD_CHECK_ROUNDS",
    "CheckedJob",
    "CheckedPrint",
    "CheckedSend",
    "Link",
    "ResetCheck",
    "UploadCheck",
    "UploadRefusedError",
    "print_checked",
    "send_checked",
    "send_checked_label",
]
