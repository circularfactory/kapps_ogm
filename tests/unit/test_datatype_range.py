"""A property whose range is a datatype outside the mapped XSD types is a literal property.

The ranges are `rdfs:Literal`, a datatype declared `rdf:type rdfs:Datatype` (a mixture datatype
stands for it here, named as an example and not a model of a mixture), and an XSD datatype that
the store interface has no python type for (`xsd:gMonth`). Each gives a literal property
specification with no fixed python type, and a value that carries its datatype is written with it.

The stand-in holds `rdf:type rdfs:Class` beside `rdf:type rdfs:Datatype` for the custom datatype,
as the live store infers it, so the datatype check has to come before the class check. The store
is a mock of the contract, `TripleStore`, that answers the reads from the stand-in in memory.
"""

import json
import re
from pathlib import Path

import pytest
from rdflib import Graph, Literal, URIRef, XSD

from kapps_triplestore_interface import IRI, SPARQLQuery, XSDToPythonTypes
from kapps_triplestore_interface.utils.utils import (
    convert_multi_bindings_to_python_type,
)

from kapps_ogm import Update
from kapps_ogm.mapping.property_spec import PropertyValueKind
from kapps_ogm.ogm import OGM
from kapps_ogm.utils.class_scope import ClassScope
from kapps_ogm.utils.errors import OGMDataError

EX = "http://example.org/ex#"
SERVICE = IRI(EX + "Service")
HAS_MIXTURE = IRI(EX + "hasMixture")
HAS_ANY = IRI(EX + "hasAny")
HAS_MONTH = IRI(EX + "hasMonth")
HAS_LABEL = IRI(EX + "hasLabel")
GAUSSIAN_MIXTURE = IRI(EX + "GaussianMixture")
INSTANCE = IRI("http://example.org/i/service1")

STAND_IN = Path(__file__).parent.parent / "test_data" / "datatype_range.ttl"

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
            None if term is None else IRI(term) for term in (sub, pred, obj)
        )
        return [
            (s, IRI(str(p)), _python_value(o)) for s, p, o in graph.triples(pattern)
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


@pytest.mark.parametrize(
    "prop",
    [HAS_MIXTURE, HAS_ANY, HAS_MONTH, HAS_LABEL],
    ids=["declared datatype", "rdfs:Literal", "unmapped xsd", "rdf:langString"],
)
def test_the_range_gives_a_literal_property_without_a_python_type(ogm, prop):
    spec = ogm.get_class_spec(class_iri=SERVICE, class_scope=scope(prop))

    assert spec.properties[prop].value_kind is PropertyValueKind.LITERAL
    assert spec.properties[prop].python_range_type is None


@pytest.mark.parametrize("prop", [HAS_MIXTURE, HAS_ANY, HAS_MONTH])
def test_a_property_chain_through_the_range_is_refused(ogm, prop):
    nested = ClassScope.from_property_chains([[prop, IRI(EX + "deeper")]])

    with pytest.raises(OGMDataError, match="property chain"):
        ogm.get_class_spec(class_iri=SERVICE, class_scope=nested)


def test_create_writes_a_custom_typed_literal_with_its_datatype(ogm, store):
    mixture = Literal("[0.3, 0.7]", datatype=URIRef(GAUSSIAN_MIXTURE))

    ogm.create(
        class_iri=SERVICE,
        instance_iri=INSTANCE,
        data={HAS_MIXTURE: [mixture]},
        class_scope=scope(HAS_MIXTURE),
    )

    written = [t for call in store.triples_add.call_args_list for t in call.args[0]]
    values = [o for s, p, o in written if p == HAS_MIXTURE]
    assert values == [mixture]
    assert values[0].datatype == URIRef(GAUSSIAN_MIXTURE)


def test_fetch_gives_back_the_stored_value_with_its_datatype(ogm):
    fetched = ogm.fetch(
        instance_iri=INSTANCE, class_scope=scope(HAS_MIXTURE), materialize=True
    )

    (value,) = fetched.data[HAS_MIXTURE]
    assert value == Literal("[0.3, 0.7]", datatype=URIRef(GAUSSIAN_MIXTURE))
    assert value.datatype == URIRef(GAUSSIAN_MIXTURE)


def test_commit_replaces_the_value_in_one_update(ogm, store):
    updated = Literal("[0.1, 0.9]", datatype=URIRef(GAUSSIAN_MIXTURE))

    ogm.commit(
        changes=[Update(instance_iri=INSTANCE, data={str(HAS_MIXTURE): [updated]})]
    )

    store.triples_add.assert_not_called()
    store.triples_update.assert_called_once()
    call = store.triples_update.call_args.kwargs
    assert [o for s, p, o in call["new_triples"]] == [updated]
    assert len(call["old_triples"]) == 1


def test_a_plain_string_is_written_as_xsd_string(ogm, store):
    ogm.create(
        class_iri=SERVICE,
        instance_iri=INSTANCE,
        data={HAS_MIXTURE: ["[0.3, 0.7]"]},
        class_scope=scope(HAS_MIXTURE),
    )

    written = [t for call in store.triples_add.call_args_list for t in call.args[0]]
    (value,) = [o for s, p, o in written if p == HAS_MIXTURE]
    assert str(value) == "[0.3, 0.7]"
    # Not the declared datatype: a plain string carries none of its own.
    assert getattr(value, "datatype", None) in (None, URIRef(str(XSD.string)))
    assert getattr(value, "datatype", None) != URIRef(GAUSSIAN_MIXTURE)
