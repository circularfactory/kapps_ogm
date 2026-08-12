"""Tests for anonymous node addressing via Skolem IRIs.

Anonymous nodes (behind COMPLEX properties) must receive stable Skolem IRIs
rather than transient blank nodes, ensuring commit operations are true no-ops
when data is unchanged.
"""

from typing import Set, Tuple

import pytest
from rdflib import BNode

from kapps_triplestore_interface import IRI, to_literal
from kapps_ogm.node.core import Node
from kapps_ogm.mapping.class_spec import ClassSpec, ClassHydrationLevel
from kapps_ogm.mapping.property_spec import PropertySpec, PropertyValueKind
from kapps_ogm.utils.skolem import is_skolem_iri
from kapps_ogm.utils.errors import UnresolvableNodeAddressError
from kapps_ogm.ogm import OGM


ONTO = "https://www.sfb1574.kit.edu/ontologies/TransferUnit"
CONVEYOR_BELT = IRI("ConveyorBelt", ONTO)
HAS_SPEED = IRI("hasConveyorSpeed", ONTO)
HAS_UNIT = IRI("hasUnit", ONTO)
HAS_VALUE = IRI("hasValue", ONTO)
BELT_IRI = IRI("https://example.org/instances/belt1")
SKOLEM_IRI = IRI("https://w3id.org/circularfactory/.well-known/genid/ab12")


def _build_param_spec() -> ClassSpec:
    """Build the anonymous parameter ClassSpec (for conveyor speed)."""
    return ClassSpec(
        iri=None,
        types=[],
        hydration_level=ClassHydrationLevel.FULL,
        properties={
            HAS_UNIT: PropertySpec(
                iri=HAS_UNIT, value_kind=PropertyValueKind.LITERAL, python_range_type=str
            ),
            HAS_VALUE: PropertySpec(
                iri=HAS_VALUE, value_kind=PropertyValueKind.LITERAL, python_range_type=float
            ),
        },
    )


def _build_belt_spec(param_spec: ClassSpec) -> ClassSpec:
    """Build the ConveyorBelt ClassSpec with a COMPLEX hasConveyorSpeed property."""
    return ClassSpec(
        iri=CONVEYOR_BELT,
        types=[],
        hydration_level=ClassHydrationLevel.FULL,
        properties={
            HAS_SPEED: PropertySpec(
                iri=HAS_SPEED, value_kind=PropertyValueKind.COMPLEX, nested=param_spec
            )
        },
    )


@pytest.fixture
def param_spec() -> ClassSpec:
    """Fixture for the anonymous parameter ClassSpec."""
    return _build_param_spec()


@pytest.fixture
def belt_spec(param_spec: ClassSpec) -> ClassSpec:
    """Fixture for the ConveyorBelt ClassSpec."""
    return _build_belt_spec(param_spec)


def _extract_anonymous_address(triples: Set[Tuple]) -> IRI:
    """Extract the object of the (BELT_IRI, HAS_SPEED, ?) triple."""
    for s, p, o in triples:
        if s == BELT_IRI and p == HAS_SPEED:
            return o
    raise ValueError(f"No triple found for ({BELT_IRI}, {HAS_SPEED}, ?)")


class TestMintingNewAnonymousNode:
    """Tests for minting a Skolem IRI when no address exists."""

    def test_mints_skolem_iri_when_no_address(self, belt_spec: ClassSpec, ogm_with_mock_db: OGM):
        """Serialising a node whose parameter has no address mints a Skolem IRI."""
        node = Node(
            id=BELT_IRI,
            class_spec=belt_spec,
            ogm=ogm_with_mock_db,
            data={HAS_SPEED: [{HAS_UNIT: ["m/s"], HAS_VALUE: [1.4]}]},
        )
        node.materialize()
        triples = node.to_triples()

        address = _extract_anonymous_address(triples)
        assert is_skolem_iri(address)

    def test_no_bnode_in_emitted_triples(self, belt_spec: ClassSpec, ogm_with_mock_db: OGM):
        """No rdflib.BNode appears anywhere in the emitted triples."""
        node = Node(
            id=BELT_IRI,
            class_spec=belt_spec,
            ogm=ogm_with_mock_db,
            data={HAS_SPEED: [{HAS_UNIT: ["m/s"], HAS_VALUE: [1.4]}]},
        )
        node.materialize()
        triples = node.to_triples()

        for s, p, o in triples:
            assert not isinstance(s, BNode), f"Subject {s} is a BNode"
            assert not isinstance(p, BNode), f"Predicate {p} is a BNode"
            assert not isinstance(o, BNode), f"Object {o} is a BNode"

    def test_parameter_properties_emitted_on_address(self, belt_spec: ClassSpec, ogm_with_mock_db: OGM):
        """The parameter's declared properties are emitted on the minted address."""
        node = Node(
            id=BELT_IRI,
            class_spec=belt_spec,
            ogm=ogm_with_mock_db,
            data={HAS_SPEED: [{HAS_UNIT: ["m/s"], HAS_VALUE: [1.4]}]},
        )
        node.materialize()
        triples = node.to_triples()

        address = _extract_anonymous_address(triples)

        assert (address, HAS_UNIT, to_literal("m/s")) in triples
        assert (address, HAS_VALUE, to_literal(1.4)) in triples

    def test_no_type_triple_for_skolem_iri(self, belt_spec: ClassSpec, ogm_with_mock_db: OGM):
        """No rdf:type or other triple is asserted about the Skolem IRI beyond its properties."""
        node = Node(
            id=BELT_IRI,
            class_spec=belt_spec,
            ogm=ogm_with_mock_db,
            data={HAS_SPEED: [{HAS_UNIT: ["m/s"], HAS_VALUE: [1.4]}]},
        )
        node.materialize()
        triples = node.to_triples()

        address = _extract_anonymous_address(triples)

        triples_about_address = [t for t in triples if t[0] == address]
        assert len(triples_about_address) == 2

        for s, p, o in triples_about_address:
            assert p != "rdf:type", f"Unexpected rdf:type triple: {(s, p, o)}"

    def test_named_belt_gets_type_triples(self, belt_spec: ClassSpec, ogm_with_mock_db: OGM):
        """The belt itself, being a named class, still gets its rdf:type triples."""
        node = Node(
            id=BELT_IRI,
            class_spec=belt_spec,
            ogm=ogm_with_mock_db,
            data={HAS_SPEED: [{HAS_UNIT: ["m/s"], HAS_VALUE: [1.4]}]},
        )
        node.materialize()
        triples = node.to_triples()

        assert (BELT_IRI, "rdf:type", CONVEYOR_BELT) in triples
        assert (BELT_IRI, "rdf:type", "owl:NamedIndividual") in triples

    def test_ogm_namespace_used_for_minted_address(self, belt_spec: ClassSpec, mock_db):
        """The OGM's configured skolem_namespace is used for the minted address."""
        custom_namespace = "https://example.org/.well-known/genid/"
        ogm = OGM(db=mock_db, skolem_namespace=custom_namespace)

        node = Node(
            id=BELT_IRI,
            class_spec=belt_spec,
            ogm=ogm,
            data={HAS_SPEED: [{HAS_UNIT: ["m/s"], HAS_VALUE: [1.4]}]},
        )
        node.materialize()
        triples = node.to_triples()

        address = _extract_anonymous_address(triples)
        assert str(address).startswith(custom_namespace)


class TestResolvingExistingAddress:
    """Tests for resolving an existing address without re-minting."""

    def test_uses_address_from_data(self, belt_spec: ClassSpec, ogm_with_mock_db: OGM):
        """When data carries the address, emitted triples use that address."""
        node = Node(
            id=BELT_IRI,
            class_spec=belt_spec,
            ogm=ogm_with_mock_db,
            data={HAS_SPEED: [{"id": SKOLEM_IRI, HAS_UNIT: ["m/s"]}]},
        )
        node.materialize()
        triples = node.to_triples()

        address = _extract_anonymous_address(triples)
        assert address == SKOLEM_IRI

    def test_stability_across_multiple_calls(self, belt_spec: ClassSpec, ogm_with_mock_db: OGM):
        """Calling to_triples() twice on the same materialised node returns identical triples."""
        node = Node(
            id=BELT_IRI,
            class_spec=belt_spec,
            ogm=ogm_with_mock_db,
            data={HAS_SPEED: [{"id": SKOLEM_IRI, HAS_UNIT: ["m/s"], HAS_VALUE: [1.4]}]},
        )
        node.materialize()

        triples_first = node.to_triples()
        triples_second = node.to_triples()

        assert triples_first == triples_second

    def test_same_address_across_separate_nodes(self, belt_spec: ClassSpec, ogm_with_mock_db: OGM):
        """Two separate Node objects over the same data dict produce the same address."""
        shared_data = {HAS_SPEED: [{"id": SKOLEM_IRI, HAS_UNIT: ["m/s"], HAS_VALUE: [1.4]}]}

        node1 = Node(id=BELT_IRI, class_spec=belt_spec, ogm=ogm_with_mock_db, data=shared_data)
        node2 = Node(id=BELT_IRI, class_spec=belt_spec, ogm=ogm_with_mock_db, data=shared_data)

        node1.materialize()
        node2.materialize()

        triples1 = node1.to_triples()
        triples2 = node2.to_triples()

        assert triples1 == triples2


class TestJsonLdProjection:
    """R4: the address must not leak northbound, and JSON-LD is a northbound projection."""

    def _belt_json_ld(self, belt_spec: ClassSpec, ogm: OGM) -> dict:
        node = Node(
            id=BELT_IRI,
            class_spec=belt_spec,
            ogm=ogm,
            data={HAS_SPEED: [{HAS_UNIT: ["m/s"], HAS_VALUE: [1.4]}]},
        )
        node.materialize()
        return node.to_json_ld()

    def test_the_anonymous_node_is_still_inlined(self, belt_spec, ogm_with_mock_db):
        """A Skolem IRI stands in for a blank node, so it inlines exactly as one did."""
        json_ld = self._belt_json_ld(belt_spec, ogm_with_mock_db)

        assert len(json_ld["@graph"]) == 1
        speed = json_ld["@graph"][0][HAS_SPEED.short]
        assert speed[HAS_UNIT.short] == "m/s"

    def test_the_address_never_appears_in_the_json_ld(self, belt_spec, ogm_with_mock_db):
        json_ld = self._belt_json_ld(belt_spec, ogm_with_mock_db)

        assert "/.well-known/genid/" not in str(json_ld)


class TestUnresolvableAddressRaises:
    """Tests that unresolvable addresses raise rather than minting."""

    def test_raises_when_data_none_and_no_node_iri(self, belt_spec: ClassSpec, ogm_with_mock_db: OGM):
        """A Node from materialised instance with data=None and _node_iri=None raises UnresolvableNodeAddressError."""
        model_cls = belt_spec.to_pydantic_model()
        instance = model_cls.model_validate(
            {"id": BELT_IRI, HAS_SPEED.lined: [{HAS_UNIT.lined: ["m/s"]}]}
        )

        node = Node(
            id=BELT_IRI,
            class_spec=belt_spec,
            instance=instance,
            ogm=ogm_with_mock_db,
        )
        # Ensure data is None to simulate the unresolvable case
        node.data = None

        with pytest.raises(UnresolvableNodeAddressError):
            node.to_triples()

    def test_succeeds_when_node_iri_set_on_nested_model(self, belt_spec: ClassSpec, ogm_with_mock_db: OGM):
        """Setting _node_iri on the nested model allows resolution via the mirror fallback."""
        model_cls = belt_spec.to_pydantic_model()
        instance = model_cls.model_validate(
            {"id": BELT_IRI, HAS_SPEED.lined: [{HAS_UNIT.lined: ["m/s"]}]}
        )

        # Set the _node_iri attribute on the nested anonymous model
        nested_list = getattr(instance, HAS_SPEED.lined)
        nested_list[0]._node_iri = SKOLEM_IRI

        node = Node(
            id=BELT_IRI,
            class_spec=belt_spec,
            instance=instance,
            ogm=ogm_with_mock_db,
        )
        node.data = None

        triples = node.to_triples()
        address = _extract_anonymous_address(triples)

        assert address == SKOLEM_IRI
