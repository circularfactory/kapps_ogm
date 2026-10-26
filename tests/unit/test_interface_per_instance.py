"""A parameter's interface is resolved per instance, and the view is selected by merge depth.

The parameter property `ex:hasSpeed` names no protocol. Its node gets the shape of an interface
property from three sources, merged: the properties the stored node carries (ABox evidence), a
caller's `Interface` declaration, and an `rdfs:subPropertyOf` marker in the TBox. The merge depth
counts the levels of the interface hierarchy from its most general property down: 1 gives the
user-facing view (the domain restriction and `ex:interfaceParameter`), no depth gives the wiring
view with the connection details.

The store is a mock of the contract, `TripleStore`, that answers the reads from the stand-in
ontology in memory, as in `test_datatype_range.py`.
"""

import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError
from rdflib import XSD, Graph, Literal, URIRef

from kapps_triplestore_interface import IRI, SPARQLQuery, XSDToPythonTypes
from kapps_triplestore_interface.utils.utils import (
    convert_multi_bindings_to_python_type,
)

from kapps_ogm import Create, Interface, Update
from kapps_ogm import ogm as ogm_module
from kapps_ogm.ogm import OGM
from kapps_ogm.utils.class_scope import ClassScope
from kapps_ogm.utils.errors import OGMDataError, OGMUsageError

EX = "http://example.org/iface#"
BELT = IRI(EX + "ConveyorBelt")
HAS_SPEED = IRI(EX + "hasSpeed")
HAS_MARKED_SPEED = IRI(EX + "hasMarkedSpeed")
INTERFACE = IRI(EX + "interfaceParameter")
MQTT = IRI(EX + "mqttParameter")
OPCUA = IRI(EX + "opcuaParameter")
HAS_VALUE = IRI(EX + "hasValue")
HAS_UNIT = IRI(EX + "hasUnit")
ACCESS_MODE = IRI(EX + "accessMode")
HAS_TOPIC = IRI(EX + "hasTopic")
HAS_BROKER = IRI(EX + "hasBroker")
HAS_PORT = IRI(EX + "hasPort")
HAS_ENDPOINT = IRI(EX + "hasEndpoint")
RDFS_COMMENT = IRI("http://www.w3.org/2000/01/rdf-schema#comment")

BELT_MQTT = IRI("http://example.org/i/beltMqtt")
BELT_OPCUA = IRI("http://example.org/i/beltOpcua")
BELT_BARE = IRI("http://example.org/i/beltBare")
BELT_DUAL = IRI("http://example.org/i/beltDual")
BELT_MARKED = IRI("http://example.org/i/beltMarked")
BELT_TWO_BROKERS = IRI("http://example.org/i/beltTwoBrokers")
NODE_MQTT = IRI("http://example.org/.well-known/genid/mqtt")

DOMAIN = {HAS_VALUE, HAS_UNIT}
MQTT_SHAPE = DOMAIN | {ACCESS_MODE, HAS_TOPIC, HAS_BROKER, HAS_PORT}
OPCUA_SHAPE = DOMAIN | {ACCESS_MODE, HAS_ENDPOINT}

STAND_IN = Path(__file__).parent.parent / "test_data" / "interface_per_instance.ttl"

DATASET_CLAUSE = re.compile(r"^FROM <[^>]*>$", re.MULTILINE)


def _python_value(term):
    if isinstance(term, URIRef):
        iri = IRI(str(term))
        return XSDToPythonTypes.get(iri, iri)
    return term.toPython() if isinstance(term, Literal) else term


@pytest.fixture
def store(mock_db):
    graph = Graph()
    graph.parse(STAND_IN, format="turtle")

    def triples_get(sub=None, pred=None, obj=None, **_):
        pattern = tuple(
            None if term is None else URIRef(str(IRI(term))) for term in (sub, pred, obj)
        )
        return [
            (IRI(str(s)), IRI(str(p)), _python_value(o))
            for s, p, o in graph.triples(pattern)
        ]

    def query(query, update=False, convert_bindings=False):
        assert not update, "the stand-in is read only"
        text = query.to_string() if isinstance(query, SPARQLQuery) else query
        answer = json.loads(
            graph.query(DATASET_CLAUSE.sub("", text)).serialize(format="json")
        )
        if convert_bindings and "results" in answer:
            answer["results"]["bindings"] = convert_multi_bindings_to_python_type(
                answer["results"]["bindings"]
            )
        return answer

    def classes_of(instance, **_):
        return {
            IRI(str(o))
            for o in graph.objects(URIRef(str(instance)), URIRef(str(IRI("rdf:type"))))
            if IRI(str(o)) != IRI("owl:NamedIndividual")
        }

    mock_db.owl_get_classes_of_individual.side_effect = classes_of
    mock_db.triples_get.side_effect = triples_get
    mock_db.query.side_effect = query
    return mock_db


@pytest.fixture
def ogm(store) -> OGM:
    return OGM(db=store)


def scope(*properties: IRI) -> ClassScope:
    return ClassScope.from_property_chains([[prop] for prop in properties])


def shape(spec, prop=HAS_SPEED) -> set:
    """The properties of the parameter node under ``prop``."""
    return set(spec.properties[prop].nested.properties)


def fetched_shape(ogm, instance, prop=HAS_SPEED, **kwargs) -> set:
    node = ogm.fetch(instance_iri=instance, class_scope=scope(prop), **kwargs)
    return shape(node.class_spec, prop)


# --- Sources: evidence, declaration, TBox marker -------------------------------------------


def test_without_a_source_the_node_has_the_domain_shape(ogm):
    assert fetched_shape(ogm, BELT_BARE) == DOMAIN


def test_the_metadata_a_node_carries_resolves_its_interface(ogm):
    assert fetched_shape(ogm, BELT_MQTT) == MQTT_SHAPE
    assert fetched_shape(ogm, BELT_OPCUA) == OPCUA_SHAPE


def test_a_declaration_gives_a_node_without_metadata_the_same_shape(ogm):
    declared = ogm.get_class_spec(
        class_iri=BELT,
        class_scope=scope(HAS_SPEED),
        interfaces=[Interface(HAS_SPEED, MQTT)],
    )
    evidenced = ogm.fetch(instance_iri=BELT_MQTT, class_scope=scope(HAS_SPEED))

    assert shape(declared) == MQTT_SHAPE
    assert declared.properties[HAS_SPEED] == evidenced.class_spec.properties[HAS_SPEED]


def test_a_node_with_metadata_of_two_protocols_resolves_both(ogm):
    assert fetched_shape(ogm, BELT_DUAL) == MQTT_SHAPE | OPCUA_SHAPE


def test_a_declaration_and_evidence_merge(ogm):
    resolved = fetched_shape(
        ogm, BELT_MQTT, interfaces=[Interface(HAS_SPEED, OPCUA)]
    )
    assert resolved == MQTT_SHAPE | OPCUA_SHAPE


def test_the_tbox_marker_still_resolves(ogm):
    assert fetched_shape(ogm, BELT_MARKED, prop=HAS_MARKED_SPEED) == {
        HAS_VALUE,
        ACCESS_MODE,
        HAS_TOPIC,
        HAS_BROKER,
        HAS_PORT,
    }


def test_a_class_spec_given_to_fetch_is_used_as_given(ogm):
    given = ogm.get_class_spec(class_iri=BELT, class_scope=scope(HAS_SPEED))

    node = ogm.fetch(instance_iri=BELT_MQTT, class_spec=given, materialize=True)

    assert node.class_spec is given
    assert shape(given) == DOMAIN
    (speed,) = node.instance.model_dump()[HAS_SPEED.lined]
    assert HAS_TOPIC.lined not in speed


def test_the_resolved_shape_keeps_the_bounds_of_the_interface(ogm):
    node = ogm.fetch(instance_iri=BELT_MQTT, class_scope=scope(HAS_SPEED))

    assert node.class_spec.properties[HAS_SPEED].nested.properties[HAS_BROKER].max_count == 1


# --- Determinism ------------------------------------------------------------------------------


def test_the_shape_does_not_depend_on_the_order_of_the_sources(ogm):
    def resolve(*interfaces):
        return ogm.get_class_spec(
            class_iri=BELT,
            class_scope=scope(HAS_SPEED),
            interfaces=[Interface(HAS_SPEED, iface) for iface in interfaces],
        ).properties[HAS_SPEED]

    one_way = resolve(MQTT, OPCUA)
    other_way = resolve(OPCUA, MQTT)

    assert one_way == other_way
    assert list(one_way.nested.properties) == list(other_way.nested.properties)
    assert one_way.nested.metadata == other_way.nested.metadata
    evidenced = ogm.fetch(instance_iri=BELT_DUAL, class_scope=scope(HAS_SPEED))
    assert evidenced.class_spec.properties[HAS_SPEED] == one_way


def test_two_instances_of_one_class_do_not_share_a_shape(ogm):
    mqtt = ogm.fetch(instance_iri=BELT_MQTT, class_scope=scope(HAS_SPEED))
    opcua = ogm.fetch(instance_iri=BELT_OPCUA, class_scope=scope(HAS_SPEED))

    assert mqtt.class_spec is not opcua.class_spec
    assert shape(mqtt.class_spec) == MQTT_SHAPE
    assert shape(opcua.class_spec) == OPCUA_SHAPE


# --- Merge depth ------------------------------------------------------------------------------


def dumped_speed(node, prop=HAS_SPEED) -> dict:
    (speed,) = node.instance.model_dump()[prop.lined]
    return speed


def test_the_user_facing_view_has_no_connection_details(ogm):
    node = ogm.fetch(
        instance_iri=BELT_MQTT,
        class_scope=scope(HAS_SPEED),
        merge_depth=1,
        materialize=True,
    )

    speed = dumped_speed(node)
    assert set(speed) == {HAS_VALUE.lined, HAS_UNIT.lined, ACCESS_MODE.lined}
    assert speed[ACCESS_MODE.lined] == ["readwrite"]


def test_the_user_facing_view_of_a_tbox_marked_property_has_no_connection_details(ogm):
    node = ogm.fetch(
        instance_iri=BELT_MARKED,
        class_scope=scope(HAS_MARKED_SPEED),
        merge_depth=1,
        materialize=True,
    )

    assert set(dumped_speed(node, HAS_MARKED_SPEED)) == {HAS_VALUE.lined, ACCESS_MODE.lined}


def test_the_wiring_view_has_the_connection_details(ogm):
    node = ogm.fetch(
        instance_iri=BELT_MQTT, class_scope=scope(HAS_SPEED), materialize=True
    )

    speed = dumped_speed(node)
    assert speed[HAS_TOPIC.lined] == ["belt/speed"]
    assert speed[HAS_BROKER.lined] == ["127.0.0.1"]


@pytest.mark.parametrize(
    "depth, expected",
    [(0, DOMAIN), (1, DOMAIN | {ACCESS_MODE}), (2, MQTT_SHAPE), (None, MQTT_SHAPE)],
)
def test_the_depth_counts_the_levels_from_the_most_general_interface(ogm, depth, expected):
    declared = ogm.get_class_spec(
        class_iri=BELT,
        class_scope=scope(HAS_SPEED),
        interfaces=[Interface(HAS_SPEED, MQTT)],
        merge_depth=depth,
    )
    assert shape(declared) == expected


def test_a_negative_depth_is_refused(ogm):
    with pytest.raises(OGMUsageError, match="merge depth"):
        ogm.get_class_spec(class_iri=BELT, class_scope=scope(HAS_SPEED), merge_depth=-1)


def test_a_depth_with_a_given_class_spec_is_refused(ogm):
    given = ogm.get_class_spec(class_iri=BELT, class_scope=scope(HAS_SPEED))

    with pytest.raises(OGMUsageError, match="class_spec"):
        ogm.fetch(instance_iri=BELT_MQTT, class_spec=given, merge_depth=1)


def test_declarations_of_one_property_must_agree_on_authoritative(ogm):
    with pytest.raises(OGMUsageError, match="authoritative"):
        ogm.get_class_spec(
            class_iri=BELT,
            class_scope=scope(HAS_SPEED),
            interfaces=[
                Interface(HAS_SPEED, MQTT, authoritative=True),
                Interface(HAS_SPEED, OPCUA),
            ],
        )


# --- Writes -----------------------------------------------------------------------------------

NEW_BELT = IRI("http://example.org/i/beltNew")
NEW_SPEED = {
    HAS_VALUE: [1.0],
    HAS_UNIT: ["m/s"],
    ACCESS_MODE: ["readwrite"],
    HAS_TOPIC: ["new/speed"],
    HAS_BROKER: ["10.0.0.1"],
}


def written(store) -> set:
    return {t for call in store.triples_add.call_args_list for t in call.args[0]}


def test_a_first_write_persists_the_metadata_of_a_declared_interface(ogm, store):
    ogm.create(
        class_iri=BELT,
        instance_iri=NEW_BELT,
        data={HAS_SPEED: [NEW_SPEED]},
        interfaces=[Interface(HAS_SPEED, MQTT)],
    )

    predicates = {p for s, p, o in written(store) if s != NEW_BELT}
    assert predicates == set(NEW_SPEED)


def test_a_first_write_without_a_declaration_refuses_the_metadata(ogm, store):
    with pytest.raises(OGMDataError):
        ogm.create(class_iri=BELT, instance_iri=NEW_BELT, data={HAS_SPEED: [NEW_SPEED]})
    store.triples_add.assert_not_called()


def test_a_create_change_takes_a_declaration(ogm, store):
    ogm.commit(
        changes=[
            Create(
                class_iri=BELT,
                instance_iri=NEW_BELT,
                data={HAS_SPEED: [NEW_SPEED]},
                interfaces=(Interface(HAS_SPEED, MQTT),),
            )
        ]
    )

    new_triples = store.triples_update.call_args.kwargs["new_triples"]
    assert HAS_TOPIC in {p for s, p, o in new_triples}


def test_a_fetched_node_goes_back_unchanged(ogm, store):
    node = ogm.fetch(instance_iri=BELT_MQTT, class_scope=scope(HAS_SPEED), materialize=True)

    ogm.commit(changes=[Update(instance_iri=BELT_MQTT, data=node.instance.model_dump())])

    store.triples_update.assert_called_once()
    call = store.triples_update.call_args.kwargs
    assert not call["old_triples"] and not call["new_triples"]


def test_an_update_reads_the_stored_node_and_its_evidence_through_the_public_fetch(
    ogm, store, monkeypatch
):
    # A tracer that wraps the public methods counts a commit's reads from the fetch below it.
    fetched = []
    inside_fetch = []
    evidence_reads = []
    public_fetch = OGM.fetch
    evidence = ogm_module.evidenced_interfaces

    def recording_fetch(self, **kwargs):
        fetched.append(kwargs["instance_iri"])
        inside_fetch.append(True)
        try:
            return public_fetch(self, **kwargs)
        finally:
            inside_fetch.pop()

    def recording_evidence(*args):
        evidence_reads.append(bool(inside_fetch))
        return evidence(*args)

    monkeypatch.setattr(OGM, "fetch", recording_fetch)
    monkeypatch.setattr(ogm_module, "evidenced_interfaces", recording_evidence)
    partial = {HAS_VALUE: [9.0], HAS_UNIT: ["m/s"], ACCESS_MODE: ["readwrite"]}

    ogm.commit(changes=[Update(instance_iri=BELT_MQTT, data={HAS_SPEED: [partial]})])

    assert fetched == [BELT_MQTT]
    assert evidence_reads and all(evidence_reads)


def test_an_authoritative_update_reads_the_stale_values_through_the_public_fetch(
    ogm, store, monkeypatch
):
    # The read of the values that an authoritative declaration makes stale sits below the
    # public fetch, as the other reads of the stored node do.
    inside_fetch = []
    reads_outside_fetch = []
    public_fetch = OGM.fetch

    def recording_fetch(self, **kwargs):
        inside_fetch.append(True)
        try:
            return public_fetch(self, **kwargs)
        finally:
            inside_fetch.pop()

    def recording(read):
        def wrapped(*args, **kwargs):
            if not inside_fetch:
                reads_outside_fetch.append(read.__name__)
            return read(*args, **kwargs)

        wrapped.__name__ = read.__name__
        return wrapped

    def outside_reads(interfaces) -> list:
        reads_outside_fetch.clear()
        ogm.commit(
            changes=[
                Update(
                    instance_iri=BELT_MQTT,
                    data={HAS_SPEED: [{HAS_VALUE: [1.5], HAS_UNIT: ["m/s"]}]},
                    interfaces=interfaces,
                )
            ]
        )
        return list(reads_outside_fetch)

    monkeypatch.setattr(OGM, "fetch", recording_fetch)
    store.query.side_effect = recording(store.query.side_effect)
    store.triples_get.side_effect = recording(store.triples_get.side_effect)

    plain = outside_reads((Interface(HAS_SPEED, OPCUA),))
    authoritative = outside_reads((Interface(HAS_SPEED, OPCUA, authoritative=True),))

    # The resolution of the class reads the ontology before the fetch, the same for both.
    assert authoritative == plain


def test_an_update_leaves_out_a_property_of_a_declared_interface_and_removes_it(ogm, store):
    # A declared interface with its data is the full answer for that write.
    partial = {HAS_VALUE: [1.5], HAS_UNIT: ["m/s"], ACCESS_MODE: ["readwrite"]}

    ogm.commit(
        changes=[
            Update(
                instance_iri=BELT_MQTT,
                data={HAS_SPEED: [partial]},
                interfaces=(Interface(HAS_SPEED, MQTT),),
            )
        ]
    )

    call = store.triples_update.call_args.kwargs
    assert {p for s, p, o in call["old_triples"]} == {HAS_TOPIC, HAS_BROKER, HAS_PORT}
    assert not call["new_triples"]


def test_an_authoritative_update_refuses_a_value_of_the_stale_interface(ogm, store):
    carried = {
        HAS_VALUE: [1.5],
        HAS_UNIT: ["m/s"],
        ACCESS_MODE: ["readwrite"],
        HAS_ENDPOINT: ["opc.tcp://belt:4840"],
        HAS_TOPIC: ["belt/speed"],
    }

    with pytest.raises(OGMDataError):
        ogm.commit(
            changes=[
                Update(
                    instance_iri=BELT_MQTT,
                    data={HAS_SPEED: [carried]},
                    interfaces=(Interface(HAS_SPEED, OPCUA, authoritative=True),),
                )
            ]
        )
    store.triples_update.assert_not_called()


def test_an_update_that_leaves_out_the_evidenced_metadata_keeps_it(ogm, store):
    partial = {HAS_VALUE: [9.0], HAS_UNIT: ["m/s"], ACCESS_MODE: ["readwrite"]}

    ogm.commit(changes=[Update(instance_iri=BELT_MQTT, data={HAS_SPEED: [partial]})])

    call = store.triples_update.call_args.kwargs
    assert {p for s, p, o in call["old_triples"]} == {HAS_VALUE}
    assert {p for s, p, o in call["new_triples"]} == {HAS_VALUE}


def test_an_update_that_names_an_evidenced_property_replaces_it(ogm, store):
    cleared = {
        HAS_VALUE: [1.5],
        HAS_UNIT: ["m/s"],
        ACCESS_MODE: ["readwrite"],
        HAS_TOPIC: [],
    }

    ogm.commit(changes=[Update(instance_iri=BELT_MQTT, data={HAS_SPEED: [cleared]})])

    call = store.triples_update.call_args.kwargs
    assert {p for s, p, o in call["old_triples"]} == {HAS_TOPIC}
    assert not call["new_triples"]


def test_a_user_facing_update_leaves_the_connection_details_alone(ogm, store):
    node = ogm.fetch(
        instance_iri=BELT_MQTT,
        class_scope=scope(HAS_SPEED),
        merge_depth=1,
        materialize=True,
    )
    data = node.instance.model_dump()
    data[HAS_SPEED.lined][0][HAS_VALUE.lined] = [9.0]

    ogm.commit(changes=[Update(instance_iri=BELT_MQTT, data=data, merge_depth=1)])

    call = store.triples_update.call_args.kwargs
    assert {p for s, p, o in call["old_triples"]} == {HAS_VALUE}
    assert {p for s, p, o in call["new_triples"]} == {HAS_VALUE}


def test_an_authoritative_declaration_removes_the_stale_metadata_in_the_same_write(
    ogm, store
):
    migrated = {
        HAS_VALUE: [1.5],
        HAS_UNIT: ["m/s"],
        ACCESS_MODE: ["readwrite"],
        HAS_ENDPOINT: ["opc.tcp://belt:4840"],
    }

    ogm.commit(
        changes=[
            Update(
                instance_iri=BELT_MQTT,
                data={HAS_SPEED: [migrated]},
                interfaces=(Interface(HAS_SPEED, OPCUA, authoritative=True),),
            )
        ]
    )

    store.triples_add.assert_not_called()
    store.triples_update.assert_called_once()
    call = store.triples_update.call_args.kwargs
    # The stored terms, datatype included, or the store would find nothing to remove.
    assert set(call["old_triples"]) == {
        (NODE_MQTT, HAS_TOPIC, Literal("belt/speed")),
        (NODE_MQTT, HAS_BROKER, Literal("127.0.0.1")),
        (NODE_MQTT, HAS_PORT, Literal("1883", datatype=XSD.int)),
    }
    assert {(s, p, str(o)) for s, p, o in call["new_triples"]} == {
        (NODE_MQTT, HAS_ENDPOINT, "opc.tcp://belt:4840")
    }


def test_an_authoritative_declaration_selects_the_shape_of_a_fetch(ogm):
    resolved = fetched_shape(
        ogm, BELT_MQTT, interfaces=[Interface(HAS_SPEED, OPCUA, authoritative=True)]
    )
    assert resolved == OPCUA_SHAPE


def test_two_values_of_a_single_valued_connection_property_are_refused(ogm):
    with pytest.raises(ValidationError):
        ogm.fetch(
            instance_iri=BELT_TWO_BROKERS, class_scope=scope(HAS_SPEED), materialize=True
        )


def test_a_user_facing_authoritative_update_removes_only_the_stale_metadata(ogm, store):
    # At depth 1 the declared interface's own connection details are out of view, not stale.
    ogm.commit(
        changes=[
            Update(
                instance_iri=BELT_MQTT,
                data={
                    HAS_SPEED: [
                        {HAS_VALUE: [1.5], HAS_UNIT: ["m/s"], ACCESS_MODE: ["readwrite"]}
                    ]
                },
                interfaces=(Interface(HAS_SPEED, OPCUA, authoritative=True),),
                merge_depth=1,
            )
        ]
    )

    call = store.triples_update.call_args.kwargs
    # rdfs:comment, which no interface restricts, stays too.
    assert set(call["old_triples"]) == {
        (NODE_MQTT, HAS_TOPIC, Literal("belt/speed")),
        (NODE_MQTT, HAS_BROKER, Literal("127.0.0.1")),
        (NODE_MQTT, HAS_PORT, Literal("1883", datatype=XSD.int)),
    }
    assert not call["new_triples"]
