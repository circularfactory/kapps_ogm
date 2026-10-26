"""The OGM sends each query that it builds itself as text, so the interface does not parse it.

Given a `SPARQLQuery`, the interface parses the rendered text to validate it, which costs several
milliseconds a query. The OGM renders its own queries with `to_string(validate=False)`: the same
text, without that parse.

The store is a mock of the contract, `TripleStore`, that answers the reads from the stand-in
ontology of `test_interface_per_instance.py` in memory. It answers `triples_get` and the class
lookup itself, so every `query` it gets comes from the OGM.
"""

import json
import re
from pathlib import Path

import pytest
from rdflib import Graph, Literal, URIRef

from kapps_triplestore_interface import IRI, SPARQLQuery, XSDToPythonTypes
from kapps_triplestore_interface.utils.utils import (
    convert_multi_bindings_to_python_type,
)

from kapps_ogm import Interface, Update
from kapps_ogm.mapping.class_spec import _property_discovery_query
from kapps_ogm.ogm import OGM
from kapps_ogm.utils.class_scope import ClassScope
from kapps_ogm.utils.errors import OGMDataError

EX = "http://example.org/iface#"
BELT = IRI(EX + "ConveyorBelt")
HAS_SPEED = IRI(EX + "hasSpeed")
OPCUA = IRI(EX + "opcuaParameter")
HAS_VALUE = IRI(EX + "hasValue")
HAS_UNIT = IRI(EX + "hasUnit")
BELT_MQTT = IRI("http://example.org/i/beltMqtt")

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
            None if term is None else URIRef(str(IRI(term)))
            for term in (sub, pred, obj)
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


def sent(store) -> list:
    """What the OGM gave the store's `query`, in order."""
    return [
        call.args[0] if call.args else call.kwargs["query"]
        for call in store.query.call_args_list
    ]


def assert_all_text(store) -> None:
    queries = sent(store)
    assert queries, "the call sent no query"
    built = [query for query in queries if not isinstance(query, str)]
    assert built == [], f"{len(built)} of {len(queries)} queries went as SPARQLQuery"


def test_a_class_specification_sends_its_queries_as_text(ogm, store):
    # The property discovery, the maximum counts and, with a merge depth, the superproperties.
    ogm.get_class_spec(
        class_iri=BELT,
        class_scope=ClassScope.from_property_chains([[HAS_SPEED]]),
        merge_depth=1,
    )
    assert_all_text(store)
    assert _property_discovery_query(BELT).to_string() in sent(store)


def test_a_fetch_sends_its_queries_as_text(ogm, store):
    # The anonymous parameter node is read with a query the OGM builds.
    ogm.fetch(
        instance_iri=BELT_MQTT,
        class_scope=ClassScope.from_property_chains([[HAS_SPEED]]),
        materialize=True,
    )
    assert_all_text(store)


def test_an_authoritative_update_sends_its_queries_as_text(ogm, store):
    # The stored terms of the values that the declaration makes stale are read as well.
    ogm.commit(
        changes=[
            Update(
                instance_iri=BELT_MQTT,
                data={HAS_SPEED: [{HAS_VALUE: [1.5], HAS_UNIT: ["m/s"]}]},
                interfaces=(Interface(HAS_SPEED, OPCUA, authoritative=True),),
            )
        ]
    )
    assert_all_text(store)


def test_the_refusal_of_an_undeclared_property_sends_its_query_as_text(ogm, store):
    # The refusal asks which properties the class declares.
    with pytest.raises(OGMDataError):
        ogm.create(
            class_iri=BELT,
            data={EX + "notDeclared": [1.0]},
            class_scope=ClassScope.from_property_chains([[HAS_SPEED]]),
            persist=False,
        )
    assert_all_text(store)
