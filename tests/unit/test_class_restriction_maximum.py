"""A property takes its maximum count from the restrictions of its class too.

An ontology can bound a property under `rdfs:subClassOf` of a class: a cardinality restriction
on the property, which the class and each of its subclasses inherit. The class specification
reads `owl:cardinality`, `owl:maxCardinality`, `owl:qualifiedCardinality` and
`owl:maxQualifiedCardinality` there, on the class and its superclasses, and the bound becomes the
property's maximum. So `OGM.create` refuses a second value of a property that the class bounds
to one, before the write, with the refusal it gives for every single-valued property: the list
maximum, which names the property.

The qualifier (`owl:onClass`, `owl:onDataRange`) is not checked: the bound counts every value.
Two IRIs count as two values. A bound stays on the class that declares it and on its subclasses.
Where several bounds meet, the tightest wins: a functional property, a class restriction, a
restriction of a superclass. The OGM takes no minimum from a class restriction.

The ontology is a stand-in of Core 0.9.0, in `example.org`. It copies Core's patterns: a
qualified cardinality on a class (`owl:onClass`) and on a datatype (`owl:onDataRange`), a
subclass, a restriction on a class of another namespace (Core declares one on `time:Interval`),
and a property whose domain names two classes. The store is a mock of the contract,
`TripleStore`, which answers the reads from the stand-in in memory and records the writes.
"""

import json
import re

import pytest
from pydantic import ValidationError
from rdflib import Graph, Literal, URIRef

from kapps_triplestore_interface import IRI, SPARQLQuery, XSDToPythonTypes
from kapps_triplestore_interface.utils.utils import (
    convert_multi_bindings_to_python_type,
)

from kapps_ogm.ogm import OGM
from kapps_ogm.utils.class_scope import ClassScope

NS = "https://example.org/classbounds#"
TIME_NS = "https://example.org/time#"

WORKPIECE = IRI(NS + "Workpiece")
COMPONENT = IRI(NS + "Component")
ASSEMBLY = IRI(NS + "Assembly")
INTERVAL = IRI(TIME_NS + "Interval")

IS_ACTIVE = IRI(NS + "isActive")
HAS_POSSESSED_WORKPIECE = IRI(NS + "hasPossessedWorkpiece")
HAS_POSSESSION_INTERVAL = IRI(NS + "hasPossessionInterval")
HAS_ORIGIN = IRI(NS + "hasOrigin")
HAS_SERIAL = IRI(NS + "hasSerial")
HAS_NOTE = IRI(NS + "hasNote")
HAS_SPEED = IRI(NS + "hasSpeed")
HAS_VALUE = IRI(NS + "hasValue")

STATE_1 = IRI("https://example.org/i/state1")
STATE_2 = IRI("https://example.org/i/state2")
ORIGIN_1 = IRI("https://example.org/i/origin1")
ORIGIN_2 = IRI("https://example.org/i/origin2")

STAND_IN = f"""
@prefix ex: <{NS}> .
@prefix time: <{TIME_NS}> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

ex:Workpiece a owl:Class ;
    rdfs:subClassOf
        [ a owl:Restriction ; owl:onProperty ex:hasPossessedWorkpiece ;
          owl:qualifiedCardinality "1"^^xsd:nonNegativeInteger ; owl:onClass ex:PossessionState ] ,
        [ a owl:Restriction ; owl:onProperty ex:isActive ;
          owl:qualifiedCardinality "1"^^xsd:nonNegativeInteger ; owl:onDataRange xsd:boolean ] ,
        [ a owl:Restriction ; owl:onProperty ex:hasSerial ;
          owl:maxQualifiedCardinality "2"^^xsd:nonNegativeInteger ; owl:onDataRange xsd:string ] ,
        [ a owl:Restriction ; owl:onProperty ex:hasNote ;
          owl:maxCardinality "3"^^xsd:nonNegativeInteger ] .

ex:Component a owl:Class ;
    rdfs:subClassOf ex:Workpiece ,
        [ a owl:Restriction ; owl:onProperty ex:hasOrigin ;
          owl:qualifiedCardinality "1"^^xsd:nonNegativeInteger ; owl:onClass ex:Origin ] ,
        [ a owl:Restriction ; owl:onProperty ex:hasNote ;
          owl:cardinality "2"^^xsd:nonNegativeInteger ] .

ex:Assembly a owl:Class ;
    rdfs:subClassOf ex:Workpiece .

time:Interval a owl:Class ;
    rdfs:subClassOf
        [ a owl:Restriction ; owl:onProperty ex:hasPossessionInterval ;
          owl:qualifiedCardinality "1"^^xsd:nonNegativeInteger ; owl:onClass ex:PossessionState ] .

ex:PossessionState a owl:Class .
ex:Origin a owl:Class .

ex:isActive a owl:DatatypeProperty ;
    rdfs:domain ex:Workpiece ; rdfs:range xsd:boolean .
ex:hasPossessedWorkpiece a owl:ObjectProperty ;
    rdfs:domain ex:Workpiece ; rdfs:range ex:PossessionState .
ex:hasPossessionInterval a owl:ObjectProperty ;
    rdfs:domain time:Interval ; rdfs:range ex:PossessionState .
ex:hasOrigin a owl:ObjectProperty ;
    rdfs:domain ex:Assembly , ex:Component ; rdfs:range ex:Origin .
ex:hasSerial a owl:DatatypeProperty , owl:FunctionalProperty ;
    rdfs:domain ex:Workpiece ; rdfs:range xsd:string .
ex:hasNote a owl:DatatypeProperty ;
    rdfs:domain ex:Workpiece ; rdfs:range xsd:string .
ex:hasValue a owl:DatatypeProperty .
ex:hasSpeed a owl:ObjectProperty ;
    rdfs:domain ex:Workpiece ;
    rdfs:range [ a owl:Restriction ; owl:onProperty ex:hasValue ;
                 owl:allValuesFrom xsd:double ;
                 owl:maxCardinality "1"^^xsd:nonNegativeInteger ] .
"""

# A dataset clause, the form in which the triple store interface writes "explicit statements
# only" for its store. The stand-in holds explicit statements only, so the mock reads a query
# without it.
DATASET_CLAUSE = re.compile(r"^FROM <[^>]*>$", re.MULTILINE)


def _python_value(term):
    """An object of `triples_get` as the store gives it: a literal as its python value, a
    datatype as its python type."""
    if isinstance(term, URIRef):
        iri = IRI(str(term))
        return XSDToPythonTypes.get(iri, iri)
    return term.toPython() if isinstance(term, Literal) else term


@pytest.fixture
def store(mock_db):
    """The mocked store, which answers each read from the stand-in."""
    graph = Graph()
    graph.parse(data=STAND_IN, format="turtle")

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

    mock_db.triples_get.side_effect = triples_get
    mock_db.query.side_effect = query
    return mock_db


@pytest.fixture
def ogm(store) -> OGM:
    return OGM(db=store)


def scope(*properties: IRI) -> ClassScope:
    return ClassScope.from_property_chains([[prop] for prop in properties])


def create(ogm: OGM, class_iri: IRI, data: dict):
    return ogm.create(
        class_iri=class_iri,
        instance_iri=IRI("https://example.org/i/new"),
        data=data,
        class_scope=scope(*data),
    )


def assert_refused(ogm: OGM, store, class_iri: IRI, prop: IRI, values, maximum=1):
    """`create` refuses the values with the list maximum, names the property, writes nothing."""
    with pytest.raises(ValidationError, match=f"at most {maximum} item") as refusal:
        create(ogm, class_iri, {prop: values})

    assert prop.lined in str(refusal.value)
    store.triples_add.assert_not_called()


def assert_admitted(ogm: OGM, store, class_iri: IRI, data: dict):
    create(ogm, class_iri, data)

    store.triples_add.assert_called_once()


class TestASecondValueIsRefusedBeforeTheWrite:
    def test_a_datatype_property_bounded_on_a_data_range(self, ogm, store):
        assert_refused(ogm, store, WORKPIECE, IS_ACTIVE, [True, False])

    def test_an_object_property_bounded_on_a_class(self, ogm, store):
        """Two IRIs count as two values."""
        assert_refused(
            ogm, store, WORKPIECE, HAS_POSSESSED_WORKPIECE, [STATE_1, STATE_2]
        )

    def test_a_restriction_on_a_class_of_another_namespace(self, ogm, store):
        assert_refused(
            ogm, store, INTERVAL, HAS_POSSESSION_INTERVAL, [STATE_1, STATE_2]
        )

    def test_a_subclass_inherits_the_bound(self, ogm, store):
        assert_refused(ogm, store, COMPONENT, IS_ACTIVE, [True, False])


class TestAnInstanceWithinTheBoundIsAdmitted:
    @pytest.mark.parametrize(
        "data",
        [
            {IS_ACTIVE: [], HAS_POSSESSED_WORKPIECE: []},
            {IS_ACTIVE: [True], HAS_POSSESSED_WORKPIECE: [STATE_1]},
        ],
        ids=["no value", "one value"],
    )
    def test_create_writes_it(self, ogm, store, data):
        assert_admitted(ogm, store, WORKPIECE, data)

    def test_the_bound_gives_no_minimum(self, ogm):
        spec = ogm.get_class_spec(
            class_iri=WORKPIECE, class_scope=scope(IS_ACTIVE, HAS_POSSESSED_WORKPIECE)
        )

        for prop in (IS_ACTIVE, HAS_POSSESSED_WORKPIECE):
            assert spec.properties[prop].min_count is None
            assert spec.properties[prop].required is False


class TestTheSpecificationOfASubclass:
    def test_a_subclass_has_the_maximum_of_its_superclass(self, ogm):
        spec = ogm.get_class_spec(
            class_iri=COMPONENT, class_scope=scope(IS_ACTIVE, HAS_POSSESSED_WORKPIECE)
        )

        assert spec.properties[IS_ACTIVE].max_count == 1
        assert spec.properties[HAS_POSSESSED_WORKPIECE].max_count == 1


class TestABoundStaysOnItsClass:
    """`hasOrigin` has two domains, and only one of them bounds it."""

    def test_the_class_with_the_restriction_refuses_a_second_value(self, ogm, store):
        assert_refused(ogm, store, COMPONENT, HAS_ORIGIN, [ORIGIN_1, ORIGIN_2])

    def test_the_other_domain_admits_two_values(self, ogm, store):
        assert_admitted(ogm, store, ASSEMBLY, {HAS_ORIGIN: [ORIGIN_1, ORIGIN_2]})


class TestTheTightestBoundWins:
    def test_a_looser_class_bound_leaves_a_functional_property_at_one(self, ogm, store):
        assert_refused(ogm, store, WORKPIECE, HAS_SERIAL, ["a", "b"])

    def test_the_subclass_bound_is_tighter_than_its_superclass_bound(self, ogm, store):
        assert_refused(ogm, store, COMPONENT, HAS_NOTE, ["a", "b", "c"], maximum=2)

    def test_the_superclass_keeps_its_looser_bound(self, ogm, store):
        assert_admitted(ogm, store, WORKPIECE, {HAS_NOTE: ["a", "b", "c"]})

    def test_a_range_restriction_still_bounds_its_property(self, ogm, store):
        with pytest.raises(ValidationError, match="at most 1 item"):
            create(ogm, WORKPIECE, {HAS_SPEED: [{HAS_VALUE: [1.0, 2.0]}]})

        store.triples_add.assert_not_called()
