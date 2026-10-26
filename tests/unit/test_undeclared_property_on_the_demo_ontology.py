"""A write refuses a property its class does not declare, on the demo ontology.

The ontology is `tests/test_data/demo.ttl`: simple properties (`hasAttributeA`, a string), an
object property (`hasNodeB`) and a complex property (`hasComplexAttribute`, an anonymous node).
The store is a mock of the contract, `TripleStore`, that answers the reads from the ontology and
its instances in memory. Nothing of the OGM is patched, so the class specification is built from
the ontology, as it is for a caller.

Each path of the write refuses the same way: `create` with a given scope, `create` with the scope
derived from the data, a `Create` and an `Update` of `commit`. The error is an `OGMDataError` that
names the instance, the property and the class, and no request that writes reaches the store. A
fetch of an instance with an undeclared statement still succeeds and warns.
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

from kapps_ogm import Create, Update
from kapps_ogm.ogm import OGM
from kapps_ogm.utils.class_scope import ClassScope
from kapps_ogm.utils.errors import OGMDataError, PropertyOutsideScopeError

DS = "http://www.sfb1574.kit.edu/ontologies/DemoStructure#"
DSI = "http://www.sfb1574.kit.edu/ontologies/DemoStructureInstance#"

NODE_A = IRI(DS + "NodeA")
NODE_B = IRI(DS + "NodeB")
HAS_NODE_B = IRI(DS + "hasNodeB")
HAS_ATTRIBUTE_A = IRI(DS + "hasAttributeA")
HAS_ATTRIBUTE_B = IRI(DS + "hasAttributeB")
HAS_COMPLEX_ATTRIBUTE = IRI(DS + "hasComplexAttribute")
HAS_VALUE = IRI(DS + "hasValue")
HAS_ATTRIBUTE_AB = IRI(DS + "hasAttributeAB")
UNDECLARED = IRI(DS + "hasNoSuchProperty")

A_STORED = IRI(DSI + "NodeAInstance1")
B_STORED = IRI(DSI + "NodeBInstance1")
A_NEW = IRI(DSI + "NodeAInstanceNew")
B_NEW = IRI(DSI + "NodeBInstanceNew")

RDF_TYPE = IRI("rdf:type")
NAMED_INDIVIDUAL = IRI("owl:NamedIndividual")
DEMO = Path(__file__).parent.parent / "test_data" / "demo.ttl"
DATASET_CLAUSE = re.compile(r"^FROM <[^>]*>$", re.MULTILINE)


def _python_value(term):
    if isinstance(term, URIRef):
        iri = IRI(str(term))
        return XSDToPythonTypes.get(iri, iri)
    return term.toPython() if isinstance(term, Literal) else term


@pytest.fixture
def store(mock_db):
    """The mocked store, which answers each read from the demo ontology and its instances."""
    graph = Graph()
    graph.parse(DEMO, format="turtle")

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


def assert_nothing_written(store) -> None:
    store.triples_add.assert_not_called()
    store.triples_update.assert_not_called()
    store.triples_delete.assert_not_called()


# The data that each case writes, and who carries the undeclared property: the instance, the
# property and the class, as the error names them. An anonymous node has neither an instance IRI
# nor a class IRI to name.
CREATE_CASES = {
    "a simple property of the instance": (
        NODE_A,
        A_NEW,
        {HAS_ATTRIBUTE_A: ["a"], UNDECLARED: ["x"]},
        (A_NEW, UNDECLARED, NODE_A),
    ),
    "a property of another class": (
        NODE_A,
        A_NEW,
        {HAS_ATTRIBUTE_A: ["a"], HAS_ATTRIBUTE_B: ["b"]},
        (A_NEW, HAS_ATTRIBUTE_B, NODE_A),
    ),
    "a property of a nested instance": (
        NODE_A,
        A_NEW,
        {HAS_NODE_B: [{"id": B_NEW, HAS_ATTRIBUTE_B: ["b"], UNDECLARED: ["x"]}]},
        (B_NEW, UNDECLARED, NODE_B),
    ),
    "a property of an anonymous node": (
        NODE_B,
        B_NEW,
        {HAS_COMPLEX_ATTRIBUTE: [{HAS_VALUE: ["v"], UNDECLARED: ["x"]}]},
        (None, UNDECLARED, None),
    ),
}


def assert_names(error: OGMDataError, expected: tuple, plain: bool = True) -> None:
    instance_iri, property_iri, class_iri = expected
    # An undeclared property is the plain error, not the subclass for a property outside the scope.
    if plain:
        assert type(error) is OGMDataError
    assert error.instance_iri == instance_iri
    assert error.property_iri == property_iri
    assert error.class_iri == class_iri


class TestCreate:
    @pytest.mark.parametrize("case", CREATE_CASES)
    def test_refuses_with_the_scope_derived_from_the_data(
        self, ogm, store, case
    ) -> None:
        class_iri, instance_iri, data, expected = CREATE_CASES[case]

        with pytest.raises(OGMDataError) as caught:
            ogm.create(class_iri=class_iri, instance_iri=instance_iri, data=data)

        assert_names(caught.value, expected)
        assert_nothing_written(store)

    @pytest.mark.parametrize("case", CREATE_CASES)
    def test_refuses_with_a_given_scope(self, ogm, store, case) -> None:
        class_iri, instance_iri, data, expected = CREATE_CASES[case]

        with pytest.raises(OGMDataError) as caught:
            ogm.create(
                class_iri=class_iri,
                instance_iri=instance_iri,
                data=data,
                class_scope=ClassScope.from_data_dict(data),
            )

        assert_names(caught.value, expected)
        assert_nothing_written(store)

    def test_refuses_a_property_that_a_narrower_scope_leaves_out(
        self, ogm, store
    ) -> None:
        """The scope hydrates `hasAttributeA` only; the data names a property that the scope
        does not reach, and that the class does not declare."""
        scope = ClassScope.from_property_chains([[HAS_ATTRIBUTE_A]])

        with pytest.raises(OGMDataError) as caught:
            ogm.create(
                class_iri=NODE_A,
                instance_iri=A_NEW,
                data={HAS_ATTRIBUTE_A: ["a"], UNDECLARED: ["x"]},
                class_scope=scope,
            )

        assert_names(caught.value, (A_NEW, UNDECLARED, NODE_A))
        assert_nothing_written(store)

    def test_writes_data_that_names_declared_properties_only(self, ogm, store) -> None:
        ogm.create(
            class_iri=NODE_A, instance_iri=A_NEW, data={HAS_ATTRIBUTE_A: ["a"]}
        )

        store.triples_add.assert_called_once()


class TestCommit:
    @pytest.mark.parametrize("case", CREATE_CASES)
    def test_refuses_a_create(self, ogm, store, case) -> None:
        class_iri, instance_iri, data, expected = CREATE_CASES[case]

        with pytest.raises(OGMDataError) as caught:
            ogm.commit(
                changes=[Create(class_iri=class_iri, instance_iri=instance_iri, data=data)]
            )

        assert_names(caught.value, expected)
        assert_nothing_written(store)

    @pytest.mark.parametrize(
        "instance_iri, data, expected",
        [
            (
                A_STORED,
                {HAS_ATTRIBUTE_A: ["a"], UNDECLARED: ["x"]},
                (A_STORED, UNDECLARED, NODE_A),
            ),
            (
                A_STORED,
                {HAS_ATTRIBUTE_B: ["b"]},
                (A_STORED, HAS_ATTRIBUTE_B, NODE_A),
            ),
            (
                A_STORED,
                {HAS_NODE_B: [{"id": B_STORED, UNDECLARED: ["x"]}]},
                (B_STORED, UNDECLARED, NODE_B),
            ),
            (
                B_STORED,
                {
                    HAS_COMPLEX_ATTRIBUTE: [
                        {HAS_VALUE: ["v"], UNDECLARED: ["x"]},
                        {HAS_VALUE: ["w"]},
                    ]
                },
                (None, UNDECLARED, None),
            ),
        ],
        ids=["simple", "of another class", "nested instance", "anonymous node"],
    )
    def test_refuses_an_update(self, ogm, store, instance_iri, data, expected) -> None:
        with pytest.raises(OGMDataError) as caught:
            ogm.commit(changes=[Update(instance_iri=instance_iri, data=data)])

        assert_names(caught.value, expected)
        assert_nothing_written(store)

    def test_refuses_the_single_instance_form(self, ogm, store) -> None:
        with pytest.raises(OGMDataError) as caught:
            ogm.commit(instance_iri=A_STORED, data={UNDECLARED: ["x"]})

        assert_names(caught.value, (A_STORED, UNDECLARED, NODE_A))
        assert_nothing_written(store)

    def test_one_refused_change_refuses_the_call(self, ogm, store) -> None:
        with pytest.raises(OGMDataError):
            ogm.commit(
                changes=[
                    Create(
                        class_iri=NODE_A,
                        instance_iri=A_NEW,
                        data={HAS_ATTRIBUTE_A: ["a"]},
                    ),
                    Update(instance_iri=B_STORED, data={UNDECLARED: ["x"]}),
                ]
            )

        assert_nothing_written(store)


class TestMinimumsStayUnenforcedOnWrite:
    def test_a_create_without_any_property_is_written(self, ogm, store) -> None:
        """The complex attribute's two restrictions are `someValuesFrom`, which write does not
        enforce: a node B without them is written."""
        ogm.create(class_iri=NODE_B, instance_iri=B_NEW, data={})

        store.triples_add.assert_called_once()


class TestFetchTolerates:
    def test_an_undeclared_statement_is_left_out(self, ogm, store) -> None:
        """The stored instance carries a statement about a property its class does not declare.
        A fetch under a scope that does not name it succeeds and leaves the statement out. The
        warning of a materialized node with undeclared data is pinned in
        `test_unknown_properties_are_refused_on_write.py`."""
        graph_get = store.triples_get.side_effect

        def triples_get(sub=None, pred=None, obj=None, **kwargs):
            found = graph_get(sub=sub, pred=pred, obj=obj, **kwargs)
            if sub == A_STORED and pred is None:
                found.append((A_STORED, UNDECLARED, "x"))
            return found

        store.triples_get.side_effect = triples_get
        scope = ClassScope.from_property_chains([[HAS_ATTRIBUTE_A]])

        node = ogm.fetch(instance_iri=A_STORED, class_scope=scope, materialize=True)

        assert node.data[HAS_ATTRIBUTE_A]
        assert UNDECLARED not in node.data


# ---------------------------------------------------------------------------
# A declared property that the scope leaves out: its own error
# ---------------------------------------------------------------------------

# The scope each case gives, the data, and what the error names: the instance, the property and
# the class. `hasAttributeAB` is a simple property of node A and B, `hasComplexAttribute` a
# complex property of node B, `hasNodeB` an object property whose nested node B carries the
# property the scope leaves out.
OUTSIDE_CASES = {
    "a simple property": (
        NODE_A,
        A_NEW,
        [[HAS_ATTRIBUTE_A]],
        {HAS_ATTRIBUTE_A: ["a"], HAS_ATTRIBUTE_AB: [1.0]},
        (A_NEW, HAS_ATTRIBUTE_AB, NODE_A),
        {HAS_ATTRIBUTE_A},
    ),
    "a complex property": (
        NODE_B,
        B_NEW,
        [[HAS_ATTRIBUTE_B]],
        {HAS_ATTRIBUTE_B: ["b"], HAS_COMPLEX_ATTRIBUTE: [{HAS_VALUE: ["v"]}]},
        (B_NEW, HAS_COMPLEX_ATTRIBUTE, NODE_B),
        {HAS_ATTRIBUTE_B},
    ),
    "a property of a nested node": (
        NODE_A,
        A_NEW,
        [[HAS_NODE_B, HAS_ATTRIBUTE_B]],
        {HAS_NODE_B: [{"id": B_NEW, HAS_ATTRIBUTE_B: ["b"], HAS_ATTRIBUTE_AB: [1.0]}]},
        (B_NEW, HAS_ATTRIBUTE_AB, NODE_B),
        {HAS_ATTRIBUTE_B},
    ),
}


def assert_outside_scope(error: PropertyOutsideScopeError, case: str) -> None:
    *_, expected, scope = OUTSIDE_CASES[case]
    assert_names(error, expected, plain=False)
    assert set(error.scope) == scope
    assert isinstance(error, OGMDataError)
    for name in (error.instance_iri, error.property_iri, error.class_iri):
        assert str(name) in str(error)


class TestPropertyOutsideScope:
    @pytest.mark.parametrize("case", OUTSIDE_CASES)
    def test_create_refuses(self, ogm, store, case) -> None:
        class_iri, instance_iri, chains, data, *_ = OUTSIDE_CASES[case]

        with pytest.raises(PropertyOutsideScopeError) as caught:
            ogm.create(
                class_iri=class_iri,
                instance_iri=instance_iri,
                data=data,
                class_scope=ClassScope.from_property_chains(chains),
            )

        assert_outside_scope(caught.value, case)
        assert_nothing_written(store)

    @pytest.mark.parametrize("case", OUTSIDE_CASES)
    def test_a_create_of_commit_refuses(self, ogm, store, case) -> None:
        class_iri, instance_iri, chains, data, *_ = OUTSIDE_CASES[case]

        with pytest.raises(PropertyOutsideScopeError) as caught:
            ogm.commit(
                changes=[
                    Create(
                        class_iri=class_iri,
                        instance_iri=instance_iri,
                        data=data,
                        class_scope=ClassScope.from_property_chains(chains),
                    )
                ]
            )

        assert_outside_scope(caught.value, case)
        assert_nothing_written(store)

    def test_a_property_the_class_does_not_declare_keeps_its_own_error(
        self, ogm, store
    ) -> None:
        """Both kinds in one call: the undeclared property is the one that is wrong."""
        with pytest.raises(OGMDataError) as caught:
            ogm.create(
                class_iri=NODE_A,
                instance_iri=A_NEW,
                data={HAS_ATTRIBUTE_A: ["a"], HAS_ATTRIBUTE_AB: [1.0], UNDECLARED: ["x"]},
                class_scope=ClassScope.from_property_chains([[HAS_ATTRIBUTE_A]]),
            )

        assert not isinstance(caught.value, PropertyOutsideScopeError)
        assert "does not declare" in str(caught.value)
        assert caught.value.property_iri == UNDECLARED
        assert_nothing_written(store)

    def test_the_class_properties_are_read_only_when_a_property_is_refused(
        self, ogm, store
    ) -> None:
        scope = ClassScope.from_property_chains([[HAS_ATTRIBUTE_A]])
        ogm.create(
            class_iri=NODE_A,
            instance_iri=A_NEW,
            data={HAS_ATTRIBUTE_A: ["a"]},
            class_scope=scope,
        )
        reads_of_a_write = store.query.call_count
        store.query.reset_mock()

        ogm.create(
            class_iri=NODE_A,
            instance_iri=A_NEW,
            data={HAS_ATTRIBUTE_A: ["a"]},
            class_scope=scope,
        )
        assert store.query.call_count == reads_of_a_write

        store.query.reset_mock()
        with pytest.raises(PropertyOutsideScopeError):
            ogm.create(
                class_iri=NODE_A,
                instance_iri=A_NEW,
                data={HAS_ATTRIBUTE_A: ["a"], HAS_ATTRIBUTE_AB: [1.0]},
                class_scope=scope,
            )
        assert store.query.call_count == reads_of_a_write + 1

    def test_a_commit_within_its_scope_writes(self, ogm, store) -> None:
        ogm.commit(
            changes=[
                Create(
                    class_iri=NODE_A,
                    instance_iri=A_NEW,
                    data={HAS_ATTRIBUTE_A: ["a"]},
                    class_scope=ClassScope.from_property_chains(
                        [[HAS_ATTRIBUTE_A], [HAS_ATTRIBUTE_AB]]
                    ),
                )
            ]
        )

        assert store.triples_add.called or store.triples_update.called

    def test_an_update_with_a_declared_property_writes(self, ogm, store) -> None:
        """`Update` takes no scope, it derives one from its data: a declared property is in
        it, so the case cannot occur."""
        ogm.commit(
            changes=[Update(instance_iri=A_STORED, data={HAS_ATTRIBUTE_AB: [2.0]})]
        )

        assert store.triples_add.called or store.triples_update.called
