"""The OGM takes its store by the triple store interface's contract, `TripleStore`.

The contract declares the operations that the layers above the interface call on a store, and no
other. So the OGM annotates every store it takes as `TripleStore`, never as the class of an
adapter, and the mocked tier builds its store as a mock with the spec `TripleStore`: an operation
that the OGM calls and that the contract does not declare fails there with an `AttributeError`.
"""

import importlib
import inspect
import pkgutil
import typing
from unittest.mock import Mock

import pytest

from kapps_triplestore_interface import TripleStore

import kapps_ogm
from kapps_ogm.ogm import OGM


def _package_functions():
    """Each function and method that a module of the package defines, with its qualified name."""
    for info in pkgutil.walk_packages(kapps_ogm.__path__, prefix="kapps_ogm."):
        module = importlib.import_module(info.name)
        for name, member in vars(module).items():
            if getattr(member, "__module__", None) != module.__name__:
                continue
            if inspect.isfunction(member):
                yield f"{module.__name__}.{name}", member
            elif inspect.isclass(member):
                for attribute, function in vars(member).items():
                    if inspect.isfunction(function):
                        yield f"{module.__name__}.{name}.{attribute}", function


def test_the_ogm_annotates_its_store_as_the_contract():
    assert typing.get_type_hints(OGM.__init__)["db"] is TripleStore


def test_no_function_of_the_package_names_an_adapter_class_for_a_store():
    """A parameter that takes the store is annotated with the contract, wherever it is."""
    named_adapter = []
    for qualified_name, function in _package_functions():
        for parameter in inspect.signature(function).parameters.values():
            annotation = parameter.annotation
            text = annotation if isinstance(annotation, str) else repr(annotation)
            if parameter.name == "db" and annotation not in (
                TripleStore,
                "TripleStore",
            ):
                named_adapter.append(f"{qualified_name}({parameter.name}: {text})")
            elif "GraphDB" in text:
                named_adapter.append(f"{qualified_name}({parameter.name}: {text})")
    assert named_adapter == []


def test_the_mocked_store_has_the_contract_as_its_spec(mock_db: Mock):
    assert mock_db.__class__ is TripleStore


def test_the_mocked_store_refuses_an_operation_the_contract_does_not_declare(
    mock_db: Mock,
):
    with pytest.raises(AttributeError):
        mock_db.owl_is_named_individual  # pylint: disable=pointless-statement
