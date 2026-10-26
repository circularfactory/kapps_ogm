"""A class scope written for a superclass also covers a subclass added later.

The OGM derives the specification of a class per call from the store, with the properties of the
class's domains and of its superclasses (`rdfs:subClassOf*`), so a subclass declared after a scope
exists needs no change in the OGM or in the scope.

The test runs live. It loads `Device` with two properties, creates a device under the scope
`[[hasName], [hasPower]]`, then declares `Pump` as a subclass of `Device` and creates a pump
under the same scope. Both instances fetch with both properties, and the specification of
`Pump` lists the inherited properties. The negative control declares `Valve` without
`rdfs:subClassOf`: the class has neither property, and the class specification refuses the scope
with `OGMDataError`, which names the missing properties ("specified in class scope, but not
found as property of class"). `create` also refuses undeclared properties, but the scope check
comes first.
"""

import uuid

import pytest
from rdflib import XSD

from kapps_triplestore_interface import IRI, TripleStore

from kapps_ogm import OGMDataError
from kapps_ogm.mapping.class_spec import ClassHydrationLevel
from kapps_ogm.ogm import OGM
from kapps_ogm.utils.class_scope import ClassScope

RDF_TYPE = IRI("rdf:type")
XSD_STRING = IRI(str(XSD.string))
XSD_DOUBLE = IRI(str(XSD.double))


@pytest.fixture(scope="module")
def v() -> dict[str, IRI]:
    ns = f"http://example.org/superscope_{uuid.uuid4().hex[:8]}#"
    names = ["Device", "Pump", "Valve", "hasName", "hasPower"]
    return {name: IRI(ns + name) for name in names}


@pytest.fixture(scope="module")
def scope(v: dict[str, IRI]) -> ClassScope:
    return ClassScope.from_property_chains([[v["hasName"]], [v["hasPower"]]])


@pytest.fixture(scope="module")
def device_ontology(db: TripleStore, v: dict[str, IRI]) -> None:
    """`Device` with a string `hasName` and a functional double `hasPower`."""
    owl = "http://www.w3.org/2002/07/owl#"
    rdfs = "http://www.w3.org/2000/01/rdf-schema#"
    db.query(
        f"""
        PREFIX owl: <{owl}>
        PREFIX rdfs: <{rdfs}>
        INSERT DATA {{
            <{v["Device"]}> a owl:Class .
            <{v["hasName"]}> a owl:DatatypeProperty ;
                rdfs:domain <{v["Device"]}> ; rdfs:range <{XSD_STRING}> .
            <{v["hasPower"]}> a owl:DatatypeProperty, owl:FunctionalProperty ;
                rdfs:domain <{v["Device"]}> ; rdfs:range <{XSD_DOUBLE}> .
        }}
        """,
        update=True,
    )


def declare(db: TripleStore, subclass: IRI, superclass: IRI | None) -> None:
    owl = "http://www.w3.org/2002/07/owl#"
    rdfs = "http://www.w3.org/2000/01/rdf-schema#"
    link = f"<{subclass}> rdfs:subClassOf <{superclass}> ." if superclass else ""
    db.query(
        f"""
        PREFIX owl: <{owl}>
        PREFIX rdfs: <{rdfs}>
        INSERT DATA {{ <{subclass}> a owl:Class . {link} }}
        """,
        update=True,
    )


def instance(class_iri: IRI) -> IRI:
    return IRI(f"{class_iri}_{uuid.uuid4().hex[:8]}")


def test_a_superclass_scope_covers_a_subclass_declared_later(
    db: TripleStore,
    ogm: OGM,
    v: dict[str, IRI],
    scope: ClassScope,
    device_ontology: None,
) -> None:
    data = {v["hasName"]: ["unit"], v["hasPower"]: [1.5]}
    device = instance(v["Device"])
    ogm.create(class_iri=v["Device"], instance_iri=device, data=data, class_scope=scope)

    declare(db, v["Pump"], v["Device"])
    pump = instance(v["Pump"])
    ogm.create(class_iri=v["Pump"], instance_iri=pump, data=data, class_scope=scope)

    for iri, class_iri in ((device, v["Device"]), (pump, v["Pump"])):
        node = ogm.fetch(instance_iri=iri, class_scope=scope)
        assert node.class_spec.iri == class_iri
        assert node.data[v["hasName"]] == ["unit"]
        assert node.data[v["hasPower"]] == [1.5]

    spec = ogm.get_class_spec(
        class_iri=v["Pump"], hydration_level=ClassHydrationLevel.FULL
    )
    assert spec.iri == v["Pump"]
    assert {v["hasName"], v["hasPower"]} <= set(spec.properties)


def test_a_subclass_declared_without_subclassof_is_refused(
    db: TripleStore,
    ogm: OGM,
    v: dict[str, IRI],
    scope: ClassScope,
    device_ontology: None,
) -> None:
    """Negative control: `Valve` is no subclass of `Device`, so the scope names properties it
    does not have."""
    declare(db, v["Valve"], None)
    valve = instance(v["Valve"])

    with pytest.raises(OGMDataError, match="not found as property of class") as refusal:
        ogm.create(
            class_iri=v["Valve"],
            instance_iri=valve,
            data={v["hasName"]: ["x"], v["hasPower"]: [2.0]},
            class_scope=scope,
        )

    assert str(v["hasName"]) in str(refusal.value)
    assert not db.iri_exists(valve, as_sub=True)
