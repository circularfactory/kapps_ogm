"""Two reads of the OGM see explicit statements only: the data of a fetched anonymous node, and
the properties of a class specification.

A store infers statements from the ontology: the inverse of a property, the types that a
subclass and a restriction give a node. Those statements are not the node's own data, and a
write could not remove them. So the read of an anonymous node returns what was asserted about
it, and the property discovery of a class reads the asserted domains and superclasses.

The tests read through the OGM's public API, `get_class_spec` and `fetch`, on a small fixture in
a namespace no other test uses. The fixture has the shape that makes a store infer: an inverse
pair, a subclass, and a restriction on the class of the anonymous node.
"""

import uuid

import pytest
from rdflib import XSD

from kapps_triplestore_interface import IRI, TripleStore

from kapps_ogm.mapping.class_spec import ClassHydrationLevel
from kapps_ogm.ogm import OGM

from .test_owl_minimums_not_required import restriction_range

OWL = "http://www.w3.org/2002/07/owl#"
RDF_TYPE = IRI("rdf:type")
XSD_BOOLEAN = IRI(str(XSD.boolean))
XSD_STRING = IRI(str(XSD.string))


@pytest.fixture(scope="module")
def vocabulary() -> dict[str, IRI]:
    ns = f"http://example.org/explicitonly_{uuid.uuid4().hex[:8]}#"
    names = [
        "Box",
        "Crate",
        "Part",
        "Component",
        "holdsComponent",
        "isHeldBy",
        "isActive",
        "hasSerial",
        "hasTag",
    ]
    return {name: IRI(ns + name) for name in names}


@pytest.fixture(scope="module")
def ontology(db: TripleStore, vocabulary: dict[str, IRI]) -> dict[str, IRI]:
    """A box holds an anonymous component; a component is a part with a restriction.

    - `holdsComponent` has a restriction range, so the OGM reads it as a complex property, and
      `isHeldBy` is its inverse.
    - `Component` is a subclass of `Part` and of a restriction on `isActive`.
    - `hasSerial` has the domain `Part`, so a component inherits it.
    - `hasTag` has a union domain, `Box` or `Crate`.
    """
    v = vocabulary
    union = f"""
        PREFIX owl: <{OWL}>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        INSERT DATA {{
            <{v["hasTag"]}> a owl:DatatypeProperty ;
                rdfs:domain [ a owl:Class ; owl:unionOf ( <{v["Box"]}> <{v["Crate"]}> ) ] ;
                rdfs:range <{XSD_STRING}> .
        }}
    """
    restriction = f"""
        PREFIX owl: <{OWL}>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        INSERT DATA {{
            <{v["Component"]}> rdfs:subClassOf [
                a owl:Restriction ;
                owl:onProperty <{v["isActive"]}> ;
                owl:someValuesFrom <{XSD_BOOLEAN}>
            ] .
        }}
    """
    triples = [
        (v["Box"], RDF_TYPE, IRI("owl:Class")),
        (v["Crate"], RDF_TYPE, IRI("owl:Class")),
        (v["Part"], RDF_TYPE, IRI("owl:Class")),
        (v["Component"], RDF_TYPE, IRI("owl:Class")),
        (v["Component"], IRI("rdfs:subClassOf"), v["Part"]),
        (v["holdsComponent"], RDF_TYPE, IRI("owl:ObjectProperty")),
        (v["holdsComponent"], IRI("rdfs:domain"), v["Box"]),
        *restriction_range(
            v["holdsComponent"], v["isActive"], ("allValuesFrom", XSD_BOOLEAN)
        ),
        (v["isHeldBy"], RDF_TYPE, IRI("owl:ObjectProperty")),
        (v["isHeldBy"], IRI("owl:inverseOf"), v["holdsComponent"]),
        (v["isActive"], RDF_TYPE, IRI("owl:DatatypeProperty")),
        (v["isActive"], IRI("rdfs:domain"), v["Component"]),
        (v["isActive"], IRI("rdfs:range"), XSD_BOOLEAN),
        (v["hasSerial"], RDF_TYPE, IRI("owl:DatatypeProperty")),
        (v["hasSerial"], IRI("rdfs:domain"), v["Part"]),
        (v["hasSerial"], IRI("rdfs:range"), XSD_STRING),
    ]
    assert db.triples_add(triples, check_exist=False)
    db.query(union, update=True)
    db.query(restriction, update=True)
    return vocabulary


def properties_of(ogm: OGM, class_iri: IRI) -> set[IRI]:
    spec = ogm.get_class_spec(
        class_iri=class_iri, hydration_level=ClassHydrationLevel.FULL
    )
    return set(spec.properties)


class TestAnAnonymousNodeIsReadAsAsserted:
    @pytest.fixture
    def box(self, db: TripleStore, ontology: dict[str, IRI]) -> IRI:
        """A box of its own, which holds one anonymous component, which is active."""
        v = ontology
        box = IRI(f"{v['Box']}_{uuid.uuid4().hex[:8]}")
        db.query(
            f"""
            INSERT DATA {{
                <{box}> a <{v["Box"]}> ;
                    <{v["holdsComponent"]}> [
                        a <{v["Component"]}> ;
                        <{v["isActive"]}> true
                    ] .
            }}
            """,
            update=True,
        )
        return box

    def test_the_store_infers_more_about_the_node(
        self, db: TripleStore, ontology: dict[str, IRI], box: IRI
    ):
        """The fixture works: the store holds inferred statements about the node, so the read
        below has something to leave out."""
        v = ontology
        inferred = db.query(
            f"""
            ASK {{
                <{box}> <{v["holdsComponent"]}> ?node .
                ?node <{v["isHeldBy"]}> <{box}> ;
                    a <{v["Part"]}> .
            }}
            """
        )
        assert inferred["boolean"]

    def test_the_fetched_node_holds_its_two_asserted_statements(
        self, ogm: OGM, ontology: dict[str, IRI], box: IRI
    ):
        v = ontology
        spec = ogm.get_class_spec(
            class_iri=v["Box"], hydration_level=ClassHydrationLevel.FULL
        )

        node = ogm.fetch(instance_iri=box, class_spec=spec)

        [component] = node.data[v["holdsComponent"]]
        assert component.data == {RDF_TYPE: [v["Component"]], v["isActive"]: [True]}


class TestThePropertiesOfAClass:
    def test_a_class_has_the_properties_of_its_domain_and_its_superclass(
        self, ogm: OGM, ontology: dict[str, IRI]
    ):
        v = ontology

        assert properties_of(ogm, v["Component"]) == {v["isActive"], v["hasSerial"]}

    def test_a_superclass_does_not_have_the_properties_of_its_subclass(
        self, ogm: OGM, ontology: dict[str, IRI]
    ):
        v = ontology

        assert properties_of(ogm, v["Part"]) == {v["hasSerial"]}

    @pytest.mark.parametrize("member", ["Box", "Crate"])
    def test_each_member_of_a_union_domain_has_the_property(
        self, ogm: OGM, ontology: dict[str, IRI], member: str
    ):
        v = ontology

        assert v["hasTag"] in properties_of(ogm, v[member])
