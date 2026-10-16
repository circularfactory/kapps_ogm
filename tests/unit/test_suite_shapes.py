"""The suite's SHACL shapes come and go through the store's shapes registry.

The suite never names the graph that a store keeps its shapes in. It loads a shape with the
registry's `shapes_set`, which replaces every shape of the dataset, and it removes the shapes with
an empty document. So these tests do not read the shapes back: they check what the store does with
a write. A write that breaks a loaded shape is refused. The same write is admitted after the
removal, and after a load of another shape, which replaces the one before it: the consequence for
the shapes that other code loaded into the dataset, which the root `conftest` states.

The store refuses to load a shape that the data of the dataset already breaks. So each write that
breaks a shape and is admitted is removed again after its test, and the shapes with it.

`conftest` imports as a top-level module because `tests/` carries no `__init__.py`, so pytest
puts that directory on `sys.path`.
"""

import uuid

import pytest

from kapps_triplestore_interface import IRI, TripleStore
from kapps_triplestore_interface.exceptions import ShaclValidationError

from conftest import (
    ONE_WORKPIECE_PER_RESOURCE,
    STAND_IN_SHAPE,
    load_shape,
    remove_shapes,
)

RDF_TYPE = IRI("rdf:type")

# The classes and the properties of the two shapes in `conftest`.
SEVERAL = "https://example.org/several#"
RESOURCE = IRI(SEVERAL + "Resource")
HAS_POSSESSOR = IRI(SEVERAL + "hasPossessor")

OTHER = "https://example.org/other#"
THING = IRI(OTHER + "Thing")
LABEL = IRI(OTHER + "label")


def fresh(namespace: str, name: str) -> IRI:
    """An instance IRI no other test uses, because the live store is wiped only per session."""
    return IRI(f"{namespace}{name}_{uuid.uuid4().hex[:8]}")


def two_possessions() -> list:
    """A resource with two possession states: `ONE_WORKPIECE_PER_RESOURCE` refuses it."""
    resource = fresh(SEVERAL, "resource")
    return [
        (resource, RDF_TYPE, RESOURCE),
        (resource, HAS_POSSESSOR, fresh(SEVERAL, "state")),
        (resource, HAS_POSSESSOR, fresh(SEVERAL, "state")),
    ]


def two_labels() -> list:
    """A thing with two labels: `STAND_IN_SHAPE` refuses it."""
    thing = fresh(OTHER, "thing")
    return [
        (thing, RDF_TYPE, THING),
        (thing, LABEL, fresh(OTHER, "label")),
        (thing, LABEL, fresh(OTHER, "label")),
    ]


@pytest.fixture
def shapes(db: TripleStore):
    """Remove every shape after the test."""
    yield
    remove_shapes(db)


@pytest.fixture
def admitted(db: TripleStore):
    """The writes that the test made, removed after it."""
    writes: list = []
    yield writes
    for triples in writes:
        db.triples_delete(triples, check_exist=False)


def test_a_loaded_shape_refuses_a_write_that_breaks_it(db: TripleStore, shapes):
    load_shape(db, ONE_WORKPIECE_PER_RESOURCE)

    with pytest.raises(ShaclValidationError):
        db.triples_add(two_possessions())


def test_the_removal_admits_that_write(db: TripleStore, shapes, admitted):
    load_shape(db, ONE_WORKPIECE_PER_RESOURCE)

    remove_shapes(db)

    write = two_possessions()
    assert db.triples_add(write)
    admitted.append(write)


def test_a_load_replaces_the_shapes_before_it(db: TripleStore, shapes, admitted):
    load_shape(db, STAND_IN_SHAPE)
    with pytest.raises(ShaclValidationError):
        db.triples_add(two_labels())

    load_shape(db, ONE_WORKPIECE_PER_RESOURCE)

    write = two_labels()
    assert db.triples_add(write)
    admitted.append(write)
