"""What a library caller can catch and read about from the package root."""

from __future__ import annotations

import builtins
import importlib
import inspect
import pkgutil
import re

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


# ======================== The exception contract ========================

#: What a public docstring may name besides the package's own types, each stated in the README.
_FOREIGN_RAISES = {"ValidationError", "ConfigError"}
_RAISES_ENTRY = re.compile(r"^ {4}(\w+):", flags=re.MULTILINE)
_NEXT_SECTION = re.compile(r"^\S", flags=re.MULTILINE)


def _raised_in(doc: str | None) -> set[str]:
    """The names a Google-style ``Raises:`` section lists."""
    _before, found, after = inspect.cleandoc(doc or "").partition("Raises:\n")
    if not found:
        return set()
    return set(_RAISES_ENTRY.findall(_NEXT_SECTION.split(after, maxsplit=1)[0]))


def _public_objects() -> list[tuple[str, object]]:
    """Every exported callable, and the public methods of each exported class defined in the package."""
    found: list[tuple[str, object]] = []
    for name in datamax_dpl.__all__:
        exported = getattr(datamax_dpl, name)
        if inspect.isclass(exported) and exported.__module__.startswith("datamax_dpl"):
            found.extend(
                (f"{name}.{member}", item)
                for member, item in vars(exported).items()
                if not member.startswith("_") and callable(item)
            )
        if callable(exported):
            found.append((name, exported))
    return found


def _package_modules() -> list[str]:
    """Every module of the package; ``__main__`` runs the CLI when imported, so it is left out."""
    walked = pkgutil.walk_packages(datamax_dpl.__path__, prefix="datamax_dpl.")
    return [module.name for module in walked if not module.name.endswith("__main__")]


def _package_exceptions() -> dict[str, type[BaseException]]:
    """Every public exception class the package defines, by name."""
    found: dict[str, type[BaseException]] = {}
    for name in _package_modules():
        for member, item in vars(importlib.import_module(name)).items():
            defined_here = inspect.isclass(item) and item.__module__.startswith("datamax_dpl")
            if defined_here and issubclass(item, BaseException) and not member.startswith("_"):
                found[member] = item
    return found


@pytest.mark.os_agnostic
def test_the_walk_over_the_public_docstrings_finds_what_it_must() -> None:
    """A parser that finds nothing would pass every name: render's documented raise must come out."""
    objects = dict(_public_objects())
    assert "UnknownModelError" in _raised_in(inspect.getdoc(objects["render"]))
    assert len(objects) > 40
    assert _raised_in(
        "Summary.\n\nRaises:\n    FooError: a.\n        more.\n    BarError: b.\n\nExample:\n    x: y"
    ) == {
        "FooError",
        "BarError",
    }


@pytest.mark.os_agnostic
def test_every_type_a_public_docstring_raises_can_be_imported_from_the_root() -> None:
    known = set(datamax_dpl.__all__) | _FOREIGN_RAISES
    missing = {
        f"{name}: {raised}"
        for name, item in _public_objects()
        for raised in _raised_in(inspect.getdoc(item))
        if raised not in known and not hasattr(builtins, raised)
    }
    assert not missing


@pytest.mark.os_agnostic
def test_every_exception_the_package_defines_shares_the_base() -> None:
    exceptions = _package_exceptions()
    assert "TransportError" in exceptions
    assert not {name for name, item in exceptions.items() if not issubclass(item, datamax_dpl.DatamaxDplError)}


@pytest.mark.os_agnostic
def test_every_exception_a_library_caller_can_meet_is_exported() -> None:
    """The label input error is the CLI's own: no function of the library surface raises it."""
    exported = set(_package_exceptions()) - {"LabelInputError"}
    assert exported <= set(datamax_dpl.__all__)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("name", "builtin"),
    [
        ("UnknownModelError", LookupError),
        ("MissingFieldError", LookupError),
        ("QrDataTooLongError", ValueError),
        ("PreviewTooLongError", ValueError),
    ],
)
def test_the_base_keeps_the_builtin_a_caller_may_already_catch(name: str, builtin: type[Exception]) -> None:
    exception = getattr(datamax_dpl, name)
    assert issubclass(exception, builtin)
    assert issubclass(exception, datamax_dpl.DatamaxDplError)
