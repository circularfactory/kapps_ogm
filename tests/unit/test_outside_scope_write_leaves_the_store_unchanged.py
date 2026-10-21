"""Live tests: a write that leaves a declared property out of its scope is refused, and the store keeps what it held.

A class declares two properties, ``name`` and ``colour``. Each case gives a class scope that names
``name`` only and data that also names ``colour``, which the class declares and the scope leaves
out. The call raises ``PropertyOutsideScopeError`` before anything is sent. The statements about
the instance are the same afterwards. A control writes the same data under a scope that names both
and the store takes it, so the refusal is what keeps the store unchanged.
"""

import uuid

import pytest

from kapps_triplestore_interface import IRI, TripleStore

from kapps_ogm import Create, Update
from kapps_ogm.ogm import OGM
from kapps_ogm.utils.class_scope import ClassScope
from kapps_ogm.utils.errors import PropertyOutsideScopeError

NS = "https://example.org/outsidescope#"
SENSOR = IRI(NS + "Sensor")
NAME = IRI(NS + "name")
COLOUR = IRI(NS + "colour")

RDF_TYPE = IRI("rdf:type")
G = IRI("http://example.org/named_graph")

ONTOLOGY = [
    (SENSOR, RDF_TYPE, IRI("owl:Class")),
    (NAME, RDF_TYPE, IRI("owl:DatatypeProperty")),
    (NAME, IRI("rdfs:domain"), SENSOR),
    (NAME, IRI("rdfs:range"), IRI("xsd:string")),
    (COLOUR, RDF_TYPE, IRI("owl:DatatypeProperty")),
    (COLOUR, IRI("rdfs:domain"), SENSOR),
    (COLOUR, IRI("rdfs:range"), IRI("xsd:string")),
]

NARROW = ClassScope.from_property_chains([[NAME]])
WIDE = ClassScope.from_property_chains([[NAME], [COLOUR]])


def fresh(name: str) -> IRI:
    """An instance IRI no other test uses, because the live store is wiped only per session."""
    return IRI(f"{NS}{name}_{uuid.uuid4().hex[:8]}")


@pytest.fixture
def live_ogm(db: TripleStore) -> OGM:
    db.triples_add(ONTOLOGY, check_exist=False)
    return OGM(db=db)


def explicit(db: TripleStore, subject: IRI) -> set:
    """The explicit statements about ``subject`` in the test graph, as (predicate, value)."""
    rows = db.query(
        f"SELECT ?p ?o WHERE {{ GRAPH <{G}> {{ <{subject}> ?p ?o }} }}",
        convert_bindings=True,
    )["results"]["bindings"]
    return {(str(row["p"]), str(row["o"])) for row in rows}


def sensor(ogm: OGM, name: str) -> IRI:
    iri = fresh("sensor")
    ogm.create(
        class_iri=SENSOR, instance_iri=iri, data={NAME: [name]}, named_graph=G
    )
    return iri


class TestARefusedCreate:
    def test_leaves_no_statement_about_the_instance(self, db, live_ogm):
        iri = fresh("sensor")

        with pytest.raises(PropertyOutsideScopeError) as caught:
            live_ogm.create(
                class_iri=SENSOR,
                instance_iri=iri,
                data={NAME: ["s"], COLOUR: ["red"]},
                class_scope=NARROW,
                named_graph=G,
            )

        assert caught.value.property_iri == COLOUR
        assert explicit(db, iri) == set()

    def test_the_same_data_under_a_scope_that_names_it_is_written(self, db, live_ogm):
        iri = fresh("sensor")

        live_ogm.create(
            class_iri=SENSOR,
            instance_iri=iri,
            data={NAME: ["s"], COLOUR: ["red"]},
            class_scope=WIDE,
            named_graph=G,
        )

        assert (str(COLOUR), "red") in explicit(db, iri)


class TestARefusedCommit:
    def test_a_create_leaves_no_statement_and_the_other_change_is_not_applied(
        self, db, live_ogm
    ):
        stored = sensor(live_ogm, "s")
        before = explicit(db, stored)
        new = fresh("sensor")

        with pytest.raises(PropertyOutsideScopeError) as caught:
            live_ogm.commit(
                changes=[
                    Update(instance_iri=stored, data={NAME: ["t"]}),
                    Create(
                        class_iri=SENSOR,
                        instance_iri=new,
                        data={NAME: ["u"], COLOUR: ["red"]},
                        class_scope=NARROW,
                    ),
                ],
                named_graph=G,
            )

        assert caught.value.instance_iri == new
        assert explicit(db, stored) == before
        assert explicit(db, new) == set()

    def test_the_same_commit_under_a_scope_that_names_it_is_written(
        self, db, live_ogm
    ):
        new = fresh("sensor")

        live_ogm.commit(
            changes=[
                Create(
                    class_iri=SENSOR,
                    instance_iri=new,
                    data={NAME: ["u"], COLOUR: ["red"]},
                    class_scope=WIDE,
                )
            ],
            named_graph=G,
        )

        assert (str(COLOUR), "red") in explicit(db, new)
