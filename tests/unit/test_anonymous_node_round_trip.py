"""Unit tests for the anonymous-node fetch/commit round trip.

A conveyor belt's speed parameter is an anonymous node carrying declared properties (value, unit)
AND undeclared ones the range restriction does not mention (MQTT topic, broker IP). A fetch then
commit must leave that node where it is, so the undeclared triples are never orphaned.

Three seams:
    1. ``OGM._fetch_complex_property`` — identity survives the read (PRD R1).
    2. ``reconcile_anonymous_addresses`` — addresses transfer from the fetched node to the
       outgoing one, which is what makes a round trip through ``model_dump()`` work.
    3. ``Node.diff`` — no change is a no-op; a changed value diffs per triple, naming the IRI.
"""

from rdflib import BNode

import pytest

from kapps_triplestore_interface import IRI, to_literal

from kapps_ogm.mapping.class_spec import ClassHydrationLevel, ClassSpec
from kapps_ogm.mapping.property_spec import PropertySpec, PropertyValueKind
from kapps_ogm.node.core import Node
from kapps_ogm.node.node_address import reconcile_anonymous_addresses
from kapps_ogm.utils.errors import AmbiguousNodeAlignmentError

ONTO = "https://www.sfb1574.kit.edu/ontologies/TransferUnit"
CONVEYOR_BELT = IRI("ConveyorBelt", ONTO)
HAS_SPEED = IRI("hasConveyorSpeed", ONTO)
HAS_UNIT = IRI("hasUnit", ONTO)
HAS_VALUE = IRI("hasValue", ONTO)
HAS_MQTT_TOPIC = IRI("hasMQTTTopic", ONTO)  # deliberately undeclared by the range restriction

BELT_IRI = IRI("https://example.org/instances/belt1")
SKOLEM_IRI = IRI("https://w3id.org/circularfactory/.well-known/genid/ab12")
OTHER_SKOLEM_IRI = IRI("https://w3id.org/circularfactory/.well-known/genid/cd34")


def build_belt_spec() -> ClassSpec:
    """The belt, whose speed is an anonymous node declaring only value and unit."""
    param_spec = ClassSpec(
        iri=None,
        types=[],
        hydration_level=ClassHydrationLevel.FULL,
        properties={
            HAS_UNIT: PropertySpec(
                iri=HAS_UNIT,
                value_kind=PropertyValueKind.LITERAL,
                python_range_type=str,
            ),
            HAS_VALUE: PropertySpec(
                iri=HAS_VALUE,
                value_kind=PropertyValueKind.LITERAL,
                python_range_type=float,
            ),
        },
    )
    return ClassSpec(
        iri=CONVEYOR_BELT,
        types=[],
        hydration_level=ClassHydrationLevel.FULL,
        properties={
            HAS_SPEED: PropertySpec(
                iri=HAS_SPEED,
                value_kind=PropertyValueKind.COMPLEX,
                nested=param_spec,
            )
        },
    )


@pytest.fixture
def belt_spec() -> ClassSpec:
    return build_belt_spec()


def query_result(node_data_map: dict) -> dict:
    """Build what ``db.query(..., convert_bindings=True)`` returns.

    Bindings are already converted to Python objects at this boundary, so identifiers arrive as
    ``IRI`` or ``BNode`` instances rather than as raw SPARQL JSON.
    """
    bindings = []
    for node_id, properties in node_data_map.items():
        for prop_iri, values in properties.items():
            for value in values:
                bindings.append(
                    {"bnode": node_id, "property": prop_iri, "value": value}
                )
    return {"results": {"bindings": bindings}}


def belt_node(ogm, spec: ClassSpec, parameter: dict) -> Node:
    """A materialized belt whose speed is the given parameter payload."""
    node = Node(
        id=BELT_IRI,
        class_spec=spec,
        data={HAS_SPEED: [parameter]},
        ogm=ogm,
    )
    node.materialize()
    return node


class TestIdentityAtRead:
    """Seam 1: the identifier the query already has must not be thrown away."""

    def test_each_node_carries_its_own_identifier(self, ogm_with_mock_db, mock_db):
        mock_db.query.return_value = query_result(
            {SKOLEM_IRI: {HAS_VALUE: [1.5], HAS_UNIT: ["m/s"]}}
        )

        result = ogm_with_mock_db._fetch_complex_property(BELT_IRI, HAS_SPEED)

        assert len(result) == 1
        assert result[0]["id"] == SKOLEM_IRI

    def test_undeclared_properties_survive_the_read(self, ogm_with_mock_db, mock_db):
        """The query is `?bnode ?property ?value` — unfiltered — so everything comes back."""
        mock_db.query.return_value = query_result(
            {
                SKOLEM_IRI: {
                    HAS_VALUE: [1.5],
                    HAS_UNIT: ["m/s"],
                    HAS_MQTT_TOPIC: ["TransferUnit1/ConveyorBelt/left/speed"],
                }
            }
        )

        result = ogm_with_mock_db._fetch_complex_property(BELT_IRI, HAS_SPEED)

        assert result[0][HAS_MQTT_TOPIC] == ["TransferUnit1/ConveyorBelt/left/speed"]

    def test_groups_are_ordered_deterministically(self, ogm_with_mock_db, mock_db):
        """Two fetches of unchanged data must align positionally, so order cannot be arbitrary."""
        mock_db.query.return_value = query_result(
            {
                BNode("z999"): {HAS_VALUE: [3.0]},
                BNode("a001"): {HAS_VALUE: [1.0]},
            }
        )

        result = ogm_with_mock_db._fetch_complex_property(BELT_IRI, HAS_SPEED)

        assert [str(group["id"]) for group in result] == ["a001", "z999"]

    def test_a_blank_node_identifier_stays_a_blank_node(self, ogm_with_mock_db, mock_db):
        mock_db.query.return_value = query_result({BNode("abc123"): {HAS_VALUE: [2.5]}})

        result = ogm_with_mock_db._fetch_complex_property(BELT_IRI, HAS_SPEED)

        assert isinstance(result[0]["id"], BNode)
        assert str(result[0]["id"]) == "abc123"

    def test_no_results_is_still_an_empty_list(self, ogm_with_mock_db, mock_db):
        mock_db.query.return_value = {"results": {"bindings": []}}

        assert ogm_with_mock_db._fetch_complex_property(BELT_IRI, HAS_SPEED) == []

    def test_the_identifier_reaches_the_nested_node(self, ogm_with_mock_db, mock_db):
        """End to end: the address must land on Node.data, the authoritative carrier."""
        mock_db.query.return_value = query_result({SKOLEM_IRI: {HAS_UNIT: ["m/s"]}})
        fetched = ogm_with_mock_db._fetch_complex_property(BELT_IRI, HAS_SPEED)

        node = Node(
            id=BELT_IRI,
            class_spec=build_belt_spec(),
            data={HAS_SPEED: fetched},
            ogm=ogm_with_mock_db,
        )

        assert node.data[HAS_SPEED][0].id == SKOLEM_IRI


class TestAddressReconciliation:
    """Seam 2: recovering the address the caller's payload could not carry."""

    def make_pair(self, ogm, old_parameter: dict, new_parameter: dict):
        old = Node(id=BELT_IRI, data={HAS_SPEED: [old_parameter]}, ogm=ogm)
        new = Node(id=BELT_IRI, data={HAS_SPEED: [new_parameter]}, ogm=ogm)
        return old, new

    def test_an_unaddressed_node_adopts_the_fetched_address(self, ogm_with_mock_db):
        old, new = self.make_pair(
            ogm_with_mock_db,
            {"id": SKOLEM_IRI, HAS_VALUE: [1.5]},
            {HAS_VALUE: [1.4]},
        )

        reconcile_anonymous_addresses(old=old, new=new)

        assert new.data[HAS_SPEED][0].id == SKOLEM_IRI

    def test_an_address_the_caller_supplied_is_never_overwritten(self, ogm_with_mock_db):
        old, new = self.make_pair(
            ogm_with_mock_db,
            {"id": SKOLEM_IRI, HAS_VALUE: [1.5]},
            {"id": OTHER_SKOLEM_IRI, HAS_VALUE: [1.4]},
        )

        reconcile_anonymous_addresses(old=old, new=new)

        assert new.data[HAS_SPEED][0].id == OTHER_SKOLEM_IRI

    def test_a_blank_node_address_is_deliberately_not_copied(self, ogm_with_mock_db):
        """Leaving it unaddressed lets _assign_id mint a Skolem IRI, relocating it once."""
        old, new = self.make_pair(
            ogm_with_mock_db,
            {"id": BNode("legacy123"), HAS_VALUE: [1.5]},
            {HAS_VALUE: [1.4]},
        )

        reconcile_anonymous_addresses(old=old, new=new)

        assert new.data[HAS_SPEED][0].id is None

    def test_reconciliation_recurses(self, ogm_with_mock_db):
        old, new = self.make_pair(
            ogm_with_mock_db,
            {"id": SKOLEM_IRI, HAS_SPEED: [{"id": OTHER_SKOLEM_IRI, HAS_VALUE: [1.5]}]},
            {HAS_SPEED: [{HAS_VALUE: [1.4]}]},
        )

        reconcile_anonymous_addresses(old=old, new=new)

        outer = new.data[HAS_SPEED][0]
        assert outer.id == SKOLEM_IRI
        assert outer.data[HAS_SPEED][0].id == OTHER_SKOLEM_IRI

    def test_extra_values_on_the_outgoing_side_stay_unaddressed(self, ogm_with_mock_db):
        """A genuinely new parameter has nothing to align with, and must be minted, not matched."""
        old = Node(
            id=BELT_IRI,
            data={HAS_SPEED: [{"id": SKOLEM_IRI, HAS_VALUE: [1.5]}]},
            ogm=ogm_with_mock_db,
        )
        new = Node(
            id=BELT_IRI,
            data={HAS_SPEED: [{HAS_VALUE: [1.4]}, {HAS_VALUE: [9.9]}]},
            ogm=ogm_with_mock_db,
        )

        reconcile_anonymous_addresses(old=old, new=new)

        assert new.data[HAS_SPEED][0].id == SKOLEM_IRI
        assert new.data[HAS_SPEED][1].id is None

    def test_dropping_one_of_several_values_raises_rather_than_shifting(
        self, ogm_with_mock_db
    ):
        """Alignment is positional, so a shortened list would silently misassign addresses.

        Shifting the second node's address onto the first does not merely lose an address — it
        moves one parameter's properties onto another parameter's node. That is worse than the
        failure R3 forbids, so it fails loudly instead.
        """
        old = Node(
            id=BELT_IRI,
            data={
                HAS_SPEED: [
                    {"id": SKOLEM_IRI, HAS_VALUE: [1.5]},
                    {"id": OTHER_SKOLEM_IRI, HAS_VALUE: [2.5]},
                ]
            },
            ogm=ogm_with_mock_db,
        )
        new = Node(
            id=BELT_IRI, data={HAS_SPEED: [{HAS_VALUE: [2.5]}]}, ogm=ogm_with_mock_db
        )

        with pytest.raises(AmbiguousNodeAlignmentError, match="hasConveyorSpeed"):
            reconcile_anonymous_addresses(old=old, new=new)

    def test_clearing_a_single_valued_property_is_unambiguous(self, ogm_with_mock_db):
        """Nothing is left to align, so there is nothing to misassign."""
        old = Node(
            id=BELT_IRI,
            data={HAS_SPEED: [{"id": SKOLEM_IRI, HAS_VALUE: [1.5]}]},
            ogm=ogm_with_mock_db,
        )
        new = Node(id=BELT_IRI, data={HAS_SPEED: []}, ogm=ogm_with_mock_db)

        reconcile_anonymous_addresses(old=old, new=new)

        assert new.data[HAS_SPEED] == []

    def test_appending_a_value_is_unambiguous(self, ogm_with_mock_db):
        """A longer list still aligns on its prefix; the extra is genuinely new."""
        old = Node(
            id=BELT_IRI,
            data={HAS_SPEED: [{"id": SKOLEM_IRI, HAS_VALUE: [1.5]}]},
            ogm=ogm_with_mock_db,
        )
        new = Node(
            id=BELT_IRI,
            data={HAS_SPEED: [{HAS_VALUE: [1.5]}, {HAS_VALUE: [9.9]}]},
            ogm=ogm_with_mock_db,
        )

        reconcile_anonymous_addresses(old=old, new=new)

        assert new.data[HAS_SPEED][0].id == SKOLEM_IRI
        assert new.data[HAS_SPEED][1].id is None

    def test_a_property_missing_on_either_side_is_skipped(self, ogm_with_mock_db):
        old = Node(
            id=BELT_IRI,
            data={HAS_SPEED: [{"id": SKOLEM_IRI, HAS_VALUE: [1.5]}]},
            ogm=ogm_with_mock_db,
        )
        new = Node(id=BELT_IRI, data={}, ogm=ogm_with_mock_db)

        reconcile_anonymous_addresses(old=old, new=new)

        assert new.data == {}

    def test_absent_data_is_a_no_op(self, ogm_with_mock_db):
        old = Node(id=BELT_IRI, data=None, ogm=ogm_with_mock_db)
        new = Node(
            id=BELT_IRI, data={HAS_SPEED: [{HAS_VALUE: [1.4]}]}, ogm=ogm_with_mock_db
        )

        reconcile_anonymous_addresses(old=old, new=new)

        assert new.data[HAS_SPEED][0].id is None


class TestRoundTrip:
    """Seam 3: what the store is actually asked to change."""

    def test_an_unchanged_commit_is_a_no_op(self, ogm_with_mock_db, belt_spec):
        old = belt_node(
            ogm_with_mock_db,
            belt_spec,
            {"id": SKOLEM_IRI, HAS_VALUE: [1.5], HAS_UNIT: ["m/s"]},
        )
        new = belt_node(
            ogm_with_mock_db,
            build_belt_spec(),
            {"id": SKOLEM_IRI, HAS_VALUE: [1.5], HAS_UNIT: ["m/s"]},
        )

        assert old.diff(other=new) == (set(), set())

    def test_a_changed_value_diffs_one_triple_naming_the_node(
        self, ogm_with_mock_db, belt_spec
    ):
        old = belt_node(
            ogm_with_mock_db,
            belt_spec,
            {"id": SKOLEM_IRI, HAS_VALUE: [12.1], HAS_UNIT: ["m/s"]},
        )
        new = belt_node(
            ogm_with_mock_db,
            build_belt_spec(),
            {"id": SKOLEM_IRI, HAS_VALUE: [1.4], HAS_UNIT: ["m/s"]},
        )

        removed, added = old.diff(other=new)

        assert removed == {(SKOLEM_IRI, HAS_VALUE, to_literal(12.1))}
        assert added == {(SKOLEM_IRI, HAS_VALUE, to_literal(1.4))}

    def test_the_unchanged_property_is_on_neither_side(
        self, ogm_with_mock_db, belt_spec
    ):
        old = belt_node(
            ogm_with_mock_db,
            belt_spec,
            {"id": SKOLEM_IRI, HAS_VALUE: [12.1], HAS_UNIT: ["m/s"]},
        )
        new = belt_node(
            ogm_with_mock_db,
            build_belt_spec(),
            {"id": SKOLEM_IRI, HAS_VALUE: [1.4], HAS_UNIT: ["m/s"]},
        )

        removed, added = old.diff(other=new)

        assert all(triple[1] != HAS_UNIT for triple in removed | added)

    def test_the_link_from_the_belt_is_never_touched(self, ogm_with_mock_db, belt_spec):
        """The DELETE must not unlink the parameter — that is what orphans it."""
        old = belt_node(
            ogm_with_mock_db, belt_spec, {"id": SKOLEM_IRI, HAS_VALUE: [12.1]}
        )
        new = belt_node(
            ogm_with_mock_db, build_belt_spec(), {"id": SKOLEM_IRI, HAS_VALUE: [1.4]}
        )

        removed, added = old.diff(other=new)

        assert all(triple[1] != HAS_SPEED for triple in removed | added)

    def test_no_blank_node_reaches_the_write_path(self, ogm_with_mock_db, belt_spec):
        old = belt_node(
            ogm_with_mock_db, belt_spec, {"id": SKOLEM_IRI, HAS_VALUE: [12.1]}
        )
        new = belt_node(
            ogm_with_mock_db, build_belt_spec(), {"id": SKOLEM_IRI, HAS_VALUE: [1.4]}
        )

        removed, added = old.diff(other=new)

        for triple in removed | added:
            assert not any(isinstance(term, BNode) for term in triple)

    def test_without_a_shared_address_identical_data_still_diffs(
        self, ogm_with_mock_db, belt_spec
    ):
        """The regression guard, and the reason ``commit`` reconciles before it diffs.

        Two nodes over identical content but with no address each mint their own, so the diff is
        non-empty even though nothing changed. A caller who fetched, called ``model_dump()`` and
        committed the plain dict is exactly this case — the model does not carry the address
        through a dump — which is why the address is recovered from the store side instead.
        """
        old = belt_node(ogm_with_mock_db, belt_spec, {HAS_VALUE: [1.5]})
        new = belt_node(ogm_with_mock_db, build_belt_spec(), {HAS_VALUE: [1.5]})

        removed, added = old.diff(other=new)

        assert removed != set() or added != set()
