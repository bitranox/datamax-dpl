"""Models carrying raw bytes serialise to JSON and back without loss.

A DPL job is not UTF-8: charset-mapped text (an umlaut becomes 0x94) and image records are raw
bytes, so pydantic's default JSON form for ``bytes`` (a UTF-8 string) raises on them.
"""

from __future__ import annotations

import importlib
import pkgutil
import typing

import pytest
from pydantic import BaseModel

import datamax_dpl
from datamax_dpl import BatchResult, Label, RenderOptions, RenderResult, Text, build_job, render_batch

NOT_UTF8_TEXT = "Größe 5"


def _job() -> RenderResult:
    return build_job(Label(elements=[Text(x=5, y=5, text=NOT_UTF8_TEXT)]), RenderOptions(model="dmx-600"))


@pytest.mark.os_agnostic
def test_the_probe_job_really_holds_a_byte_that_is_not_utf8() -> None:
    """Guards the tests below: a pure-ASCII job would pass them with the bug still in place."""
    with pytest.raises(UnicodeDecodeError):
        _job().data.decode("utf-8")


@pytest.mark.os_agnostic
def test_a_render_result_round_trips_through_json() -> None:
    """model_dump_json must not raise, and validating its output must give the same bytes back."""
    result = _job()
    assert RenderResult.model_validate_json(result.model_dump_json()).data == result.data


@pytest.mark.os_agnostic
def test_a_batch_result_round_trips_through_json() -> None:
    """The joined batch bytes and each record's bytes survive JSON."""
    label = Label(elements=[Text(x=5, y=5, text="{name}")])
    batch = render_batch(label, [{"name": NOT_UTF8_TEXT}], RenderOptions(model="dmx-600"))
    back = BatchResult.model_validate_json(batch.model_dump_json())
    assert back.data == batch.data
    assert back.results[0].data == batch.results[0].data


def _holds_bytes(annotation: object) -> bool:
    if annotation is bytes:
        return True
    return any(_holds_bytes(arg) for arg in typing.get_args(annotation))


def _package_models() -> list[type[BaseModel]]:
    for module in pkgutil.walk_packages(datamax_dpl.__path__, f"{datamax_dpl.__name__}."):
        importlib.import_module(module.name)
    seen: list[type[BaseModel]] = []
    pending: list[type[BaseModel]] = list(BaseModel.__subclasses__())
    while pending:
        cls = pending.pop()
        pending.extend(cls.__subclasses__())
        if cls.__module__.startswith(f"{datamax_dpl.__name__}.") and cls not in seen:
            seen.append(cls)
    return seen


def _models_with_bytes() -> list[type[BaseModel]]:
    return [cls for cls in _package_models() if any(_holds_bytes(f.annotation) for f in cls.model_fields.values())]


@pytest.mark.os_agnostic
def test_the_model_scan_finds_the_known_bytes_models() -> None:
    """The scan must see the models it guards, or the next test passes over an empty list."""
    names = {cls.__name__ for cls in _models_with_bytes()}
    assert {"RenderResult", "BatchResult"} <= names


@pytest.mark.os_agnostic
@pytest.mark.parametrize("model", _models_with_bytes(), ids=lambda cls: cls.__name__)
def test_every_model_with_bytes_uses_base64_json(model: type[BaseModel]) -> None:
    """A new bytes field elsewhere must declare base64 both ways, or its JSON breaks on the first umlaut."""
    config = model.model_config
    assert config.get("ser_json_bytes") == "base64"
    assert config.get("val_json_bytes") == "base64"
