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
again, which would print nothing; that listing passes on to the next label of a batch. A label
that, rendered again for a round, has an error finding (the module it now holds leaves too little
memory, a held name in error) is refused there, before anything of that round is sent.

Still missing: the label is refused (``image_upload_refused``), its format is never sent, nothing
after it is sent, and the module is cleared again where a clear may go. Each round after the
first adds an ``image_upload_retried`` finding saying what it did and what the printer then listed.

A job whose images go to module C, the printer's default module, which no directory lists under
that letter, is refused before anything is sent (``image_module_unlisted``): the check could never
pass, and the format may not go out before it has.

In a batch each label is checked on its own, and a label rendered again (with the module known
empty after a clear, or halved) passes what the printer then holds on to the next label. A label
that, rendered with what the printer holds after the labels before it, has an error finding stops
the batch there: none of it and nothing after it is sent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple

from ..domain.batch import with_cut
from ..domain.enums import FindingCode, Severity
from ..domain.errors import LabelRejectedError
from ..domain.findings import Finding
from ..domain.held_images import DEFAULT_MODULE, EMPTY_MODULE, HeldImages
from ..domain.profiles import get_profile
from .use_cases import AnswerWait, BatchResult, open_within, read_stored_names

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..domain.render import RenderResult
    from .ports import OpenPrinter, PrinterTransport
    from .use_cases import RecordRender

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


class CheckedPrint(NamedTuple):
    """What a checked send sent where, and the job as it went out.

    Attributes:
        uri: The printer.
        bytes_sent: Job bytes written, every round's uploads and clears included.
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
    """

    renders: Sequence[RecordRender]
    check: UploadCheck
    held: HeldImages = HeldImages()


class UploadRefusedError(LabelRejectedError):
    """The printer did not list a label's images after every round, or never could; that label's format was never sent.

    Attributes:
        result: The job as far as it went, the refused label's ``image_upload_refused`` included.
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
            none (``stored_names_wait``, as the read itself); None connects once.
        bytes_sent: Job bytes written so far, the directory requests not counted.
    """

    printer: PrinterTransport | None
    uri: str
    timeout: float
    open_printer: OpenPrinter
    reconnect: AnswerWait | None
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
        if self.printer is None:
            self.printer = (
                self.open_printer(self.uri, timeout=self.timeout)
                if self.reconnect is None
                else open_within(
                    uri=self.uri, timeout=self.timeout, open_printer=self.open_printer, wait=self.reconnect
                )
            )
            self._opened.append(self.printer)
        self.printer.send(data)
        self.bytes_sent += len(data)

    def read(self, module: str, *, wait: AnswerWait) -> HeldImages:
        """Read what ``module`` holds on the open channel; keep the channel that answered."""
        before = self.printer
        self.printer, held = read_stored_names(
            self.printer, module=module, uri=self.uri, timeout=self.timeout, open_printer=self.open_printer, wait=wait
        )
        if self.printer is not None and self.printer is not before:
            self._opened.append(self.printer)
        return held

    def close_opened(self) -> None:
        """Close every channel this link opened itself; the one it was given stays the caller's."""
        for printer in self._opened:
            printer.close()


def _missing(result: RenderResult, held: HeldImages) -> tuple[str, ...]:
    """The names the label downloaded that ``held`` does not list: all of them when it lists nothing known."""
    names = tuple(dict.fromkeys(result.downloads))
    if held.listed is None:
        return names
    return tuple(name for name in names if name not in held.listed)


def _what_was_read(module: str, missing: tuple[str, ...], held: HeldImages) -> str:
    """Say what the read after an upload found, for a finding's message."""
    if not missing:
        return f"module {module} then listed every image"
    if held.listed is None:
        return f"module {module} could not be read ({held.unread_because})"
    return f"module {module} did not list {', '.join(missing)}"


def _clear_text(result: RenderResult) -> str:
    """Say which clear went before an upload again, for a finding's message."""
    if result.retry_clear_empties:
        return f"cleared module {result.image_module}"
    if result.retry_clear:
        return "removed the label's own image names"
    return f"sent no clear (module {result.image_module} may not be cleared)"


@dataclass
class _Record:
    """One label's rounds: its render, what the printer held before it and the rounds' findings."""

    render: RecordRender
    held: HeldImages
    link: Link
    check: UploadCheck
    result: RenderResult = field(init=False)
    after: HeldImages = field(init=False)
    findings: list[Finding] = field(default_factory=list[Finding])
    _last_read: str = field(default="", init=False)
    _listed: HeldImages = field(default=HeldImages(), init=False)

    def __post_init__(self) -> None:
        """Render the label as it is first sent."""
        self.result, self.after = self.render(self.held, halve=False)

    def upload(self) -> tuple[str, ...]:
        """Send the label's upload, read the directory and return the names it does not list."""
        module = self.result.image_module or ""
        self.link.send(self.result.data[: self.result.upload_end])
        listed = self.link.read(module, wait=self.check.wait)
        self._listed = listed
        self._last_read = _what_was_read(module, missing := _missing(self.result, listed), listed)
        return missing

    def _uncleared(self) -> HeldImages | None:
        """What the module holds before an upload again where no clear went, from the last read.

        None where a clear went (the module is known empty, or the Prodigy removed the label's own
        names, so its downloads go again as they were), or where the read listed nothing known.
        A name the read listed is held: downloading it again would print nothing (one Allegro 2,
        2026-11-01), so the label is rendered again leaving it out.
        """
        if self.result.retry_clear or self._listed.listed is None:
            return None
        return self.after.as_listed(self._listed.listed)

    def clear_and_upload(self) -> tuple[str, ...] | str:
        """Round 2: clear where a clear may go, render again for what the module then holds, upload again.

        The reason instead where the label, rendered again, has an error: nothing of the round
        is sent then.
        """
        base = EMPTY_MODULE if self.result.retry_clear_empties else self._uncleared()
        again = None if base is None else self.render(base, halve=False)
        if again is not None and not again[0].ok:
            return "rendered again for round 2 the label is refused: " + "; ".join(f.message for f in again[0].errors)
        self.link.send(self.result.retry_clear)
        cleared = _clear_text(self.result)
        if again is not None:
            self.result, self.after = again
        missing = self.upload()
        self._retried(f"round 2: {cleared}, then uploaded the images again; {self._last_read}")
        return missing

    def halve_and_upload(self) -> tuple[str, ...] | str:
        """Round 3: clear, upload the images halved; the reason instead where they cannot be halved."""
        base = EMPTY_MODULE if self.result.retry_clear_empties else self._uncleared()
        halved, after = self.render(self.held if base is None else base, halve=True)
        if not halved.halved_elements or self.result.halved_elements:
            return "no image of the label can be halved (a QR Code never is, nor an image halved already)"
        if not halved.ok:
            return "halving is refused: " + "; ".join(f.message for f in halved.errors)
        self.link.send(self.result.retry_clear)
        cleared = _clear_text(self.result)
        self.result, self.after = halved, after
        missing = self.upload()
        self._retried(f"round 3: {cleared}, then uploaded the images halved 2x2; {self._last_read}")
        return missing

    def _retried(self, message: str) -> None:
        self.findings.append(Finding(code=FindingCode.IMAGE_UPLOAD_RETRIED, severity=Severity.INFO, message=message))

    def refuse(self, missing: tuple[str, ...], *, rounds: int, why_not: str = "") -> None:
        """Refuse the label: name the missing images and the rounds tried, and clear again."""
        tried = "1 round" if rounds == 1 else f"{rounds} rounds"
        reason = f"; no further round: {why_not}" if why_not else ""
        self.findings.append(
            Finding(
                code=FindingCode.IMAGE_UPLOAD_REFUSED,
                severity=Severity.ERROR,
                message=(
                    f"images {', '.join(missing)} not stored after {tried} ({self._last_read}){reason}; the label's "
                    "format was not sent, since it would print without them"
                ),
            )
        )
        self.link.send(self.result.retry_clear)

    def as_sent(self) -> RenderResult:
        """The label's result as it went out, with the rounds' findings."""
        return self.result.model_copy(update={"findings": self.result.findings + tuple(self.findings)})


def _check_label(record: _Record) -> bool:
    """Run a label's rounds; return True when its images are listed and the rest may follow."""
    missing = record.upload()
    rounds = 1
    why_not = ""
    if missing and record.check.rounds >= CLEAR_ROUND:
        outcome = record.clear_and_upload()
        if isinstance(outcome, str):
            why_not = outcome
        else:
            missing, rounds = outcome, CLEAR_ROUND
    if missing and not why_not and record.check.rounds >= HALVING_ROUND:
        outcome = record.halve_and_upload()
        if isinstance(outcome, str):
            why_not = outcome
        else:
            missing, rounds = outcome, HALVING_ROUND
    if missing:
        record.refuse(missing, rounds=rounds, why_not=why_not)
        return False
    return True


def _unlisted_module(result: RenderResult) -> RenderResult | None:
    """The label refused with ``image_module_unlisted`` when it downloads into a module no directory lists.

    None for a label that downloads nothing, or whose module a directory lists.
    """
    if not result.downloads or result.image_module != DEFAULT_MODULE:
        return None
    built_in = get_profile(result.model).image_module
    letter = f" (module {built_in} on the {result.model})" if built_in else ""
    refusal = Finding(
        code=FindingCode.IMAGE_MODULE_UNLISTED,
        severity=Severity.ERROR,
        message=(
            f"image_module {DEFAULT_MODULE} is the printer's default module, which no directory lists by that "
            "letter, so the upload check can never see the images; nothing was sent. Set image_module to a "
            f"module letter the printer lists{letter}, or print over a one-way link"
        ),
    )
    return result.model_copy(update={"findings": (*result.findings, refusal)})


def _send_labels(link: Link, job: CheckedJob, *, cuts: Sequence[bool]) -> tuple[list[RenderResult], bool]:
    """Send each label of ``job`` in turn, with a batch cut after each label ``cuts`` marks.

    Returns each label reached as it went out, and True when every one was sent; a label refused
    (or, rendered again, found in error) is the last one reached, and none of its format went out.
    """
    sent: list[RenderResult] = []
    held = job.held
    for render, cut in zip(job.renders, cuts, strict=True):
        record = _Record(render=render, held=held, link=link, check=job.check)
        if not record.result.ok:
            sent.append(record.result)
            return sent, False
        if (unlisted := _unlisted_module(record.result)) is not None:
            sent.append(unlisted)
            return sent, False
        if record.result.downloads and not _check_label(record):
            sent.append(record.as_sent())
            return sent, False
        # Built after the check: a round may have rendered the label again.
        whole = with_cut(record.result.data, record.result.closing, cut=cut)
        link.send(whole[record.result.upload_end :] if record.result.downloads else whole)
        sent.append(record.as_sent())
        held = record.after
    return sent, True


def send_checked_label(link: Link, job: CheckedJob) -> tuple[RenderResult, bool]:
    """Send the one label of ``job`` on ``link`` as :func:`send_checked` sends each label of a batch."""
    sent, done = _send_labels(link, job, cuts=(False,))
    return sent[0], done


def send_checked(
    link: Link, result: RenderResult | BatchResult, job: CheckedJob
) -> tuple[RenderResult | BatchResult, bool]:
    """Send ``result`` label by label on ``link``, each label that downloads an image after its upload check.

    ``result`` is the job as first rendered, with its batch cuts and findings and the result of
    each label not reached. Each label is rendered again from ``job.renders`` with what the printer
    holds before it, which gives the render ``result`` holds until a round changes what is held.

    Returns:
        The job as it went out, each label reached with the rounds' findings, and True when every
        label was sent; False when a label was refused, which nothing after it follows.
    """
    if not isinstance(result, BatchResult):
        return send_checked_label(link, job)
    # Refused before the first record goes out: a record that downloads nothing would print, and
    # the check of the first one that downloads could never pass.
    for index, record in enumerate(result.results):
        if (unlisted := _unlisted_module(record)) is not None:
            sent, done = [*result.results[:index], unlisted], False
            break
    else:
        sent, done = _send_labels(link, job, cuts=result.cut_after or (False,) * len(result.results))
    batch = BatchResult(
        data=b"",
        results=(*sent, *result.results[len(sent) :]),
        batch_findings=result.batch_findings,
        cut_after=result.cut_after,
    )
    return batch.model_copy(update={"data": b"".join(batch.record_jobs())}), done


def print_checked(
    result: RenderResult | BatchResult, job: CheckedJob, *, uri: str, timeout: float, open_printer: OpenPrinter
) -> CheckedPrint:
    """Send a rendered job, checking each label's image upload before its format goes out.

    A channel that cannot read answers gets the whole job at once, as :func:`print_result` sends it.

    Raises:
        LabelRejectedError: The job has an error finding; nothing was sent.
        UploadRefusedError: A label's images were still not listed after every round; its format
            and everything after it were not sent. Or the job downloads into module C, which no
            directory lists (``image_module_unlisted``); nothing was sent.
        TransportError: The printer could not be reached or written.
    """
    if not result.ok:
        msg = "label has errors; nothing was sent"
        raise LabelRejectedError(msg)
    printer = open_printer(uri, timeout=timeout)
    link = Link(printer=printer, uri=uri, timeout=timeout, open_printer=open_printer, reconnect=job.check.wait)
    try:
        if not printer.bidirectional:
            link.send(result.data)
            return CheckedPrint(uri=uri, bytes_sent=link.bytes_sent, result=result)
        sent, done = send_checked(link, result, job)
    finally:
        printer.close()
        link.close_opened()
    if not done:
        raise UploadRefusedError(sent, bytes_sent=link.bytes_sent)
    return CheckedPrint(uri=uri, bytes_sent=link.bytes_sent, result=sent)


__all__ = [
    "MAX_UPLOAD_CHECK_ROUNDS",
    "MIN_UPLOAD_CHECK_ROUNDS",
    "UPLOAD_CHECK_ROUNDS",
    "CheckedJob",
    "CheckedPrint",
    "Link",
    "UploadCheck",
    "UploadRefusedError",
    "print_checked",
    "send_checked",
    "send_checked_label",
]
