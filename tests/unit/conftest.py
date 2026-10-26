"""Shared fixtures for unit tests."""

import os
import sys
import pytest
from unittest.mock import Mock

from kapps_triplestore_interface import IRI, TripleStore
from kapps_triplestore_interface.exceptions import TripleStoreResponseError
from kapps_ogm.ogm import OGM
from kapps_ogm.mapping.class_spec import ClassSpec
from kapps_ogm.node.core import Node


# Test data constants
TRANSFER_UNIT_IRI = IRI(
    "http://www.sfb1574.kit.edu/ontologies/TransferUnit#TransferUnit"
)
INSTANCE_IRI = IRI("http://example.org/instances/transfer_unit_001")
RDF_TYPE = IRI("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")

PROPERTY_CHAINS = [
    [IRI("http://www.sfb1574.kit.edu/ontologies/TransferUnit#hasConveyorBelt")],
    [IRI("http://www.sfb1574.kit.edu/ontologies/TransferUnit#hasLightBarrier")],
]

HAS_CONVEYOR_BELT = IRI(
    "http://www.sfb1574.kit.edu/ontologies/TransferUnit#hasConveyorBelt"
)
HAS_CONVEYOR_SPEED = IRI(
    "http://www.sfb1574.kit.edu/ontologies/TransferUnit#hasConveyorSpeed"
)
HAS_VALUE = IRI("http://www.sfb1574.kit.edu/ontologies/CrcInterfaces#hasValue")
HAS_UNIT = IRI("http://www.sfb1574.kit.edu/ontologies/TransferUnit#hasUnit")

MOCK_INSTANCE_DATA = {
    HAS_CONVEYOR_BELT.lined: [
        {
            HAS_CONVEYOR_SPEED.lined: [
                {
                    HAS_VALUE.lined: [1.5],
                    HAS_UNIT.lined: ["m/s"],
                }
            ]
        }
    ]
}


@pytest.fixture
def mock_db():
    """A mock store with the spec of the contract, `TripleStore`."""
    db = Mock(spec=TripleStore)
    db.triples_get = Mock(return_value=[])
    db.iri_exists = Mock(return_value=False)
    db.owl_get_classes_of_individual = Mock(return_value=[])
    db.new_iri = lambda base, schema: schema(base)

    return db


class StoreRefusal(TripleStoreResponseError):
    """A subclass of `TripleStoreResponseError`. It proves that a subclass reaches the caller with its own type."""


@pytest.fixture(
    params=[TripleStoreResponseError, StoreRefusal], ids=lambda cls: cls.__name__
)
def store_error(request):
    """An error the triple store raises on a write, carrying a report in its message."""
    return request.param("Error while querying GraphDB (500) - <validation report>")


@pytest.fixture
def ogm_with_mock_db(mock_db):
    """Create an OGM instance with a mocked database."""
    ogm = OGM(db=mock_db)
    return ogm


@pytest.fixture
def simple_class_spec() -> ClassSpec:
    """Create a simple ClassSpec for testing."""
    # Create a minimal mock ClassSpec without database queries
    class_spec = Mock(spec=ClassSpec)
    class_spec.iri = TRANSFER_UNIT_IRI
    class_spec.properties = {}
    return class_spec


@pytest.fixture
def mock_node(ogm_with_mock_db: OGM, simple_class_spec: ClassSpec) -> Node:
    """Create a mock Node without loading data."""
    node = Node(
        id=INSTANCE_IRI,
        class_spec=simple_class_spec,
        ogm=ogm_with_mock_db,
    )
    return node
