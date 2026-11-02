"""Asking a channel that cannot answer is refused before the channel is opened."""

from __future__ import annotations

import dataclasses
import json
from typing import TYPE_CHECKING, Any

import pytest
from lib_layered_config import Config

from datamax_dpl.adapters.cli.main import main
from datamax_dpl.composition import AppServices, build_production

if TYPE_CHECKING:
    from pathlib import Path


def _services() -> AppServices:
    def get_config(**_kwargs: Any) -> Config:
        return Config({}, {})

    return dataclasses.replace(build_production(), get_config=get_config)


QUERIES = [["status"], ["memory"], ["detect"]]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("query", QUERIES, ids=lambda query: query[0])
def test_a_query_of_a_file_refuses_without_creating_the_file(
    tmp_path: Path, query: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "job.dpl"
    code = main(["--json", *query, "--printer", f"file:{target}"], services_factory=_services)
    assert code == 2
    assert json.loads(capsys.readouterr().out)["error"] == "UnsupportedOperationError"
    assert not target.exists()


@pytest.mark.os_agnostic
@pytest.mark.parametrize("query", QUERIES, ids=lambda query: query[0])
def test_a_query_of_a_file_in_a_missing_folder_gets_the_same_refusal(
    tmp_path: Path, query: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """The refusal says what is wrong with the request, not that a folder is missing."""
    target = tmp_path / "missing" / "job.dpl"
    code = main(["--json", *query, "--printer", f"file:{target}"], services_factory=_services)
    assert code == 2
    assert "cannot open" not in json.loads(capsys.readouterr().out)["message"]
    assert not target.parent.exists()


@pytest.mark.os_agnostic
@pytest.mark.parametrize("query", QUERIES, ids=lambda query: query[0])
def test_a_query_of_stdout_is_refused_and_prints_nothing_but_the_envelope(
    query: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--json", *query, "--printer", "-"], services_factory=_services)
    assert code == 2
    assert json.loads(capsys.readouterr().out)["error"] == "UnsupportedOperationError"
