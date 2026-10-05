"""Live tests: ``OGM.create`` and ``OGM.commit`` return only for a write that the store made.

Each case puts an instance into a named graph and then makes a write whose precondition is false
there. The call raises ``PreconditionFailedError`` from the triple store interface, and the store
keeps what it held. The cases are the ones the store refused silently before: a second create of
one instance, a create that writes about an instance it only links, a commit into a graph that
does not hold the instance, a commit whose read is stale when its write arrives, and a commit that
removes a value the store only infers.
"""

import uuid

import pytest

from kapps_triplestore_interface import GraphDB, IRI
from kapps_triplestore_interface.exceptions import PreconditionFailedError

from kapps_ogm.ogm import OGM
from kapps_ogm.utils.class_scope import ClassScope

NS = "https://example.org/truth#"
MEASUREMENT = IRI(NS + "Measurement")
WORKPIECE = IRI(NS + "Workpiece")
METER = IRI(NS + "Meter")
VALUE = IRI(NS + "value")
MEASURED_ON = IRI(NS + "measuredOn")
NAME = IRI(NS + "name")
Q = IRI(NS + "q")
P1 = IRI(NS + "p1")

RDF_TYPE = IRI("rdf:type")
RDFS_DOMAIN = IRI("rdfs:domain")
RDFS_RANGE = IRI("rdfs:range")

# Both graphs are cleared by the ``db`` fixture at the start of a session.
G1 = IRI("http://example.org/named_graph")
G2 = IRI("http://example.org/local_named_graph")

ONTOLOGY = [
    (MEASUREMENT, RDF_TYPE, IRI("owl:Class")),
    (WORKPIECE, RDF_TYPE, IRI("owl:Class")),
    (METER, RDF_TYPE, IRI("owl:Class")),
    (VALUE, RDF_TYPE, IRI("owl:DatatypeProperty")),
    (VALUE, RDFS_DOMAIN, MEASUREMENT),
    (VALUE, RDFS_RANGE, IRI("xsd:double")),
    (MEASURED_ON, RDF_TYPE, IRI("owl:ObjectProperty")),
    (MEASURED_ON, RDFS_DOMAIN, MEASUREMENT),
    (MEASURED_ON, RDFS_RANGE, WORKPIECE),
    (NAME, RDF_TYPE, IRI("owl:DatatypeProperty")),
    (NAME, RDFS_DOMAIN, WORKPIECE),
    (NAME, RDFS_RANGE, IRI("xsd:string")),
    # A value of p1 is also a value of q, which the store infers. The class names only q.
    (Q, RDF_TYPE, IRI("owl:DatatypeProperty")),
    (Q, RDFS_DOMAIN, METER),
    (Q, RDFS_RANGE, IRI("xsd:double")),
    (P1, RDF_TYPE, IRI("owl:DatatypeProperty")),
    (P1, IRI("rdfs:subPropertyOf"), Q),
]

WITH_NAME = ClassScope.from_property_chains([[VALUE], [MEASURED_ON, NAME]])


def fresh(name: str) -> IRI:
    """An instance IRI no other test uses, because the live store is wiped only per session."""
    return IRI(f"{NS}{name}_{uuid.uuid4().hex[:8]}")


@pytest.fixture
def live_ogm(db: GraphDB) -> OGM:
    db.triples_add(ONTOLOGY, check_exist=False)
    return OGM(db=db)


def explicit(db: GraphDB, subject: IRI, graph: IRI) -> set:
    """The explicit triples with ``subject`` in ``graph``, as (predicate, value) strings."""
    rows = db.query(
        f"SELECT ?p ?o WHERE {{ GRAPH <{graph}> {{ <{subject}> ?p ?o }} }}",
        convert_bindings=True,
    )["results"]["bindings"]
    return {(str(row["p"]), str(row["o"])) for row in rows}


def values(db: GraphDB, subject: IRI, predicate: IRI, graph: IRI) -> set:
    return {float(o) for p, o in explicit(db, subject, graph) if p == str(predicate)}


def measurement(ogm: OGM, value: float, graph: IRI = G1, **kwargs) -> IRI:
    iri = kwargs.pop("instance_iri", None) or fresh("m")
    ogm.create(
        class_iri=MEASUREMENT,
        instance_iri=iri,
        data={str(VALUE): [value], **kwargs.pop("data", {})},
        class_scope=kwargs.pop("class_scope", WITH_NAME),
        named_graph=graph,
    )
    return iri


def workpiece(ogm: OGM, name: str) -> IRI:
    iri = fresh("obj")
    ogm.create(
        class_iri=WORKPIECE,
        instance_iri=iri,
        data={str(NAME): [name]},
        named_graph=G1,
    )
    return iri


class TestCreate:
    def test_a_second_create_of_one_instance_raises_and_the_store_keeps_its_value(
        self, db, live_ogm
    ):
        m = measurement(live_ogm, 1.25)

        with pytest.raises(PreconditionFailedError):
            measurement(live_ogm, 7.0, instance_iri=m)

        assert values(db, m, VALUE, G1) == {1.25}

    def test_the_same_create_twice_raises_the_second_time(self, db, live_ogm):
        m = measurement(live_ogm, 1.25)
        before = explicit(db, m, G1)

        with pytest.raises(PreconditionFailedError):
            measurement(live_ogm, 1.25, instance_iri=m)

        assert explicit(db, m, G1) == before

    def test_two_measurements_with_equal_values_on_two_objects_are_both_created(
        self, db, live_ogm
    ):
        obj1, obj2 = workpiece(live_ogm, "Housing 1"), workpiece(live_ogm, "Housing 2")

        m1 = measurement(live_ogm, 1.5, data={str(MEASURED_ON): [{"id": str(obj1)}]})
        m2 = measurement(live_ogm, 1.5, data={str(MEASURED_ON): [{"id": str(obj2)}]})

        assert (str(MEASURED_ON), str(obj1)) in explicit(db, m1, G1)
        assert (str(MEASURED_ON), str(obj2)) in explicit(db, m2, G1)
        assert values(db, m1, VALUE, G1) == values(db, m2, VALUE, G1) == {1.5}

    def test_a_measurement_on_an_existing_hydrated_object_leaves_the_object_alone(
        self, db, live_ogm
    ):
        obj2 = workpiece(live_ogm, "Housing 2")
        before = explicit(db, obj2, G1)

        m2 = measurement(live_ogm, 1.5, data={str(MEASURED_ON): [{"id": str(obj2)}]})

        assert (str(MEASURED_ON), str(obj2)) in explicit(db, m2, G1)
        assert explicit(db, obj2, G1) == before

    def test_a_nested_instance_with_properties_that_exists_raises(self, db, live_ogm):
        obj2 = workpiece(live_ogm, "Housing 2")
        before = explicit(db, obj2, G1)
        m3 = fresh("m")

        with pytest.raises(PreconditionFailedError):
            measurement(
                live_ogm,
                1.5,
                instance_iri=m3,
                data={str(MEASURED_ON): [{"id": str(obj2), str(NAME): ["Renamed"]}]},
            )

        assert explicit(db, m3, G1) == set()
        assert explicit(db, obj2, G1) == before


class TestCommit:
    def test_a_commit_into_a_graph_that_does_not_hold_the_instance_raises(
        self, db, live_ogm
    ):
        m = measurement(live_ogm, 1.25)

        with pytest.raises(PreconditionFailedError):
            live_ogm.commit(instance_iri=m, data={str(VALUE): [1.5]}, named_graph=G2)

        assert values(db, m, VALUE, G1) == {1.25}
        assert explicit(db, m, G2) == set()

    def test_a_value_another_writer_changed_after_the_read_raises(
        self, db, live_ogm, monkeypatch
    ):
        """Another writer changes the value between the commit's own read and its write. The
        commit would remove 1.25, which is gone, so it raises, and the other write stands.
        """
        m = measurement(live_ogm, 1.25)
        write = db.triples_update

        def another_writer_first(*args, **kwargs):
            db.query(
                f"WITH <{G1}> DELETE {{ <{m}> <{VALUE}> ?v }} "
                f'INSERT {{ <{m}> <{VALUE}> "9.99"^^<http://www.w3.org/2001/XMLSchema#double> }} '
                f"WHERE {{ <{m}> <{VALUE}> ?v }}",
                update=True,
            )
            return write(*args, **kwargs)

        monkeypatch.setattr(db, "triples_update", another_writer_first)

        with pytest.raises(PreconditionFailedError):
            live_ogm.commit(instance_iri=m, data={str(VALUE): [1.5]}, named_graph=G1)

        assert values(db, m, VALUE, G1) == {9.99}

    def test_a_value_the_store_only_infers_raises_and_is_named(self, db, live_ogm):
        """The read includes inferred triples, the delete cannot remove one. ``m1 q 1.5`` is
        inferred from ``m1 p1 1.5``, so a commit that replaces q's value raises."""
        m1 = fresh("meter")
        db.triples_add(
            [(m1, RDF_TYPE, METER), (m1, P1, 1.5)], named_graph=G1, check_exist=False
        )

        with pytest.raises(PreconditionFailedError) as caught:
            live_ogm.commit(instance_iri=m1, data={str(Q): [2.0]}, named_graph=G1)

        assert (str(m1), str(Q), "1.5") in {
            (str(s), str(p), str(o)) for s, p, o in caught.value.triples
        }
        assert values(db, m1, P1, G1) == {1.5}
        assert values(db, m1, Q, G1) == set()
