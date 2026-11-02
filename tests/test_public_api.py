"""What a library caller can catch and read about from the package root."""

from __future__ import annotations

import pytest

import datamax_dpl
from datamax_dpl.adapters.config import get_config
from datamax_dpl.adapters.preview import preview_findings, render_preview_png
from datamax_dpl.domain.render_context import RenderOptions


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "name", ["UnknownModelError", "MissingFieldError", "ConfigurationError", "PrinterTimeoutError"]
)
def test_an_exception_the_library_raises_is_importable_from_the_root(name: str) -> None:
    assert name in datamax_dpl.__all__
    assert issubclass(getattr(datamax_dpl, name), Exception)


@pytest.mark.os_agnostic
def test_the_exported_unknown_model_error_is_the_one_render_raises() -> None:
    label = datamax_dpl.Label(elements=[datamax_dpl.Text(x=1, y=1, text="A")])
    with pytest.raises(datamax_dpl.UnknownModelError):
        datamax_dpl.render(label, RenderOptions(model="bogus"))


@pytest.mark.os_agnostic
def test_the_exported_missing_field_error_is_the_one_fill_label_raises() -> None:
    label = datamax_dpl.Label(elements=[datamax_dpl.Text(x=1, y=1, text="{absent}")])
    with pytest.raises(datamax_dpl.MissingFieldError):
        datamax_dpl.fill_label(label, {})


_DOCUMENTED = [
    ("render", ["Args:", "Returns:", "Raises:", "UnknownModelError"]),
    ("render_batch", ["Args:", "Returns:", "Raises:", "UnknownModelError"]),
    ("fill_label", ["Args:", "Returns:", "Raises:"]),
    ("get_profile", ["Args:", "Returns:", "Raises:"]),
    ("explain", ["Args:", "Returns:"]),
]


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("name", "sections"), _DOCUMENTED)
def test_a_public_function_documents_what_it_takes_returns_and_raises(name: str, sections: list[str]) -> None:
    assert all(section in (getattr(datamax_dpl, name).__doc__ or "") for section in sections)


@pytest.mark.os_agnostic
def test_the_preview_functions_document_what_they_take_return_and_raise() -> None:
    assert all(s in (render_preview_png.__doc__ or "") for s in ["Args:", "Returns:", "UnknownModelError"])
    assert all(s in (preview_findings.__doc__ or "") for s in ["Args:", "Returns:"])


@pytest.mark.os_agnostic
def test_get_config_documents_its_refusal_of_a_bad_profile() -> None:
    assert "ValueError" in (get_config.__doc__ or "")
    with pytest.raises(ValueError, match="profile"):
        get_config(profile="../x")
