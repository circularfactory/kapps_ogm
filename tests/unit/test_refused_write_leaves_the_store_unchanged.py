"""Live tests: a write that names an undeclared property is refused, and the store keeps what it held.

A class declares one property. Each case writes data that names a second one, which the class does
not declare, and the call raises ``OGMDataError`` before anything is sent. The statements about the
instance, and about every instance in the call, are the same afterwards. A control writes the same
data without the undeclared property and the store takes it, so the refusal is what keeps the
store unchanged and not a store that refuses everything.
"""

import uuid

import pytest

from kapps_triplestore_interface import IRI, TripleStore

from kapps_ogm import Create, Update
from kapps_ogm.ogm import OGM
from kapps_ogm.utils.errors import OGMDataError

NS = "http://example.org/refusedwrite#"
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
]


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

        with pytest.raises(OGMDataError) as caught:
            live_ogm.create(
                class_iri=SENSOR,
                instance_iri=iri,
                data={NAME: ["s"], COLOUR: ["red"]},
                named_graph=G,
            )

        assert caught.value.property_iri == COLOUR
        assert explicit(db, iri) == set()

    def test_the_same_data_without_the_property_is_written(self, db, live_ogm):
        iri = sensor(live_ogm, "s")

        assert explicit(db, iri) != set()


class TestARefusedCommit:
    def test_an_update_leaves_the_stored_statements_unchanged(self, db, live_ogm):
        iri = sensor(live_ogm, "s")
        before = explicit(db, iri)

        with pytest.raises(OGMDataError) as caught:
            live_ogm.commit(
                changes=[Update(instance_iri=iri, data={NAME: ["t"], COLOUR: ["red"]})],
                named_graph=G,
            )

        assert caught.value.instance_iri == iri
        assert caught.value.property_iri == COLOUR
        assert explicit(db, iri) == before

    def test_a_create_leaves_no_statement_and_the_other_change_is_not_applied(
        self, db, live_ogm
    ):
        stored = sensor(live_ogm, "s")
        before = explicit(db, stored)
        new = fresh("sensor")

        with pytest.raises(OGMDataError):
            live_ogm.commit(
                changes=[
                    Update(instance_iri=stored, data={NAME: ["t"]}),
                    Create(
                        class_iri=SENSOR,
                        instance_iri=new,
                        data={NAME: ["u"], COLOUR: ["red"]},
                    ),
                ],
                named_graph=G,
            )

        assert explicit(db, stored) == before
        assert explicit(db, new) == set()

    def test_the_same_update_without_the_property_is_written(self, db, live_ogm):
        iri = sensor(live_ogm, "s")
        before = explicit(db, iri)

        live_ogm.commit(
            changes=[Update(instance_iri=iri, data={NAME: ["t"]})], named_graph=G
        )

        assert explicit(db, iri) != before
