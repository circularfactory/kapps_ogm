"""A custom-typed literal makes the round trip through a live store: create, fetch, commit.

The datatype is named as an example of a datatype outside XSD, as a ProbSPARQL-aware store reads
a mixture; it is not a model of a mixture. The store infers `rdfs:Class` for every range object,
so this is the case where the datatype check has to come before the class check.
"""

from pathlib import Path

from rdflib import Graph, Literal, URIRef

from kapps_triplestore_interface import IRI

from kapps_ogm import Update
from kapps_ogm.ogm import OGM
from kapps_ogm.utils.class_scope import ClassScope

ONTOLOGY = Path(__file__).parent.parent / "test_data" / "datatype_range.ttl"
NS = "https://example.org/ex#"
SERVICE = IRI(NS + "Service")
HAS_MIXTURE = IRI(NS + "hasMixture")
GAUSSIAN_MIXTURE = IRI(NS + "GaussianMixture")
INSTANCE = IRI("https://example.org/i/service1")


def ontology_triples() -> list:
    """The ontology of the stand-in, less the stored instance."""
    graph = Graph()
    graph.parse(ONTOLOGY, format="turtle")
    return [
        (IRI(str(s)), IRI(str(p)), IRI(str(o)))
        for s, p, o in graph
        if isinstance(o, URIRef) and str(s) != str(INSTANCE)
    ]


def mixture(text: str) -> Literal:
    return Literal(text, datatype=URIRef(GAUSSIAN_MIXTURE))


def test_a_custom_typed_literal_round_trips_with_its_datatype(ogm: OGM):
    ogm.db.triples_add(ontology_triples(), check_exist=False)
    scope = ClassScope.from_property_chains([[HAS_MIXTURE]])

    ogm.create(
        class_iri=SERVICE,
        instance_iri=INSTANCE,
        data={HAS_MIXTURE: [mixture("[0.3, 0.7]")]},
        class_scope=scope,
    )
    stored = ogm.db.query(
        f"ASK {{ <{INSTANCE}> <{HAS_MIXTURE}> ?o . FILTER (datatype(?o) = <{GAUSSIAN_MIXTURE}>) }}"
    )
    assert stored["boolean"] is True
    fetched = ogm.fetch(instance_iri=INSTANCE, class_scope=scope, materialize=True)
    assert fetched.data[HAS_MIXTURE] == [mixture("[0.3, 0.7]")]
    assert fetched.data[HAS_MIXTURE][0].datatype == URIRef(GAUSSIAN_MIXTURE)

    ogm.commit(
        changes=[
            Update(
                instance_iri=INSTANCE, data={str(HAS_MIXTURE): [mixture("[0.1, 0.9]")]}
            )
        ]
    )
    refetched = ogm.fetch(instance_iri=INSTANCE, class_scope=scope, materialize=True)
    assert refetched.data[HAS_MIXTURE] == [mixture("[0.1, 0.9]")]
