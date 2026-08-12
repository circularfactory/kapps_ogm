"""Integration tests for the fetch/commit round trip through ``OGM.commit``.

These drive the canonical usage pattern — the one ``scripts/demo_update_value.py`` demonstrates
and the middleware uses::

    fetch(materialize=True) -> model_dump() -> edit a value -> commit(data=<plain dict>)

That pattern is the reason this machinery exists. The dumped payload deliberately does not carry
the anonymous node's address, so unless ``commit`` recovers it from the store the write mints a
replacement, unlinks the real node and strands everything the ClassSpec does not declare.

The fixture mirrors the failure recorded on the ticket: a conveyor belt whose speed parameter
carries a value and a unit (both declared by the range restriction) plus an MQTT topic (not
declared, and normal under the Open World Assumption).
"""

from unittest.mock import Mock, patch

import pytest
from rdflib import BNode

from kapps_triplestore_interface import GraphDB, IRI, to_literal

from kapps_ogm.mapping.class_spec import ClassHydrationLevel, ClassSpec
from kapps_ogm.mapping.property_spec import PropertySpec, PropertyValueKind
from kapps_ogm.ogm import OGM
from kapps_ogm.utils.skolem import is_skolem_iri

ONTO = "https://www.sfb1574.kit.edu/ontologies/TransferUnit"
CONVEYOR_BELT = IRI("ConveyorBelt", ONTO)
HAS_SPEED = IRI("hasConveyorSpeed", ONTO)
HAS_UNIT = IRI("hasUnit", ONTO)
HAS_VALUE = IRI("hasValue", ONTO)
HAS_MQTT_TOPIC = IRI("hasMQTTTopic", ONTO)  # undeclared by the range restriction

BELT_IRI = IRI("https://example.org/instances/ConveyorBelt1_left")
PARAMETER_IRI = IRI("https://w3id.org/circularfactory/.well-known/genid/ab12")

STORED_VALUE = 12.1
UPDATED_VALUE = 1.4


def build_belt_spec() -> ClassSpec:
    parameter = ClassSpec(
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
                nested=parameter,
            )
        },
    )


def seeded_ogm(parameter_id) -> tuple[OGM, Mock]:
    """An OGM over a store holding one belt, whose speed node carries an undeclared MQTT topic."""
    db = Mock(spec=GraphDB)
    db.triples_get.return_value = []
    db.iri_exists.return_value = False
    db.is_subclass.return_value = True
    db.owl_get_classes_of_individual.return_value = [CONVEYOR_BELT]
    db.query.return_value = {
        "results": {
            "bindings": [
                {"bnode": parameter_id, "property": HAS_VALUE, "value": STORED_VALUE},
                {"bnode": parameter_id, "property": HAS_UNIT, "value": "m/s"},
                {
                    "bnode": parameter_id,
                    "property": HAS_MQTT_TOPIC,
                    "value": "TransferUnit1/ConveyorBelt/left/speed",
                },
            ]
        }
    }
    return OGM(db=db), db


def round_trip(parameter_id, new_value=None) -> tuple[set, set, dict]:
    """Fetch, dump, optionally edit the speed, commit. Returns what the store was asked to change."""
    ogm, db = seeded_ogm(parameter_id)
    spec = build_belt_spec()

    with patch.object(ogm, "get_class_spec", return_value=spec):
        fetched = ogm.fetch(instance_iri=BELT_IRI, class_spec=spec, materialize=True)

        payload = fetched.instance.model_dump()
        payload["id"] = str(BELT_IRI)
        if new_value is not None:
            payload[HAS_SPEED.lined][0][HAS_VALUE.lined] = [new_value]

        ogm.commit(instance_iri=BELT_IRI, data=payload)

    call = db.triples_update.call_args
    return call.kwargs["old_triples"], call.kwargs["new_triples"], payload


@pytest.fixture
def unchanged():
    return round_trip(PARAMETER_IRI)


@pytest.fixture
def changed():
    return round_trip(PARAMETER_IRI, UPDATED_VALUE)


class TestTheDumpedPayload:
    """What the caller round-trips through has no address in it — by design."""

    def test_the_parameter_dumps_without_an_id(self, unchanged):
        _, _, payload = unchanged
        assert "id" not in payload[HAS_SPEED.lined][0]

    def test_the_payload_never_mentions_the_address(self, unchanged):
        _, _, payload = unchanged
        assert str(PARAMETER_IRI) not in str(payload)


class TestAnUnchangedCommit:
    def test_writes_nothing_at_all(self, unchanged):
        removed, added, _ = unchanged
        assert (removed, added) == (set(), set())


class TestAChangedValue:
    def test_deletes_exactly_the_old_value_triple(self, changed):
        removed, _, _ = changed
        assert removed == {(PARAMETER_IRI, HAS_VALUE, to_literal(STORED_VALUE))}

    def test_inserts_exactly_the_new_value_triple(self, changed):
        _, added, _ = changed
        assert added == {(PARAMETER_IRI, HAS_VALUE, to_literal(UPDATED_VALUE))}

    def test_never_unlinks_the_parameter_from_the_belt(self, changed):
        """Unlinking is what orphans the node and everything undeclared hanging off it."""
        removed, added, _ = changed
        assert all(triple[1] != HAS_SPEED for triple in removed | added)

    def test_leaves_the_unchanged_unit_alone(self, changed):
        removed, added, _ = changed
        assert all(triple[1] != HAS_UNIT for triple in removed | added)

    def test_keeps_the_parameter_at_its_own_address(self, changed):
        removed, added, _ = changed
        assert all(triple[0] == PARAMETER_IRI for triple in removed | added)

    def test_sends_no_blank_node_to_the_store(self, changed):
        removed, added, _ = changed
        assert not any(
            isinstance(term, BNode) for triple in removed | added for term in triple
        )


class TestALegacyBlankNode:
    """A node seeded as `[ ... ]` is relocated to a Skolem IRI once, then stays put."""

    @pytest.fixture
    def relocated(self):
        return round_trip(BNode("legacy-genid-1360"), UPDATED_VALUE)

    def test_the_new_side_lands_on_a_skolem_iri(self, relocated):
        _, added, _ = relocated
        subjects = {triple[0] for triple in added if triple[1] != HAS_SPEED}
        assert subjects and all(is_skolem_iri(subject) for subject in subjects)

    def test_the_old_side_still_names_the_blank_node(self, relocated):
        removed, _, _ = relocated
        assert any(isinstance(triple[0], BNode) for triple in removed)
