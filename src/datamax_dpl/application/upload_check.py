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

Where no clear may go (a plug-in module, a model with no clear) a round renders the label again
with what the last read listed, so a name the module holds is left out rather than downloaded
again, which would print nothing. Where the read got no answer, each name sent before it may be
held: round 2 then reads the names again before it uploads anything; with an answer the label is
rendered again from that listing and the round goes on (a label left with nothing to download
passes, at full size), with none round 2 is left out. Round 3 runs after a read with no answer
only where every download gets a new name by halving, which a QR Code never does. On the plain
Prodigy, whose removes take out only the label's own names, round 3 renders from what the last
read that answered listed less those names, so a halved name the module holds is placed, not
downloaded again. A label that, rendered again for a round, has an error finding (the module it
now holds leaves too little memory, a held name in error) is refused there
(``image_upload_render_refused``), before anything of that round is sent. One that, rendered
again, downloads nothing (each image it places is one the module holds) sends no upload and makes
no read after it in that round: nothing of it can be missing.

Before the first label, once it is known not to be refused, the printer is asked for its status
once (:class:`ResetCheck`): its first answer after a power cycle reports the reset, and one Allegro 2
printed the first label after one about 12 mm shifted. On such a report one calibration label goes
first, so that the shift lands on it (``printer_reset_calibrated``). A names read before the job
may have taken the report already (:attr:`~datamax_dpl.domain.held_images.HeldImages.reset_reported`);
nothing is asked then. Nothing here reads a reset as an empty module, and a label the upload check
refuses later still had its calibration label: only uploading can tell.

Still missing: the label is refused (``image_upload_refused``), its format is never sent, nothing
after it is sent, and the module is cleared again where a clear may go. Each round after the
first adds an ``image_upload_retried`` finding saying what it did and what the printer then listed.

A job whose images go to module C, the printer's default module, which no directory lists under
that letter, is refused before anything is sent (``image_module_unlisted``): the check could never
pass, and the format may not go out before it has.

In a batch each label is checked on its own and passes on to the next label what its last read
that answered listed, with every clear sent after that read applied (a retry clear before a later
round, the clear after the label): a ``<STX>q`` leaves the module empty, the plain Prodigy's
removes take out the label's own names. Where no read answered it passes on what its render
says. A name it sent after that read, where no clear may go, may be held: it is passed on with
the command that sent it, and no later label downloads it again either. A later label whose
first upload would reads the names once before its round 1: with an answer it is rendered again
from that listing, where a listed name holds the picture its command sent (the same picture is
placed at full size, another one under the same content name is downloaded under the next salt,
and a label left with nothing to download goes out with no further check), and its rounds go on
as usual; with no answer its round 1 is left out and round 2 reads the names again. A label that,
rendered with what the printer holds after the labels before it, has an error finding stops the
batch there: none of it and nothing after it is sent.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Generic, NamedTuple, TypedDict, TypeVar

from ..domain._invariants import require_update_fields
from ..domain.batch import with_cut
from ..domain.enums import FindingCode, ImageModule, Severity
from ..domain.errors import ConfigurationError, LabelRejectedError
from ..domain.findings import Finding
from ..domain.frozen_mapping import FrozenMapping
from ..domain.held_images import EMPTY_MODULE, HeldImages
from ..domain.profiles import get_profile
from ..domain.render import RenderResult, held_after_read
from ..domain.reset_label import (
    CALIBRATE_AFTER_RESET,
    CALIBRATION_LABEL_TEXT,
    RESET_CHECK_WAIT_SECONDS,
    check_label_text,
)
from ..domain.timing import TimeoutName, check_timeout
from .use_cases import AnswerWait, BatchResult, open_within, read_reset_report, read_stored_names

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..domain.render import RenderedRecord
    from .ports import OpenPrinter, PrinterTransport
    from .use_cases import RecordRender

_Sent_co = TypeVar("_Sent_co", bound="RenderResult | BatchResult", covariant=True)


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

    def __post_init__(self) -> None:
        """Refuse a value that could only fail once a job is on its way."""
        check_timeout(self.wait, name=TimeoutName.RESET_CHECK_WAIT)
        check_label_text(self.text)


class CheckedPrint(NamedTuple):
    """What a checked send sent where, and the job as it went out.

    Attributes:
        uri: The printer.
        bytes_sent: Job bytes written, every round's uploads and clears included, and a calibration
            label's.
        result: The job as it went out: each label with its rounds' findings, a halved label as halved.
    """

    uri: str
    bytes_sent: int
    result: RenderResult | BatchResult


class CheckedJob(NamedTuple):
    """A job to send with its upload check, and how to render each of its labels again.

    The renders and the job they rebuild are rendered with the same options; with
    ``model_image_budget`` False, as ``print`` and ``verify`` render for a two-way link, the images
    go at full size and the check, not the model's budget, decides whether they are halved.

    Attributes:
        renders: One render per label, in the order they are sent (``record_renders``).
        check: How the upload is checked.
        held: What the printer holds in the image module before the first label: the names read
            before rendering, or nothing known.
        reset: How a reset report is asked for before the first label; None asks nothing.
    """

    renders: Sequence[RecordRender]
    check: UploadCheck
    held: HeldImages = HeldImages()
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

    def __init__(self, result: RenderResult | BatchResult, *, bytes_sent: int) -> None:
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


def _all_findings(result: RenderResult | BatchResult) -> tuple[Finding, ...]:
    results = result.results if isinstance(result, BatchResult) else (result,)
    return tuple(f for r in results for f in r.findings)


@dataclass
class Link:
    """The channel a checked job goes out on, opened again when a read lost it.

    Attributes:
        printer: The open channel, None after a read that failed closed it.
        uri: The printer.
        timeout: Seconds for each connect and write.
        open_printer: Opens a new channel.
        reconnect: How long a send keeps trying to open a new channel where a failed read left
            none (``stored_names_wait``, as the read itself).
        bytes_sent: Job bytes written so far, the directory requests not counted.
    """

    printer: PrinterTransport | None
    uri: str
    timeout: float
    open_printer: OpenPrinter
    reconnect: AnswerWait
    bytes_sent: int = 0
    _opened: list[PrinterTransport] = field(default_factory=list["PrinterTransport"])

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
        """The open channel, a new one when a failed read left none; :meth:`close_opened` closes a new one."""
        if self.printer is None:
            self.printer = open_within(
                uri=self.uri, timeout=self.timeout, open_printer=self.open_printer, wait=self.reconnect
            )
            self._opened.append(self.printer)
        return self.printer

    def read(self, module: ImageModule, *, wait: AnswerWait) -> HeldImages:
        """Read what ``module`` holds on the open channel; keep the channel that answered."""
        before = self.printer
        read = read_stored_names(
            self.printer, module=module, uri=self.uri, timeout=self.timeout, open_printer=self.open_printer, wait=wait
        )
        self.printer = read.channel
        if self.printer is not None and self.printer is not before:
            self._opened.append(self.printer)
        return read.held

    def close_opened(self) -> None:
        """Close every channel this link opened itself; the one it was given stays the caller's."""
        for printer in self._opened:
            printer.close()


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


class _PassedOn(NamedTuple):
    """What a label of a batch leaves the next one: what the printer holds, and what it may hold.

    Attributes:
        held: What the printer holds in the image module after the label.
        maybe_held: Names sent, where no clear may go, since the last read that answered, each
            with the download command that sent it: each may be held or may be lost, so no later
            label may download one again; one that would reads the names first
            (:meth:`_Record.read_first`). A read that then lists one shows the picture its
            command sent, which a later label's other picture under the same content name is not.
    """

    held: HeldImages
    maybe_held: FrozenMapping[str, bytes] = FrozenMapping[str, bytes]({})


@dataclass
class _Record:
    """One label's rounds: its render, what the printer held before it and the rounds' findings."""

    render: RecordRender
    before: _PassedOn
    link: Link
    check: UploadCheck
    result: RenderResult = field(init=False)
    findings: list[Finding] = field(default_factory=list[Finding])
    # What the render of ``result`` says the printer holds after it; never passed on as it is
    # once a read answered, since the read knows more (see :meth:`passed_on`).
    _rendered_after: HeldImages = field(init=False)
    # The download command of every name the label's rounds sent, and of each name the label
    # before it may have left, for a name a read then lists.
    _sent: dict[str, bytes] = field(default_factory=dict[str, bytes], init=False)
    _last_read: str = field(default="", init=False)
    # What the last read that answered listed, less what every clear since took out (see _clear).
    # Unknown where no read has answered yet: then nothing is known to leave out.
    _known: frozenset[str] | None = field(default=None, init=False)
    # Names downloaded since the last read that answered: each may be held, or may be lost.
    _unsure: frozenset[str] = field(default=frozenset(), init=False)

    @property
    def held(self) -> HeldImages:
        """What the printer held in the image module before the label."""
        return self.before.held

    def __post_init__(self) -> None:
        """Render the label as it is first sent."""
        self._unsure = frozenset(self.before.maybe_held)
        self._sent.update(self.before.maybe_held)
        self._rendered(self.render(self.held, halve=False))

    def _rendered(self, rendered: RenderedRecord) -> None:
        """Make ``rendered`` the label as it goes out, with what its render says the printer then holds."""
        self.result, self._rendered_after = rendered.result, rendered.held_after

    def _as_read(self, known: frozenset[str]) -> HeldImages:
        """What the printer holds after the label once a read listed ``known``, every round's sent names known.

        The one way a read's listing becomes what the printer holds: for an upload again where no
        clear went (no clear after the label either, then) and for the next label of a batch.
        A name a round sent keeps the picture it sent. The render's own commands are only what the
        label meant to download, which a round left out never sent: a name only those give, once
        listed, is one the printer held without this batch, not one the batch sent, so a label
        rendered from the result reports it (``image_name_held``) instead of leaving it out silently.
        """
        rendered = self._rendered_after
        carried = {name: command for name, command in rendered.sent.items() if name in self.held.sent}
        return held_after_read(self.result, replace(rendered, sent={**carried, **self._sent}), known)

    def _held_before(self, known: frozenset[str]) -> HeldImages:
        """What the module held before the label once a read listed ``known``, each name sent before with its picture.

        A name the label before it may have left is one it sent: listed now, it holds that
        picture, which this label's other picture under the same content name is not.
        """
        before = replace(self.held, sent={**self.held.sent, **self.before.maybe_held})
        return before.as_listed(known)

    def passed_on(self) -> _PassedOn:
        """What the printer holds after the label, and what it may hold, for the next label of a batch.

        As the last read that answered listed it, where one did (a name sent before a read that
        got no answer, then listed by a later read, is held: downloading it again would print
        nothing), with every clear sent since applied (a retry clear, see :meth:`_clear`, and the
        clear after the label); as the render says where none did. A
        name sent since that read stays one the printer may hold, where no clear may go: a label
        passes with a read that got no answer when its last round downloaded nothing.
        """
        held = self._rendered_after if self._known is None else self._as_read(self._known)
        if self.result.retry_clear_may_go:
            return _PassedOn(held=held)
        return _PassedOn(held=held, maybe_held=FrozenMapping({name: self._sent[name] for name in self._unsure}))

    def read_first(self) -> _Skipped | _Refused | None:
        """Read the names before round 1, where it would download a name an earlier label may have left.

        Such a name was sent before a read that got no answer, where no clear may go. An answer
        settles it: the label is rendered again from what the module then lists, so a listed name
        is placed at full size rather than downloaded again, and its rounds go on as usual.

        Returns:
            None where round 1 may run (or, rendered again, the label downloads nothing);
            :class:`_Skipped` where the read got no answer too, so round 1 is left out;
            :class:`_Refused` where the label, rendered again, has an error.
        """
        if not self._downloaded_again(self.result):
            return None
        listed = self.link.read(self._module(), wait=self.check.wait)
        if listed.listed is None:
            twice = self._downloaded_again(self.result)
            skipped = _Skipped(
                f"round 1 skipped, it would download again {self._unread(twice)}; the names read before round 1 "
                f"got no answer either ({listed.unread_because})"
            )
            self._retried(skipped.reason)
            return skipped
        self._known, self._unsure = listed.listed, frozenset()
        again = self.render(self._held_before(listed.listed), halve=False)
        if not again.result.ok:
            return _Refused.of(again.result, when="from the names read before round 1")
        self._rendered(again)
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
        self._sent.update(self._rendered_after.sent)
        listed = self.link.read(module, wait=self.check.wait)
        if listed.listed is None:
            self._unsure |= frozenset(self.result.downloads)
        else:
            self._known, self._unsure = listed.listed, frozenset()
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
        return self._as_read(self._known)

    def _before_halving(self) -> HeldImages | None:
        """What the module holds before the halved upload, once the round's clear went; None where nothing is known.

        ``<STX>q`` leaves it empty. The plain Prodigy's removes take out only the label's own
        names, the full-size ones: a halved name the last read that answered listed stays held,
        and downloading it again, with no clear before it, would print nothing.
        """
        if self.result.retry_clear_empties:
            return EMPTY_MODULE
        if self.result.retry_clear_may_go and self._known is not None:
            return self._as_read(self._known - frozenset(self.result.downloads))
        return self._uncleared()

    def _downloaded_again(self, result: RenderResult) -> tuple[str, ...]:
        """The names ``result`` would download that may be held already, with no clear before them.

        A name sent since the last read that answered may have arrived; downloading it again, with
        no clear to empty the module first, would print nothing (one Allegro 2, 2026-11-01).
        """
        if self.result.retry_clear_may_go:
            return ()
        return tuple(name for name in result.downloads if name in self._unsure)

    @staticmethod
    def _unread(names: tuple[str, ...]) -> str:
        """Why ``names`` may be held: no clear may empty the module, and the read after them got no answer."""
        held = "it may be held" if len(names) == 1 else "they may be held"
        return (
            f"{', '.join(names)}: {held} already, since no clear may empty the module and the read after "
            "got no answer, and a download of a held name prints nothing"
        )

    def clear_and_upload(self) -> tuple[str, ...] | _Refused | _Skipped:
        """Round 2: clear where a clear may go, render again for what the module then holds, upload again.

        Where the upload again would download a name the module may hold (no clear may go, and the
        read after it got no answer), the round first reads the names again, uploading nothing
        before that read: with an answer the label is rendered again from that listing and the
        round goes on (a label left with nothing to download passes, at full size); with none the
        round is left out.

        Returns the names the read after it does not list; :class:`_Refused` instead where the
        label, rendered again, has an error, and :class:`_Skipped` where the names read again got
        no answer either: nothing of the round is sent then.
        """
        again = self._rendered_for_round_2()
        if isinstance(again, _Refused):
            return again
        read_again = ""
        if twice := self._downloaded_again(self.result if again is None else again.result):
            listed = self.link.read(self._module(), wait=self.check.wait)
            if listed.listed is None:
                skipped = _Skipped(
                    f"round 2 skipped, it would download again {self._unread(twice)}; the names read again got no "
                    f"answer either ({listed.unread_because})"
                )
                self._retried(skipped.reason)
                return skipped
            self._known, self._unsure = listed.listed, frozenset()
            found = _found(self.result, listed, self._module())
            module = self._module().value
            read_again = f", read module {module} again first as the read before it gave no listing ({found.text})"
            again = self._rendered_for_round_2()
            if isinstance(again, _Refused):
                return again
        cleared = self._clear()
        if again is not None:
            self._rendered(again)
        missing = self.upload()
        self._retried(f"round 2: {cleared}{read_again}, then {self._uploaded('the images again', read=not read_again)}")
        return missing

    def _rendered_for_round_2(self) -> RenderedRecord | _Refused | None:
        """The label rendered again for what the module holds once round 2's clear went.

        None where it goes again as it was: the plain Prodigy's removes take out its own names, or
        no read of it answered where no clear may go. :class:`_Refused` where it has an error.
        """
        base = EMPTY_MODULE if self.result.retry_clear_empties else self._uncleared()
        if base is None:
            return None
        again = self.render(base, halve=False)
        return again if again.result.ok else _Refused.of(again.result, when="for round 2")

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
        rendered = self.render(self.held if base is None else base, halve=True)
        halved = rendered.result
        if not halved.halved_elements or self.result.halved_elements:
            return _Skipped("no image of the label can be halved (a QR Code never is, nor an image halved already)")
        if not halved.ok:
            return _Skipped("halving is refused: " + "; ".join(f.message for f in halved.errors))
        if twice := self._downloaded_again(halved):
            stays = "a QR Code is never halved, so its name stays"
            return _Skipped(f"halving is skipped, it would download again ({stays}) {self._unread(twice)}")
        cleared = self._clear()
        self._rendered(rendered)
        missing = self.upload()
        self._retried(f"round 3: {cleared}, then {self._uploaded('the images halved 2x2')}")
        return missing

    def _clear(self) -> str:
        """Send the label's retry clear, apply it to what the last read that answered listed, and say what it did.

        The one place a clear goes out during the rounds, so what a later round or the next label
        takes the module to hold never keeps a name the clear took out: ``<STX>q`` empties the
        listing, the plain Prodigy's removes take out the label's own names. A name sent since
        that read needs nothing here: where a clear may go, none is carried as maybe held.
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

    def refuse(self, missing: tuple[str, ...], *, rounds: int, why_not: str = "") -> None:
        """Refuse the label: name the missing images and the rounds tried, and clear again."""
        if rounds:
            tried = "1 round" if rounds == 1 else f"{rounds} rounds"
            outcome = f"not stored after {tried} ({self._last_read})"
            reason = f"; no further round: {why_not}" if why_not else ""
        else:
            outcome, reason = "not sent", f": no round ran: {why_not}"
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


def _check_label(record: _Record) -> bool:
    """Run a label's rounds; return True when its images are listed and the rest may follow."""
    why_not: list[str] = []
    first = record.read_first()
    if isinstance(first, _Refused):
        record.refuse_rendered(first)
        return False
    if isinstance(first, _Skipped):
        missing, rounds = tuple(dict.fromkeys(record.result.downloads)), 0
        why_not.append(first.reason)
    else:
        # Rendered again from the names read first, a label may download nothing: its upload
        # then sends nothing and finds nothing missing.
        missing, rounds = record.upload(), 1
    if missing and record.check.rounds >= CLEAR_ROUND:
        outcome = record.clear_and_upload()
        if isinstance(outcome, _Skipped):
            why_not.append(outcome.reason)
        elif isinstance(outcome, _Refused):
            record.refuse_rendered(outcome)
            return False
        else:
            missing, rounds = outcome, rounds + 1
    if missing and record.check.rounds >= HALVING_ROUND:
        halving = record.halve_and_upload()
        if isinstance(halving, _Skipped):
            why_not.append(halving.reason)
        else:
            missing, rounds = halving, rounds + 1
    if missing:
        return _refused(record, missing, rounds=rounds, why_not=why_not)
    return True


def _refused(record: _Record, missing: tuple[str, ...], *, rounds: int, why_not: list[str]) -> bool:
    """Refuse ``record`` after ``rounds`` rounds run, each reason a round did not run in ``why_not``."""
    record.refuse(missing, rounds=rounds, why_not="; ".join(why_not))
    return False


def _unlisted_module(result: RenderResult) -> RenderResult | None:
    """The label refused with ``image_module_unlisted`` when it downloads into a module no directory lists.

    None for a label that downloads nothing, or whose module a directory lists.
    """
    if not result.downloads or result.image_module != ImageModule.C:
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


@dataclass(frozen=True)
class _LabelsSent:
    """The labels of a checked job reached, each as it went out, and whether every one was sent.

    A label refused (or, rendered again, found in error) is the last one reached, and none of its
    format went out.
    """

    sent: tuple[RenderResult, ...]
    all_sent: bool


def _refuse_mismatch(result: RenderResult | BatchResult, job: CheckedJob) -> None:
    """Refuse a result and renders describing different batches, before a label of either goes out.

    Raises:
        ConfigurationError: Their label counts differ.
    """
    labels = len(result.results) if isinstance(result, BatchResult) else 1
    if labels != len(job.renders):
        msg = f"the job holds {labels} labels but {len(job.renders)} renders; nothing was sent"
        raise ConfigurationError(msg)


def _after_reset(link: Link, render: RecordRender, job: CheckedJob) -> tuple[Finding, ...]:
    """Ask for a reset report and act on one, before the first label; return what the job should say.

    A read with no answer, like a printer that reports nothing, returns nothing: the job goes on
    exactly as it would have. Nothing is sent but the status request unless a reset was reported.
    """
    reset = job.reset
    if reset is None:
        return ()
    reported = job.held.reset_reported or read_reset_report(link.channel(), wait=reset.wait)
    if not reported:
        return ()
    if not reset.calibrate:
        return (_not_calibrated("[printer] calibrate_after_reset is off", severity=Severity.INFO),)
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


def _send_labels(link: Link, job: CheckedJob, *, cuts: Sequence[bool]) -> _LabelsSent:
    """Send each label of ``job`` in turn, with a batch cut after each label ``cuts`` marks."""
    sent: list[RenderResult] = []
    before = _PassedOn(held=job.held)
    for render, cut in zip(job.renders, cuts, strict=True):
        record = _Record(render=render, before=before, link=link, check=job.check)
        if not record.result.ok:
            return _LabelsSent(sent=(*sent, record.result), all_sent=False)
        if (unlisted := _unlisted_module(record.result)) is not None:
            return _LabelsSent(sent=(*sent, unlisted), all_sent=False)
        if not sent:
            record.findings.extend(_after_reset(link, render, job))
        if record.result.downloads and not _check_label(record):
            return _LabelsSent(sent=(*sent, record.as_sent()), all_sent=False)
        # Built after the check: a round may have rendered the label again.
        whole = with_cut(record.result.data, record.result.closing, cut=cut)
        link.send(whole[record.result.upload_end :] if record.result.downloads else whole)
        sent.append(record.as_sent())
        before = record.passed_on()
    return _LabelsSent(sent=tuple(sent), all_sent=True)


def send_checked_label(link: Link, job: CheckedJob) -> CheckedSend[RenderResult]:
    """Send the one label of ``job`` on ``link`` as :func:`send_checked` sends each label of a batch."""
    labels = _send_labels(link, job, cuts=(False,))
    return CheckedSend(result=labels.sent[0], all_sent=labels.all_sent)


def send_checked(
    link: Link, result: RenderResult | BatchResult, job: CheckedJob
) -> CheckedSend[RenderResult | BatchResult]:
    """Send ``result`` label by label on ``link``, each label that downloads an image after its upload check.

    ``result`` is the job as first rendered, with its batch cuts and findings and the result of
    each label not reached. Each label is rendered again from ``job.renders`` with what the printer
    holds before it, which gives the render ``result`` holds until a round changes what is held.

    Returns:
        The job as it went out, each label reached with the rounds' findings, and whether every
        label was sent (see :class:`CheckedSend`).

    Raises:
        ConfigurationError: ``result`` and ``job.renders`` hold different numbers of labels.
    """
    _refuse_mismatch(result, job)
    if not isinstance(result, BatchResult):
        return send_checked_label(link, job)
    # Refused before the first record goes out: a record that downloads nothing would print, and
    # the check of the first one that downloads could never pass.
    for index, record in enumerate(result.results):
        if (unlisted := _unlisted_module(record)) is not None:
            labels = _LabelsSent(sent=(*result.results[:index], unlisted), all_sent=False)
            break
    else:
        labels = _send_labels(link, job, cuts=result.cut_after or (False,) * len(result.results))
    whole = BatchResult(
        results=(*labels.sent, *result.results[len(labels.sent) :]),
        batch_findings=result.batch_findings,
        cut_after=result.cut_after,
    )
    return CheckedSend(result=whole, all_sent=labels.all_sent)


def print_checked(
    result: RenderResult | BatchResult, job: CheckedJob, *, uri: str, timeout: float, open_printer: OpenPrinter
) -> CheckedPrint:
    """Send a rendered job, checking each label's image upload before its format goes out.

    A channel that cannot read answers gets the whole job at once, as :func:`print_result` sends it.

    Raises:
        LabelRejectedError: The job has an error finding; nothing was sent.
        UploadRefusedError: A label's images were still not listed after every round, or the
            label, rendered again for what the printer then held, has an error finding
            (``image_upload_render_refused``); its format and everything after it were not sent.
            Or the job downloads into module C, which no directory lists
            (``image_module_unlisted``); nothing was sent.
        ConfigurationError: ``result`` and ``job.renders`` hold different numbers of labels; nothing
            was sent.
        TransportError: The printer could not be reached or written.
    """
    if not result.ok:
        msg = "label has errors; nothing was sent"
        raise LabelRejectedError(msg)
    _refuse_mismatch(result, job)
    printer = open_printer(uri, timeout=timeout)
    link = Link(printer=printer, uri=uri, timeout=timeout, open_printer=open_printer, reconnect=job.check.wait)
    try:
        if not printer.bidirectional:
            for piece in result.record_jobs() if isinstance(result, BatchResult) else (result.data,):
                link.send(piece)
            return CheckedPrint(uri=uri, bytes_sent=link.bytes_sent, result=result)
        checked = send_checked(link, result, job)
    finally:
        printer.close()
        link.close_opened()
    if not checked.all_sent:
        raise UploadRefusedError(checked.result, bytes_sent=link.bytes_sent)
    return CheckedPrint(uri=uri, bytes_sent=link.bytes_sent, result=checked.result)


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
