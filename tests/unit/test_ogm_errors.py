"""The OGM's own errors: one base and four categories, each named by what the caller must do.

``OGMDataError`` is Refused, ``OGMOntologyError`` is Repair, ``OGMNotFoundError`` is Refresh, and
``OGMUsageError`` with ``OGMUsageTypeError`` is Fix the call. Each category also subclasses the
built-in that it replaces, so an ``except ValueError`` or ``except TypeError`` of a consumer keeps
working, and each message stays the text it was. These tests run on a mocked store.
"""

import ast
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from kapps_triplestore_interface import IRI, TripleStore
from kapps_triplestore_interface.exceptions import (
    TripleStoreResponseError,
    InvalidQueryError,
)

import kapps_ogm

from kapps_ogm import (
    OGM,
    OGMDataError,
    OGMError,
    OGMNotFoundError,
    OGMOntologyError,
    OGMUsageError,
    OGMUsageTypeError,
    Create,
    Update,
)
from kapps_ogm.mapping.class_spec import ClassHydrationLevel, ClassSpec
from kapps_ogm.mapping.property_spec import PropertySpec, PropertyValueKind
from kapps_ogm.utils.class_scope import ClassScope
from kapps_ogm.utils import (
    AmbiguousNodeAlignmentError,
    AnonymousNodeFetchError,
    UnresolvableNodeAddressError,
    mint_skolem_iri,
)

NS = "http://example.org/errors#"
BELT = IRI(NS + "Belt")
BELT1 = IRI(NS + "belt1")
HAS_SPEED = IRI(NS + "hasSpeed")
HAS_COLOUR = IRI(NS + "hasColour")
HAS_WIDTH = IRI(NS + "hasWidth")


def types_of(types: dict[IRI, IRI]):
    """A ``triples_get`` that gives each subject of ``types`` its one rdf:type, and else nothing."""

    def triples_get(sub=None, pred=None, **_):
        if pred == "rdf:type" and sub in types:
            return [(sub, IRI("rdf:type"), types[sub])]
        return []

    return triples_get


@pytest.fixture
def store() -> Mock:
    db = Mock(spec=TripleStore)
    db.triples_get.return_value = []
    db.iri_exists.return_value = False
    db.owl_get_classes_of_individual.return_value = set()
    return db


@pytest.fixture
def ogm(store: Mock) -> OGM:
    """An OGM on the mocked store. It shadows the live ``ogm`` of the root conftest here."""
    return OGM(db=store)


def belt_spec() -> ClassSpec:
    return ClassSpec(
        iri=BELT, types=[], hydration_level=ClassHydrationLevel.FULL, properties={}
    )


# ---------------------------------------------------------------------------
# The data breaks the class: Refused
# ---------------------------------------------------------------------------


class TestOGMDataError:
    def test_is_an_ogm_error_and_a_value_error(self):
        assert issubclass(OGMDataError, OGMError)
        assert issubclass(OGMDataError, ValueError)

    def test_carries_the_raw_context(self):
        error = OGMDataError(
            "message",
            instance_iri=BELT1,
            property_iri=IRI(NS + "hasSpeed"),
            class_iri=BELT,
            value="fast",
        )
        assert str(error) == "message"
        assert error.instance_iri == BELT1
        assert error.property_iri == IRI(NS + "hasSpeed")
        assert error.class_iri == BELT
        assert error.value == "fast"

    def test_an_attribute_that_does_not_apply_is_none(self):
        error = OGMDataError("message")
        assert (error.instance_iri, error.property_iri, error.class_iri) == (
            None,
            None,
            None,
        )
        assert error.value is None

    def test_its_docstring_names_its_action(self):
        assert "Refused" in OGMDataError.__doc__

    @pytest.mark.parametrize(
        "unknown, named",
        [
            ({HAS_COLOUR}, HAS_COLOUR),
            ({HAS_COLOUR, HAS_WIDTH}, None),
        ],
        ids=["one property", "two properties"],
    )
    def test_a_class_scope_with_a_property_the_class_lacks_keeps_its_message(
        self, ogm, store, unknown, named
    ):
        """The scope comes from the data on the commit path: the data names the property."""
        store.triples_get.side_effect = types_of({BELT: IRI("owl:Class")})
        store.query.return_value = {"results": {"bindings": [{"property": HAS_SPEED}]}}
        scope = ClassScope({HAS_SPEED: {}, **{prop: {} for prop in unknown}})

        with pytest.raises(OGMDataError) as caught:
            ClassSpec.specify(
                class_iri=BELT,
                ogm=ogm,
                hydration_level=ClassHydrationLevel.SCOPE,
                class_scope=scope,
            )

        if named is not None:
            assert str(caught.value) == (
                f"Properties {set(unknown)} specified in class scope, but not found as "
                f"property of class {BELT}."
            )
        assert caught.value.class_iri == BELT
        assert caught.value.property_iri == named
        assert (caught.value.instance_iri, caught.value.value) == (None, None)

    def test_a_property_chain_past_a_literal_keeps_its_message(self, ogm, store):
        """The scope continues a property whose range is a literal, so no class follows it."""
        store.triples_get.side_effect = types_of(
            {HAS_SPEED: IRI("owl:DatatypeProperty")}
        )
        nested_scope = ClassScope({HAS_COLOUR: {}})

        with (
            patch.object(
                PropertySpec, "_resolve_effective_ranges", return_value={float}
            ),
            pytest.raises(OGMDataError) as caught,
        ):
            PropertySpec.specify(
                prop_iri=HAS_SPEED,
                nested_scope=nested_scope,
                ogm=ogm,
                hydration_level=ClassHydrationLevel.SCOPE,
                _path=(BELT,),
            )

        assert str(caught.value) == (
            f"Property {HAS_SPEED} cannot be part of a property chain as it has a "
            f"literal range {float}"
        )
        assert caught.value.property_iri == HAS_SPEED
        assert caught.value.class_iri == BELT
        assert (caught.value.instance_iri, caught.value.value) == (None, None)

    def test_a_property_chain_past_a_literal_names_no_class_without_one(
        self, ogm, store
    ):
        """A property specified on its own has no class above it."""
        store.triples_get.side_effect = types_of(
            {HAS_SPEED: IRI("owl:DatatypeProperty")}
        )

        with (
            patch.object(
                PropertySpec, "_resolve_effective_ranges", return_value={float}
            ),
            pytest.raises(OGMDataError) as caught,
        ):
            PropertySpec.specify(
                prop_iri=HAS_SPEED,
                nested_scope=ClassScope({HAS_COLOUR: {}}),
                ogm=ogm,
                hydration_level=ClassHydrationLevel.SCOPE,
            )

        assert caught.value.property_iri == HAS_SPEED
        assert caught.value.class_iri is None


# ---------------------------------------------------------------------------
# The ontology has a shape that the OGM cannot map: Repair
# ---------------------------------------------------------------------------


class TestOGMOntologyError:
    def test_is_an_ogm_error_and_a_value_error(self):
        assert issubclass(OGMOntologyError, OGMError)
        assert issubclass(OGMOntologyError, ValueError)

    def test_carries_the_raw_context(self):
        error = OGMOntologyError(
            "message", class_iri=BELT, property_iri=IRI(NS + "hasSpeed")
        )
        assert str(error) == "message"
        assert error.class_iri == BELT
        assert error.property_iri == IRI(NS + "hasSpeed")

    def test_an_attribute_that_does_not_apply_is_none(self):
        error = OGMOntologyError("message")
        assert (error.class_iri, error.property_iri) == (None, None)

    def test_its_docstring_names_its_action(self):
        assert "Repair" in OGMOntologyError.__doc__

    def test_a_class_without_a_type_keeps_its_message(self, ogm, store):
        store.triples_get.return_value = []

        with pytest.raises(OGMOntologyError) as caught:
            ogm.get_class_spec(class_iri=BELT)

        assert (
            str(caught.value)
            == "Class http://example.org/errors#Belt has no rdf:type defined."
        )
        assert caught.value.class_iri == BELT
        assert caught.value.property_iri is None


# ---------------------------------------------------------------------------
# The instance or the node does not exist, or the OGM cannot resolve it: Refresh
# ---------------------------------------------------------------------------


class TestOGMNotFoundError:
    def test_is_an_ogm_error_and_a_value_error(self):
        assert issubclass(OGMNotFoundError, OGMError)
        assert issubclass(OGMNotFoundError, ValueError)

    def test_carries_the_raw_context(self):
        error = OGMNotFoundError("message", iri=BELT1)
        assert str(error) == "message"
        assert error.iri == BELT1

    def test_an_attribute_that_does_not_apply_is_none(self):
        assert OGMNotFoundError("message").iri is None

    def test_its_docstring_names_its_action(self):
        assert "Refresh" in OGMNotFoundError.__doc__

    @pytest.mark.parametrize(
        "node_error",
        [
            AnonymousNodeFetchError,
            UnresolvableNodeAddressError,
            AmbiguousNodeAlignmentError,
        ],
    )
    def test_the_three_node_errors_are_not_found_errors(self, node_error):
        assert issubclass(node_error, OGMNotFoundError)
        assert node_error("message").iri is None

    def test_a_fetch_of_an_instance_without_a_class_keeps_its_message(self, ogm, store):
        with pytest.raises(OGMNotFoundError) as caught:
            ogm.fetch(instance_iri=BELT1)

        assert (
            str(caught.value)
            == "Could not determine class IRI for instance http://example.org/errors#belt1"
        )
        assert caught.value.iri == BELT1

    def test_a_commit_to_an_instance_without_a_class_names_it(self, ogm, store):
        with pytest.raises(OGMNotFoundError) as caught:
            ogm.commit(instance_iri=BELT1, data={})

        assert caught.value.iri == BELT1
        store.triples_update.assert_not_called()

    def test_a_fetch_of_an_anonymous_node_names_it(self, ogm):
        skolem_iri = mint_skolem_iri()

        with pytest.raises(AnonymousNodeFetchError) as caught:
            ogm.fetch(instance_iri=skolem_iri)

        assert caught.value.iri == skolem_iri


# ---------------------------------------------------------------------------
# The API was called incorrectly: Fix the call
# ---------------------------------------------------------------------------


class TestOGMUsageError:
    def test_is_an_ogm_error_and_a_value_error(self):
        assert issubclass(OGMUsageError, OGMError)
        assert issubclass(OGMUsageError, ValueError)

    def test_carries_its_message(self):
        assert str(OGMUsageError("message")) == "message"

    def test_its_docstring_names_its_action(self):
        assert "Fix the call" in OGMUsageError.__doc__

    def test_two_changes_that_name_one_instance_keep_their_message(self, ogm, store):
        with pytest.raises(OGMUsageError) as caught:
            ogm.commit(
                changes=[
                    Update(instance_iri=BELT1, data={}),
                    Update(instance_iri=BELT1, data={}),
                ]
            )

        assert str(caught.value) == (
            "Two changes name http://example.org/errors#belt1. "
            "Put all changes to one instance in one change."
        )
        assert not isinstance(caught.value, TypeError)
        store.triples_update.assert_not_called()


class TestOGMUsageTypeError:
    def test_is_a_usage_error_and_a_type_error(self):
        assert issubclass(OGMUsageTypeError, OGMUsageError)
        assert issubclass(OGMUsageTypeError, TypeError)

    def test_carries_its_message(self):
        assert str(OGMUsageTypeError("message")) == "message"

    def test_its_docstring_names_its_action(self):
        assert "Fix the call" in OGMUsageTypeError.__doc__

    def test_a_commit_with_neither_form_keeps_its_message(self, ogm, store):
        with pytest.raises(OGMUsageTypeError) as caught:
            ogm.commit(instance_iri=BELT1)

        assert str(caught.value) == "commit() takes instance_iri and data, or changes"
        store.triples_update.assert_not_called()

    def test_a_commit_with_both_forms_is_still_a_type_error(self, ogm):
        with pytest.raises(TypeError) as caught:
            ogm.commit(instance_iri=BELT1, data={}, changes=[])

        assert isinstance(caught.value, OGMUsageTypeError)

    def test_a_change_of_the_wrong_type_is_still_a_type_error(self, ogm):
        with pytest.raises(TypeError) as caught:
            ogm.commit(changes=[{"id": BELT1}])

        assert isinstance(caught.value, OGMUsageTypeError)
        assert (
            str(caught.value)
            == "commit() takes Create, Update and Append changes, got dict"
        )


# ---------------------------------------------------------------------------
# An error of the triple store interface reaches the caller as the same object
# ---------------------------------------------------------------------------


class TestAnInterfaceErrorPassesThrough:
    def test_from_a_create(self, ogm, store):
        refusal = TripleStoreResponseError(
            "Error while querying GraphDB (500) - <report>"
        )
        store.triples_add.side_effect = refusal

        with (
            patch.object(ogm, "get_class_spec", return_value=belt_spec()),
            pytest.raises(TripleStoreResponseError) as caught,
        ):
            ogm.create(class_iri=BELT, data={}, instance_iri=BELT1)

        assert caught.value is refusal
        assert not isinstance(caught.value, OGMError)

    def test_from_a_commit(self, ogm, store):
        refusal = TripleStoreResponseError(
            "Error while querying GraphDB (500) - <report>"
        )
        store.triples_update.side_effect = refusal

        with (
            patch.object(ogm, "get_class_spec", return_value=belt_spec()),
            pytest.raises(TripleStoreResponseError) as caught,
        ):
            ogm.commit(changes=[Create(class_iri=BELT, data={}, instance_iri=BELT1)])

        assert caught.value is refusal

    def test_from_the_read_of_a_commit(self, ogm, store):
        refusal = InvalidQueryError("malformed query")
        store.owl_get_classes_of_individual.side_effect = refusal

        with pytest.raises(InvalidQueryError) as caught:
            ogm.commit(instance_iri=BELT1, data={})

        assert caught.value is refusal

    def test_from_a_fetch(self, ogm, store):
        refusal = TripleStoreResponseError(
            "Error while querying GraphDB (401) - <report>"
        )
        store.owl_get_classes_of_individual.side_effect = refusal

        with pytest.raises(TripleStoreResponseError) as caught:
            ogm.fetch(instance_iri=BELT1)

        assert caught.value is refusal

    def test_from_a_fetch_with_a_class_spec(self, ogm, store):
        refusal = TripleStoreResponseError(
            "Error while querying GraphDB (503) - <report>"
        )
        store.triples_get.side_effect = refusal
        spec = belt_spec()
        spec.properties = {
            IRI(NS + "hasSpeed"): PropertySpec(
                iri=IRI(NS + "hasSpeed"),
                value_kind=PropertyValueKind.LITERAL,
                python_range_type=float,
            )
        }

        with pytest.raises(TripleStoreResponseError) as caught:
            ogm.fetch(instance_iri=BELT1, class_spec=spec)

        assert caught.value is refusal


# ---------------------------------------------------------------------------
# A lazy import catches only ImportError
# ---------------------------------------------------------------------------


class _UnreadableModule:
    """A module whose names cannot be read: it raises what the test gives it."""

    def __init__(self, error: Exception):
        self._error = error

    def __getattr__(self, name):
        raise self._error


def nesting_model(kind: PropertyValueKind):
    """The model of a class with one property that nests a value of ``kind``, and that value."""
    has_part = IRI(NS + "hasPart")
    if kind is PropertyValueKind.OBJECT:
        nested = ClassSpec(iri=BELT, hydration_level=ClassHydrationLevel.SCOPE)
        value = {"id": BELT1}
    else:
        nested = ClassSpec(iri=None, hydration_level=ClassHydrationLevel.FULL)
        value = {}
    spec = belt_spec()
    spec.properties = {
        has_part: PropertySpec(iri=has_part, value_kind=kind, nested=nested)
    }
    return spec.to_pydantic_model(), {"id": BELT1, has_part.lined: [value]}


NESTING_KINDS = [PropertyValueKind.OBJECT, PropertyValueKind.COMPLEX]


class TestTheLazyImport:
    """A nested value is parsed by a validator that imports ``Node`` when it runs."""

    @pytest.mark.parametrize("kind", NESTING_KINDS, ids=lambda kind: kind.name)
    def test_an_error_other_than_an_import_error_is_raised(self, kind, monkeypatch):
        model, data = nesting_model(kind)
        broken = RuntimeError("the node module is broken")
        monkeypatch.setitem(
            sys.modules, "kapps_ogm.node.core", _UnreadableModule(broken)
        )

        with pytest.raises(RuntimeError) as caught:
            model.model_validate(data)

        assert caught.value is broken

    @pytest.mark.parametrize("kind", NESTING_KINDS, ids=lambda kind: kind.name)
    def test_an_import_error_is_still_caught(self, kind, monkeypatch):
        model, data = nesting_model(kind)
        monkeypatch.setitem(
            sys.modules,
            "kapps_ogm.node.core",
            _UnreadableModule(ImportError("no node module")),
        )

        instance = model.model_validate(data)

        assert len(getattr(instance, IRI(NS + "hasPart").lined)) == 1


# ---------------------------------------------------------------------------
# No bare ValueError or TypeError remains for the four conditions
# ---------------------------------------------------------------------------

SOURCE = Path(kapps_ogm.__file__).parent

# The OGM's bare ValueErrors that are none of the four conditions. Each one is Repair, and not a
# shape of the ontology: an internal invariant or the OGM's configuration. A consumer that maps
# only typed errors answers a bare built-in as Repair. Each is named by its module and function.
NOT_ONE_OF_THE_FOUR = {
    # The value kind of a PropertySpec is one of the three members of PropertyValueKind.
    ("mapping/property_spec.py", "to_pydantic_field", "ValueError"),
    ("ogm.py", "_fetch", "ValueError"),
    ("utils/blank_instance.py", "_blank_value_for_property", "ValueError"),
    # The Skolem namespace given to the OGM, which mints its anonymous nodes' IRIs.
    ("utils/skolem.py", "validate_skolem_namespace", "ValueError"),
}


def bare_raises() -> set[tuple[str, str, str]]:
    """Every ``raise ValueError`` and ``raise TypeError`` in the package, by module and function."""
    found = set()
    for path in sorted(SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(function):
                if not isinstance(node, ast.Raise) or node.exc is None:
                    continue
                raised = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
                if isinstance(raised, ast.Name) and raised.id in {
                    "ValueError",
                    "TypeError",
                }:
                    module = path.relative_to(SOURCE).as_posix()
                    found.add((module, function.name, raised.id))
    return found


def test_no_bare_value_or_type_error_remains_for_the_four_conditions():
    assert bare_raises() == NOT_ONE_OF_THE_FOUR


def test_the_check_finds_a_bare_raise():
    """The check reads the source it is meant to read: it finds the raises it lets through."""
    assert (
        "utils/skolem.py",
        "validate_skolem_namespace",
        "ValueError",
    ) in bare_raises()
