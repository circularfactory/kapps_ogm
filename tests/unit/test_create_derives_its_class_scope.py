"""``OGM.create`` derives its class scope from its data, as ``OGM.commit`` does.

With no class scope and no loader, ``create`` builds the scope from the data it is given: each
property of the data, and each property of a nested value with properties, down to the leaves.
A class scope that the caller passes wins over the derived one, and so does a loader. The scope
of empty data is empty: ``create`` and ``commit`` with ``data={}`` raise no ``IndexError``.

The ontology is a small stand-in in ``example.org``: a workpiece with a literal, an object
property whose range is a named class with a literal of its own, and a complex property whose
range is an anonymous restriction. The store is a mock of the contract, ``TripleStore``, which
answers the reads from the stand-in in memory and records the writes.
"""

import json
import re
from unittest.mock import Mock, patch

import pytest
from rdflib import BNode, Graph, Literal, URIRef
from rdflib.compare import isomorphic

from kapps_triplestore_interface import IRI, SPARQLQuery, XSDToPythonTypes
from kapps_triplestore_interface.utils.utils import (
    convert_multi_bindings_to_python_type,
)

from kapps_ogm import Create, OGMUsageError
from kapps_ogm.ogm import OGM
from kapps_ogm.utils.class_scope import ClassScope
from kapps_ogm.utils.skolem import is_skolem_iri

NS = "https://example.org/derivedscope#"

WORKPIECE = IRI(NS + "Workpiece")
ORIGIN = IRI(NS + "Origin")

HAS_SERIAL = IRI(NS + "hasSerial")
HAS_ORIGIN = IRI(NS + "hasOrigin")
HAS_NAME = IRI(NS + "hasName")
HAS_SPEED = IRI(NS + "hasSpeed")
HAS_VALUE = IRI(NS + "hasValue")

NEW = IRI("https://example.org/i/new")
ORIGIN_1 = IRI("https://example.org/i/origin1")
STORED = IRI("https://example.org/i/stored")

# Every property of the workpiece data below, nested ones included.
ALL_PROPERTIES = {HAS_SERIAL, HAS_ORIGIN, HAS_NAME, HAS_SPEED, HAS_VALUE}

STAND_IN = f"""
@prefix ex: <{NS}> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

ex:Workpiece a owl:Class .
ex:Origin a owl:Class .

ex:hasSerial a owl:DatatypeProperty ;
    rdfs:domain ex:Workpiece ; rdfs:range xsd:string .
ex:hasOrigin a owl:ObjectProperty ;
    rdfs:domain ex:Workpiece ; rdfs:range ex:Origin .
ex:hasName a owl:DatatypeProperty ;
    rdfs:domain ex:Origin ; rdfs:range xsd:string .
ex:hasValue a owl:DatatypeProperty .
ex:hasSpeed a owl:ObjectProperty ;
    rdfs:domain ex:Workpiece ;
    rdfs:range [ a owl:Restriction ; owl:onProperty ex:hasValue ;
                 owl:allValuesFrom xsd:double ;
                 owl:maxCardinality "1"^^xsd:nonNegativeInteger ] .

<{STORED}> a ex:Workpiece ; ex:hasSerial "S0" .
"""

# The form in which the triple store interface writes "explicit statements only". The stand-in
# holds explicit statements only, so the mock reads a query without it.
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
    mock_db.owl_get_classes_of_individual.side_effect = lambda iri, **_: (
        {WORKPIECE} if IRI(iri) == STORED else set()
    )
    return mock_db


@pytest.fixture
def ogm(store) -> OGM:
    return OGM(db=store)


def workpiece() -> dict:
    """A workpiece with a literal, a nested instance with properties, and a nested anonymous
    node: a complex property."""
    return {
        HAS_SERIAL: ["S1"],
        HAS_ORIGIN: [{"id": ORIGIN_1, HAS_NAME: ["Plant 1"]}],
        HAS_SPEED: [{HAS_VALUE: [2.5]}],
    }


def written_graph(store) -> Graph:
    """The triples of the one `triples_add` call, with each minted Skolem IRI as a blank node,
    so that two writes compare by their shape and not by the addresses they minted."""
    store.triples_add.assert_called_once()
    (triples,), _ = store.triples_add.call_args
    bnodes: dict = {}

    def term(value):
        if isinstance(value, (IRI, URIRef)) and is_skolem_iri(value):
            return bnodes.setdefault(str(value), BNode())
        if isinstance(value, (IRI, URIRef)):
            return URIRef(str(value))
        return value if isinstance(value, (BNode, Literal)) else Literal(value)

    graph = Graph()
    for s, p, o in triples:
        graph.add((term(s), URIRef(IRI(p)), term(o)))
    return graph


def written_predicates(store) -> set:
    return {IRI(p) for _, p, _ in written_graph(store)}


def scope_of_a_create(ogm: OGM, data: dict, **create_args) -> ClassScope:
    """The class scope that `create` hands to the class specification."""
    with patch.object(
        ogm, "get_class_spec", wraps=ogm.get_class_spec
    ) as get_class_spec:
        ogm.create(class_iri=WORKPIECE, instance_iri=NEW, data=data, **create_args)
    return get_class_spec.call_args.kwargs["class_scope"]


class TestCreateWithoutAScopeDerivesItFromTheData:
    def test_it_writes_what_an_explicit_derived_scope_writes(self, ogm, store):
        """The nested instance and the nested anonymous node are written as with the scope
        derived from the same data, passed explicitly."""
        ogm.create(class_iri=WORKPIECE, instance_iri=NEW, data=workpiece())
        without_scope = written_graph(store)
        store.triples_add.reset_mock()

        ogm.create(
            class_iri=WORKPIECE,
            instance_iri=NEW,
            data=workpiece(),
            class_scope=ClassScope.from_data_dict(workpiece()),
        )
        with_scope = written_graph(store)

        assert isomorphic(without_scope, with_scope)
        assert ALL_PROPERTIES <= written_predicates(store)

    def test_the_derived_scope_reaches_the_class_specification(self, ogm):
        assert scope_of_a_create(ogm, workpiece()) == ClassScope.from_data_dict(
            workpiece()
        )

    def test_a_create_in_commit_derives_it_too(self, ogm, store):
        ogm.commit(
            changes=[Create(class_iri=WORKPIECE, instance_iri=NEW, data=workpiece())]
        )

        added = store.triples_update.call_args.kwargs["new_triples"]
        assert ALL_PROPERTIES <= {IRI(p) for _, p, _ in added}


class TestAGivenScopeWins:
    """The given scope is the one used, even where it covers more than the data."""

    WIDER = ClassScope.from_property_chains([[HAS_SERIAL], [HAS_ORIGIN, HAS_NAME]])

    def test_an_explicit_scope_wins_over_the_derived_one(self, ogm):
        assert (
            scope_of_a_create(ogm, {HAS_SERIAL: ["S1"]}, class_scope=self.WIDER)
            == self.WIDER
        )

    def test_a_loader_wins_over_the_derived_scope(self, store):
        loader = Mock()
        loader.expand.return_value = self.WIDER
        ogm = OGM(db=store, loader=loader)

        assert scope_of_a_create(ogm, {HAS_SERIAL: ["S1"]}) == self.WIDER
        loader.expand.assert_called_with(WORKPIECE)


class TestEmptyData:
    def test_its_scope_is_empty(self):
        assert ClassScope.from_data_dict({}) == ClassScope()

    def test_create_writes_the_instance_and_its_type_only(self, ogm, store):
        ogm.create(class_iri=WORKPIECE, instance_iri=NEW, data={})

        assert {(s, p) for s, p, _ in written_graph(store)} == {
            (URIRef(NEW), URIRef(IRI("rdf:type")))
        }

    def test_commit_changes_nothing(self, ogm, store):
        ogm.commit(instance_iri=STORED, data={})

        store.triples_add.assert_not_called()
        store.triples_update.assert_called_once()
        call = store.triples_update.call_args
        assert not call.kwargs["old_triples"]
        assert not call.kwargs["new_triples"]


class TestAScopeThatCannotBeDerived:
    def test_the_error_names_the_missing_scope(self, ogm, store):
        """Data with properties whose derived scope is empty is a call create cannot serve:
        the error says so, and nothing is written."""
        with patch.object(ClassScope, "from_data_dict", return_value=ClassScope()):
            with pytest.raises(OGMUsageError, match="class scope"):
                ogm.create(class_iri=WORKPIECE, instance_iri=NEW, data=workpiece())

        store.triples_add.assert_not_called()
