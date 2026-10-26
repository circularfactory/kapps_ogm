"""``Append``: one value added to a multi-valued property of a stored instance, through ``OGM.commit``.

An append writes one value and leaves the other values of the property in place. It is
validated as a commit's value is: the class of the instance must have the property, the value
must fit its range, and a bounded property must not go over its maximum. A commit whose changes
are all appends sends one request, a single ``INSERT DATA``: there is nothing to read and rewrite,
so two writers that append to one property at the same moment both land. A commit that carries
an append beside a ``Create`` or an ``Update`` puts the appended triples into its one
``DELETE/INSERT``.

The ontology is the demo ontology of the round-trip tests, `tests/test_data/demo.ttl`, with its
instances: a simple property (`hasAttributeA`, a string), an object property (`hasNodeB`), a
property whose domain is a union (`hasAttributeAB`, a double) and a complex property
(`hasComplexAttribute`, an anonymous node with a value and a number). One restriction is added
to it: a node B has at most two `hasAttributeB`. The store is a mock of the contract,
`TripleStore`, which answers the reads from the ontology in memory and records the writes.

The last class runs on a live store: two threads append to one property of one instance at the
same moment, and both values land.
"""

import json
import re
import threading
import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError
from rdflib import Graph, Literal, URIRef

from kapps_triplestore_interface import IRI, SPARQLQuery, TripleStore, XSDToPythonTypes
from kapps_triplestore_interface.utils.utils import (
    convert_multi_bindings_to_python_type,
)

from kapps_ogm import Append, Create, Update
from kapps_ogm.ogm import OGM
from kapps_ogm.utils.errors import OGMDataError, OGMNotFoundError, OGMUsageError
from kapps_ogm.utils.skolem import is_skolem_iri

DS = "http://www.sfb1574.kit.edu/ontologies/DemoStructure#"
DSI = "http://www.sfb1574.kit.edu/ontologies/DemoStructureInstance#"

NODE_A = IRI(DS + "NodeA")
NODE_B = IRI(DS + "NodeB")
HAS_NODE_B = IRI(DS + "hasNodeB")
HAS_ATTRIBUTE_A = IRI(DS + "hasAttributeA")
HAS_ATTRIBUTE_AB = IRI(DS + "hasAttributeAB")
HAS_ATTRIBUTE_B = IRI(DS + "hasAttributeB")
HAS_ATTRIBUTE_C = IRI(DS + "hasAttributeC")
HAS_COMPLEX_ATTRIBUTE = IRI(DS + "hasComplexAttribute")
HAS_VALUE = IRI(DS + "hasValue")
HAS_NUMBER = IRI(DS + "hasNumber")

A1 = IRI(DSI + "NodeAInstance1")
A2 = IRI(DSI + "NodeAInstance2")
B1 = IRI(DSI + "NodeBInstance1")
B2 = IRI(DSI + "NodeBInstance2")
B3 = IRI(DSI + "NodeBInstance3")
MISSING = IRI(DSI + "NoSuchInstance")

RDF_TYPE = IRI("rdf:type")
NAMED_INDIVIDUAL = IRI("owl:NamedIndividual")

DEMO = Path(__file__).parent.parent / "test_data" / "demo.ttl"

# A node B has at most two values of `hasAttributeB`. Each stored node B holds one.
BOUND = f"""
@prefix ds: <{DS}> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

ds:NodeB rdfs:subClassOf
    [ a owl:Restriction ; owl:onProperty ds:hasAttributeB ;
      owl:maxCardinality "2"^^xsd:nonNegativeInteger ] .
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
    """The mocked store, which answers each read from the demo ontology and its instances."""
    graph = Graph()
    graph.parse(DEMO, format="turtle")
    graph.parse(data=BOUND, format="turtle")

    def triples_get(sub=None, pred=None, obj=None, **_):
        pattern = tuple(
            None if term is None else IRI(term) for term in (sub, pred, obj)
        )
        return [
            (s, IRI(str(p)), _python_value(o)) for s, p, o in graph.triples(pattern)
        ]

    def classes_of(instance, **_):
        return {
            IRI(str(o))
            for o in graph.objects(URIRef(str(instance)), URIRef(str(RDF_TYPE)))
            if IRI(str(o)) != NAMED_INDIVIDUAL
        }

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
    mock_db.owl_get_classes_of_individual.side_effect = classes_of
    mock_db.query.side_effect = query
    return mock_db


@pytest.fixture
def ogm(store) -> OGM:
    return OGM(db=store)


def _plain(term):
    """An object the OGM writes, with a literal as its python value."""
    return term.toPython() if isinstance(term, Literal) else term


def appended(store) -> set:
    """The triples of the one insert that the store received, as (subject, predicate, object
    in its python form)."""
    store.triples_add.assert_called_once()
    store.triples_update.assert_not_called()
    call = store.triples_add.call_args
    assert call.kwargs.get("check_exist") is False
    assert not call.kwargs.get("absent_subjects")
    triples = call.args[0] if call.args else call.kwargs["triples_to_add"]
    return {(s, IRI(p), _plain(o)) for s, p, o in triples}


def assert_nothing_written(store):
    store.triples_add.assert_not_called()
    store.triples_update.assert_not_called()


class TestASimpleValue:
    def test_is_one_insert_of_the_one_triple(self, ogm, store):
        ogm.commit(changes=[Append(A2, HAS_ATTRIBUTE_A, "Attribute A2.2 Content")])

        assert appended(store) == {(A2, HAS_ATTRIBUTE_A, "Attribute A2.2 Content")}

    def test_reads_no_stored_value_of_an_unbounded_property(self, ogm, store):
        """No read and rewrite: the other values of the property are never read. The reads of
        the class and its properties are of the ontology, not of the instance."""
        ogm.commit(changes=[Append(A2, HAS_ATTRIBUTE_A, "Attribute A2.2 Content")])

        subjects = {
            IRI(call.kwargs.get("sub")) for call in store.triples_get.call_args_list
        }
        assert A2 not in subjects
        assert not [call for call in store.query.call_args_list if str(A2) in str(call)]

    def test_a_property_whose_domain_is_a_union(self, ogm, store):
        ogm.commit(changes=[Append(B2, HAS_ATTRIBUTE_AB, 1.5)])

        assert appended(store) == {(B2, HAS_ATTRIBUTE_AB, 1.5)}

    def test_returns_a_node_holding_the_appended_value_only(self, ogm):
        [node] = ogm.commit(
            changes=[Append(A2, HAS_ATTRIBUTE_A, "Attribute A2.2 Content")]
        )

        assert node.id == A2
        assert node.data == {HAS_ATTRIBUTE_A: ["Attribute A2.2 Content"]}

    def test_the_named_graph_applies(self, ogm, store):
        graph = IRI("http://example.org/graphs/append")
        ogm.commit(
            changes=[Append(A2, HAS_ATTRIBUTE_A, "x")],
            named_graph=graph,
        )

        assert store.triples_add.call_args.kwargs["named_graph"] == graph


class TestALink:
    @pytest.mark.parametrize("value", [B3, {"id": str(B3)}], ids=["iri", "id"])
    def test_writes_only_the_triple_that_points_at_the_instance(
        self, ogm, store, value
    ):
        ogm.commit(changes=[Append(A2, HAS_NODE_B, value)])

        assert appended(store) == {(A2, HAS_NODE_B, B3)}

    def test_a_nested_instance_with_properties_is_refused(self, ogm, store):
        with pytest.raises(OGMUsageError, match="Create"):
            ogm.commit(
                changes=[
                    Append(
                        A2,
                        HAS_NODE_B,
                        {"id": str(B3), str(HAS_ATTRIBUTE_B): ["Attribute B3"]},
                    )
                ]
            )
        assert_nothing_written(store)


class TestAnAnonymousNode:
    VALUE = {str(HAS_VALUE): ["Complex Attribute 2.2 Value"], str(HAS_NUMBER): [7]}

    def test_is_a_new_node_under_a_skolem_iri(self, ogm, store):
        ogm.commit(changes=[Append(B2, HAS_COMPLEX_ATTRIBUTE, self.VALUE)])

        triples = appended(store)
        [link] = [t for t in triples if t[0] == B2]
        node = link[2]
        assert link[1] == HAS_COMPLEX_ATTRIBUTE
        assert is_skolem_iri(node)
        assert triples == {
            link,
            (node, HAS_VALUE, "Complex Attribute 2.2 Value"),
            (node, HAS_NUMBER, 7),
        }

    def test_two_appends_of_equal_content_are_two_nodes(self, ogm, store):
        """An anonymous node has no identity of its own (kapps_ogm 4)."""
        ogm.commit(
            changes=[
                Append(B2, HAS_COMPLEX_ATTRIBUTE, self.VALUE),
                Append(B2, HAS_COMPLEX_ATTRIBUTE, self.VALUE),
            ]
        )

        nodes = {t[2] for t in appended(store) if t[0] == B2}
        assert len(nodes) == 2

    def test_one_given_with_an_address_is_refused(self, ogm, store):
        with pytest.raises(OGMUsageError, match="anonymous"):
            ogm.commit(
                changes=[
                    Append(
                        B2,
                        HAS_COMPLEX_ATTRIBUTE,
                        {**self.VALUE, "id": "http://example.org/.well-known/genid/x"},
                    )
                ]
            )
        assert_nothing_written(store)


class TestTheValidators:
    def test_a_property_the_class_does_not_have_is_refused(self, ogm, store):
        with pytest.raises(OGMDataError) as refusal:
            ogm.commit(changes=[Append(A2, HAS_ATTRIBUTE_C, "Attribute C")])

        assert refusal.value.property_iri == HAS_ATTRIBUTE_C
        assert refusal.value.class_iri == NODE_A
        assert_nothing_written(store)

    def test_a_nested_value_with_a_property_the_class_does_not_declare_is_refused(
        self, ogm, store
    ):
        """The anonymous node of a complex property names a property that its class does not
        declare: the append raises the refusal on write, and writes nothing."""
        undeclared = IRI(DS + "hasNoSuchProperty")

        with pytest.raises(OGMDataError) as refusal:
            ogm.commit(
                changes=[
                    Append(
                        B1,
                        HAS_COMPLEX_ATTRIBUTE,
                        {HAS_VALUE: ["v"], undeclared: ["x"]},
                    )
                ]
            )

        assert type(refusal.value) is OGMDataError
        assert refusal.value.property_iri == undeclared
        assert str(undeclared) in str(refusal.value)
        assert_nothing_written(store)

    def test_a_value_outside_the_range_is_refused(self, ogm, store):
        with pytest.raises(ValidationError):
            ogm.commit(changes=[Append(B2, HAS_ATTRIBUTE_AB, "not a number")])
        assert_nothing_written(store)

    def test_an_instance_the_store_does_not_hold_is_not_found(self, ogm, store):
        with pytest.raises(OGMNotFoundError):
            ogm.commit(changes=[Append(MISSING, HAS_ATTRIBUTE_A, "x")])
        assert_nothing_written(store)


class TestTheMaximum:
    def test_a_value_within_the_maximum_is_written(self, ogm, store):
        ogm.commit(changes=[Append(B1, HAS_ATTRIBUTE_B, "Attribute B1.2 Content")])

        assert appended(store) == {(B1, HAS_ATTRIBUTE_B, "Attribute B1.2 Content")}

    def test_a_value_over_the_maximum_is_refused_before_the_write(self, ogm, store):
        with pytest.raises(OGMDataError, match="at most 2") as refusal:
            ogm.commit(
                changes=[
                    Append(B1, HAS_ATTRIBUTE_B, "Attribute B1.2 Content"),
                    Append(B1, HAS_ATTRIBUTE_B, "Attribute B1.3 Content"),
                ]
            )

        assert refusal.value.property_iri == HAS_ATTRIBUTE_B
        assert refusal.value.instance_iri == B1
        assert_nothing_written(store)

    def test_a_value_the_store_holds_already_does_not_count_again(self, ogm, store):
        """Inserting a triple the store holds changes nothing, so it adds no value."""
        ogm.commit(
            changes=[
                Append(B1, HAS_ATTRIBUTE_B, "Attribute B1 Content"),
                Append(B1, HAS_ATTRIBUTE_B, "Attribute B1.2 Content"),
            ]
        )

        assert appended(store) == {
            (B1, HAS_ATTRIBUTE_B, "Attribute B1 Content"),
            (B1, HAS_ATTRIBUTE_B, "Attribute B1.2 Content"),
        }


class TestBesideOtherChanges:
    def test_a_create_and_an_append_that_links_it_are_one_update(self, ogm, store):
        ogm.commit(
            changes=[
                Create(
                    class_iri=NODE_B,
                    data={str(HAS_ATTRIBUTE_B): ["Attribute B3 Content"]},
                    instance_iri=B3,
                ),
                Append(A2, HAS_NODE_B, B3),
            ]
        )

        store.triples_add.assert_not_called()
        store.triples_update.assert_called_once()
        call = store.triples_update.call_args.kwargs
        assert (A2, HAS_NODE_B, B3) in {
            (s, IRI(p), o) for s, p, o in call["new_triples"]
        }
        assert call["absent_subjects"] == [B3]

    def test_a_create_an_update_and_an_append_are_one_update(self, ogm, store):
        ogm.commit(
            changes=[
                Create(class_iri=NODE_B, data={}, instance_iri=B3),
                Update(instance_iri=B2, data={str(HAS_ATTRIBUTE_B): ["changed"]}),
                Append(A2, HAS_NODE_B, B3),
            ]
        )

        store.triples_update.assert_called_once()
        call = store.triples_update.call_args.kwargs
        new = {(s, IRI(p), _plain(o)) for s, p, o in call["new_triples"]}
        old = {(s, IRI(p), _plain(o)) for s, p, o in call["old_triples"]}
        assert {(A2, HAS_NODE_B, B3), (B2, HAS_ATTRIBUTE_B, "changed")} <= new
        assert (B2, HAS_ATTRIBUTE_B, "Attribute B2 Content") in old
        assert not {t for t in old if t[0] == A2}

    def test_two_appends_to_one_instance_are_one_insert(self, ogm, store):
        ogm.commit(
            changes=[
                Append(A2, HAS_ATTRIBUTE_A, "first"),
                Append(A2, HAS_NODE_B, B1),
            ]
        )

        assert appended(store) == {
            (A2, HAS_ATTRIBUTE_A, "first"),
            (A2, HAS_NODE_B, B1),
        }

    @pytest.mark.parametrize("position", ["before", "after"])
    def test_an_append_and_an_update_of_one_instance_are_refused(
        self, ogm, store, position
    ):
        update = Update(instance_iri=A2, data={str(HAS_ATTRIBUTE_A): ["changed"]})
        append = Append(A2, HAS_NODE_B, B1)
        changes = [update, append] if position == "after" else [append, update]

        with pytest.raises(OGMUsageError, match="one change"):
            ogm.commit(changes=changes)
        assert_nothing_written(store)


# ---------------------------------------------------------------------------
# Two writers at the same moment, on a live store
# ---------------------------------------------------------------------------

NS = "http://example.org/append#"
RESOURCE = IRI(NS + "Resource")
CAPABILITY = IRI(NS + "Capability")
HAS_CAPABILITY = IRI(NS + "hasCapability")

ONTOLOGY = [
    (RESOURCE, RDF_TYPE, IRI("owl:Class")),
    (CAPABILITY, RDF_TYPE, IRI("owl:Class")),
    (HAS_CAPABILITY, RDF_TYPE, IRI("owl:ObjectProperty")),
    (HAS_CAPABILITY, IRI("rdfs:domain"), RESOURCE),
    (HAS_CAPABILITY, IRI("rdfs:range"), CAPABILITY),
]


def fresh(name: str) -> IRI:
    """An instance IRI no other test uses, because the live store is wiped only per session."""
    return IRI(f"{NS}{name}_{uuid.uuid4().hex[:8]}")


class TestTwoWritersAtOnce:
    def test_both_values_land(self, db: TripleStore):
        db.triples_add(ONTOLOGY, check_exist=False)
        resource, held = fresh("resource"), fresh("capability")
        db.triples_add(
            [(resource, RDF_TYPE, RESOURCE), (resource, HAS_CAPABILITY, held)]
        )
        added = [fresh("capability"), fresh("capability")]
        barrier = threading.Barrier(len(added))
        errors: list[BaseException] = []

        def register(capability: IRI) -> None:
            try:
                ogm = OGM(db=db)
                barrier.wait()
                ogm.commit(changes=[Append(resource, HAS_CAPABILITY, capability)])
            except BaseException as error:  # pylint: disable=broad-except
                errors.append(error)

        threads = [threading.Thread(target=register, args=(c,)) for c in added]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []
        stored = {
            triple[2]
            for triple in db.triples_get(
                sub=resource, pred=HAS_CAPABILITY, include_implicit=False
            )
        }
        assert stored == {held, *added}
