"""Tests for what ``OGM.create`` and a ``Create`` of ``OGM.commit`` write, on a mocked store.

The data decides whether a nested value is a link or an instance of its own. A value given by its
IRI alone, as ``{"id": iri}`` or as an IRI, is a link: only the triple that points at it is
written, even when the class scope hydrates the linked class. A value with properties is created
with the new instance.

The write itself carries the precondition that nothing it creates exists yet: every subject it
writes about is passed as ``absent_subjects``, and the store refuses the write if one of them is
the subject of an explicit triple. A refusal reaches the caller as the store interface raised it.
"""

from unittest.mock import Mock, patch

import pytest

from kapps_triplestore_interface import GraphDB, IRI
from kapps_triplestore_interface.exceptions import (
    PreconditionFailedError,
    TripleStoreInterfaceError,
)

from kapps_ogm import Create
from kapps_ogm.mapping.class_spec import ClassHydrationLevel, ClassSpec
from kapps_ogm.mapping.property_spec import PropertySpec, PropertyValueKind
from kapps_ogm.ogm import OGM

EX = "https://example.org/ex#"
MEASUREMENT = IRI(EX + "Measurement")
WORKPIECE = IRI(EX + "Workpiece")
VALUE = IRI(EX + "value")
MEASURED_ON = IRI(EX + "measuredOn")
NAME = IRI(EX + "name")

M2 = IRI("https://example.org/i/m2")
OBJ2 = IRI("https://example.org/i/obj2")


def measurement_spec() -> ClassSpec:
    """A measurement whose ``measuredOn`` hydrates the workpiece with its ``name``."""
    workpiece = ClassSpec(
        iri=WORKPIECE,
        hydration_level=ClassHydrationLevel.FULL,
        properties={
            NAME: PropertySpec(
                iri=NAME, value_kind=PropertyValueKind.LITERAL, python_range_type=str
            )
        },
    )
    return ClassSpec(
        iri=MEASUREMENT,
        hydration_level=ClassHydrationLevel.FULL,
        properties={
            VALUE: PropertySpec(
                iri=VALUE, value_kind=PropertyValueKind.LITERAL, python_range_type=float
            ),
            MEASURED_ON: PropertySpec(
                iri=MEASURED_ON, value_kind=PropertyValueKind.OBJECT, nested=workpiece
            ),
        },
    )


@pytest.fixture
def store() -> Mock:
    db = Mock(spec=GraphDB)
    db.iri_exists.return_value = False
    return db


@pytest.fixture
def ogm(store: Mock) -> OGM:
    ogm = OGM(db=store)
    with patch.object(
        ogm, "get_class_spec", side_effect=lambda **_: measurement_spec()
    ):
        yield ogm


def measurement(on) -> dict:
    return {str(VALUE): [1.5], str(MEASURED_ON): [on]}


def subjects(triples) -> set:
    return {subject for subject, _, _ in triples}


LINKS = {
    "id only": {"id": str(OBJ2)},
    "IRI": OBJ2,
}


class TestCreate:
    @pytest.mark.parametrize("link", LINKS.values(), ids=LINKS.keys())
    def test_a_link_writes_only_the_triple_that_points_at_it(self, ogm, store, link):
        ogm.create(class_iri=MEASUREMENT, data=measurement(link), instance_iri=M2)

        triples = store.triples_add.call_args.args[0]
        assert subjects(triples) == {M2}
        assert (M2, MEASURED_ON, OBJ2) in triples

    @pytest.mark.parametrize("link", LINKS.values(), ids=LINKS.keys())
    def test_a_link_is_not_an_absent_subject(self, ogm, store, link):
        ogm.create(class_iri=MEASUREMENT, data=measurement(link), instance_iri=M2)

        assert store.triples_add.call_args.kwargs["absent_subjects"] == [M2]

    def test_a_nested_instance_with_properties_is_created(self, ogm, store):
        owned = {"id": str(OBJ2), str(NAME): ["Housing 2"]}
        ogm.create(class_iri=MEASUREMENT, data=measurement(owned), instance_iri=M2)

        triples = store.triples_add.call_args.args[0]
        assert subjects(triples) == {M2, OBJ2}
        assert (OBJ2, IRI("rdf:type"), WORKPIECE) in {
            (s, IRI(p), o) for s, p, o in triples
        }
        assert set(store.triples_add.call_args.kwargs["absent_subjects"]) == {M2, OBJ2}

    def test_the_new_instance_is_an_absent_subject(self, ogm, store):
        ogm.create(class_iri=MEASUREMENT, data={str(VALUE): [1.5]}, instance_iri=M2)

        assert store.triples_add.call_args.kwargs["absent_subjects"] == [M2]

    def test_the_named_graph_goes_with_the_precondition(self, ogm, store):
        graph = IRI("https://example.org/graph")
        ogm.create(
            class_iri=MEASUREMENT,
            data={str(VALUE): [1.5]},
            instance_iri=M2,
            named_graph=graph,
        )

        assert store.triples_add.call_args.kwargs["named_graph"] == graph


class TestACreateOfCommit:
    @pytest.mark.parametrize("link", LINKS.values(), ids=LINKS.keys())
    def test_a_link_writes_only_the_triple_that_points_at_it(self, ogm, store, link):
        ogm.commit(
            changes=[
                Create(class_iri=MEASUREMENT, data=measurement(link), instance_iri=M2)
            ]
        )

        kwargs = store.triples_update.call_args.kwargs
        assert subjects(kwargs["new_triples"]) == {M2}
        assert kwargs["absent_subjects"] == [M2]

    def test_every_instance_it_creates_is_an_absent_subject(self, ogm, store):
        other = IRI("https://example.org/i/m3")
        owned = {"id": str(OBJ2), str(NAME): ["Housing 2"]}
        ogm.commit(
            changes=[
                Create(class_iri=MEASUREMENT, data=measurement(owned), instance_iri=M2),
                Create(
                    class_iri=MEASUREMENT, data={str(VALUE): [2.0]}, instance_iri=other
                ),
            ]
        )

        kwargs = store.triples_update.call_args.kwargs
        assert set(kwargs["absent_subjects"]) == {M2, OBJ2, other}

    def test_the_existence_is_not_read_before_the_write(self, ogm, store):
        ogm.commit(
            changes=[
                Create(class_iri=MEASUREMENT, data={str(VALUE): [1.5]}, instance_iri=M2)
            ]
        )

        store.iri_exists.assert_called_once_with(
            M2, as_sub=True, as_pred=True, as_obj=True
        )


class RefusalOfASubclass(PreconditionFailedError):
    """A subclass of ``PreconditionFailedError``. It proves that a subclass reaches the caller
    with its own type."""


@pytest.fixture(
    params=[PreconditionFailedError, RefusalOfASubclass],
    ids=lambda cls: cls.__name__,
)
def precondition_failed(request):
    """A refusal of the interface: the store found the precondition of the write false."""
    return request.param(
        "precondition false, nothing written", triples=[], named_graph=None
    )


class TestARefusedPrecondition:
    def test_propagates_unchanged_from_create(self, ogm, store, precondition_failed):
        store.triples_add.side_effect = precondition_failed

        with pytest.raises(TripleStoreInterfaceError) as caught:
            ogm.create(class_iri=MEASUREMENT, data={str(VALUE): [1.5]}, instance_iri=M2)

        assert caught.value is precondition_failed
        assert caught.value.__cause__ is None

    def test_propagates_unchanged_from_a_commit_of_changes(
        self, ogm, store, precondition_failed
    ):
        store.triples_update.side_effect = precondition_failed

        with pytest.raises(TripleStoreInterfaceError) as caught:
            ogm.commit(
                changes=[
                    Create(
                        class_iri=MEASUREMENT, data={str(VALUE): [1.5]}, instance_iri=M2
                    )
                ]
            )

        assert caught.value is precondition_failed
        assert caught.value.__cause__ is None

    def test_propagates_unchanged_from_a_commit_of_one_instance(
        self, ogm, store, precondition_failed
    ):
        store.owl_get_classes_of_individual.return_value = {MEASUREMENT}
        store.triples_get.side_effect = lambda sub, pred, **_: (
            [(M2, VALUE, 1.25)] if pred == VALUE else []
        )
        store.triples_update.side_effect = precondition_failed

        with pytest.raises(TripleStoreInterfaceError) as caught:
            ogm.commit(instance_iri=M2, data={str(VALUE): [1.5]})

        assert caught.value is precondition_failed
        assert caught.value.__cause__ is None
