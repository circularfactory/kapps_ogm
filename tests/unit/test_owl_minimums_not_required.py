"""OWL restrictions give a field the type of its values and their maximum, never a minimum.

Under the Open World Assumption a missing triple is unknown, not false. A parameter whose range
says `owl:someValuesFrom xsd:float` can legally carry no value yet, for example while the device
that publishes it has not sent one. So no OWL restriction makes a generated field required or
gives it a minimum length. `NodeValidator` still reports a shortfall below an OWL minimum as a
warning. The maximum stays enforced: a write over it is refused before it is sent.

The first classes check the generated field on hand-built specs, in the shape that
`PropertySpec._specify_complex_property` records for each restriction. The live class reads each
restriction from a real store and goes through `OGM.create` and `OGM.commit`.
"""

import uuid
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from rdflib import BNode, Literal, XSD

from kapps_triplestore_interface import IRI

from kapps_ogm.mapping.class_spec import ClassHydrationLevel, ClassSpec
from kapps_ogm.mapping.property_spec import PropertySpec, PropertyValueKind
from kapps_ogm.ogm import OGM
from kapps_ogm.utils.class_scope import ClassScope

NS = "https://example.org/owlminimums#"
HAS_VALUE = IRI(NS + "hasValue")

LITERAL = PropertyValueKind.LITERAL

# How `_specify_complex_property` records each restriction on the value property. A cardinality
# arrives with its own allValuesFrom here, as when an intersection merges two restrictions.
RESTRICTIONS = {
    "someValuesFrom": dict(value_kind=LITERAL, some_from=float, min_count=1),
    "allValuesFrom": dict(value_kind=LITERAL, python_range_type=float),
    "minCardinality": dict(value_kind=LITERAL, python_range_type=float, min_count=2),
    "cardinality": dict(
        value_kind=LITERAL, python_range_type=float, min_count=2, max_count=2
    ),
    "cardinality 1": dict(
        value_kind=LITERAL, python_range_type=float, min_count=1, max_count=1
    ),
}


def value_spec(form: str) -> PropertySpec:
    return PropertySpec(iri=HAS_VALUE, **RESTRICTIONS[form])


def parameter_model(spec: PropertySpec):
    """The pydantic model of an anonymous parameter node that carries only ``spec``."""
    return ClassSpec(
        iri=None,
        properties={spec.iri: spec},
        hydration_level=ClassHydrationLevel.FULL,
    ).to_pydantic_model()


class TestNoFieldIsRequiredByOwl:
    """A minimum from OWL makes no field required and gives no list a minimum length."""

    @pytest.mark.parametrize("form", RESTRICTIONS)
    def test_the_field_is_not_required(self, form: str):
        spec = value_spec(form)
        _, field = spec.to_pydantic_field()

        assert spec.required is False
        assert not field.is_required()

    @pytest.mark.parametrize("form", RESTRICTIONS)
    def test_a_node_without_the_property_validates(self, form: str):
        """An empty list, for a single-valued field too."""
        model = parameter_model(value_spec(form)).model_validate({})

        assert getattr(model, HAS_VALUE.lined) == []

    @pytest.mark.parametrize("form", RESTRICTIONS)
    def test_an_empty_list_validates(self, form: str):
        model = parameter_model(value_spec(form)).model_validate({HAS_VALUE.lined: []})

        assert getattr(model, HAS_VALUE.lined) == []

    def test_the_lower_bound_is_kept_on_the_spec(self):
        """The bound is not lost: `NodeValidator` reads it to warn about a shortfall."""
        assert value_spec("minCardinality").min_count == 2


class TestTypeAndMaximumStay:
    """What OWL does constrain is still enforced."""

    def test_some_values_from_still_types_the_values(self):
        model = parameter_model(value_spec("someValuesFrom"))

        validated = model.model_validate({HAS_VALUE.lined: [1.5]})

        assert getattr(validated, HAS_VALUE.lined) == [1.5]
        with pytest.raises(ValidationError, match="at least one value of type"):
            model.model_validate({HAS_VALUE.lined: ["fast"]})

    def test_a_value_over_the_maximum_is_refused(self):
        model = parameter_model(value_spec("cardinality"))

        with pytest.raises(ValidationError, match="too_long|at most 2"):
            model.model_validate({HAS_VALUE.lined: [1.0, 2.0, 3.0]})


# ---------------------------------------------------------------------------
# create refuses a value over the maximum before it sends anything
# ---------------------------------------------------------------------------

BELT = IRI(NS + "Belt")
HAS_PAIR = IRI(NS + "hasPair")
HAS_SETPOINT = IRI(NS + "hasSetpoint")


def belt_spec() -> ClassSpec:
    """A belt with a list of at most two values and a functional, single-valued setpoint."""
    return ClassSpec(
        iri=BELT,
        properties={
            HAS_PAIR: PropertySpec(
                iri=HAS_PAIR, value_kind=LITERAL, python_range_type=float, max_count=2
            ),
            HAS_SETPOINT: PropertySpec(
                iri=HAS_SETPOINT,
                value_kind=LITERAL,
                python_range_type=float,
                max_count=1,
            ),
        },
        hydration_level=ClassHydrationLevel.FULL,
    )


class TestCreateRefusesOverTheMaximum:
    @pytest.mark.parametrize(
        "data",
        [{HAS_PAIR: [1.0, 2.0, 3.0]}, {HAS_SETPOINT: [1.0, 2.0]}],
        ids=["over maxCardinality", "second value of a functional property"],
    )
    def test_nothing_is_sent(self, ogm_with_mock_db: OGM, mock_db, data: dict):
        with patch.object(ogm_with_mock_db, "get_class_spec", return_value=belt_spec()):
            with pytest.raises(ValidationError):
                ogm_with_mock_db.create(
                    class_iri=BELT, instance_iri=IRI(NS + "belt1"), data=data
                )

        for write in ("triples_add", "triples_update", "triples_delete", "query"):
            getattr(mock_db, write).assert_not_called()


# ---------------------------------------------------------------------------
# Each restriction read from a store
# ---------------------------------------------------------------------------


def restriction_range(subject: IRI, on_property: IRI, *facets) -> list[tuple]:
    """``subject rdfs:range [ owl:intersectionOf ( <one restriction per facet> ) ]``.

    Each facet is a ``(predicate, object)`` pair on its own ``owl:Restriction``, which is how an
    ontology states a type and a cardinality for one property. Add the triples in one call, so
    that each blank node label resolves to one node, and with ``check_exist=False``: the
    existence check groups the triples by blank node and cannot merge two groups.
    """
    owl = "http://www.w3.org/2002/07/owl#"
    rdf = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    range_node, cells = BNode(), [BNode() for _ in facets] + [IRI(rdf + "nil")]
    triples = [
        (subject, IRI("rdfs:range"), range_node),
        (range_node, IRI(owl + "intersectionOf"), cells[0]),
    ]
    for i, (predicate, target) in enumerate(facets):
        restriction = BNode()
        triples += [
            (restriction, IRI("rdf:type"), IRI(owl + "Restriction")),
            (restriction, IRI(owl + "onProperty"), on_property),
            (restriction, IRI(owl + predicate), target),
            (cells[i], IRI(rdf + "first"), restriction),
            (cells[i], IRI(rdf + "rest"), cells[i + 1]),
        ]
    return triples


def count(n: int) -> Literal:
    return Literal(n, datatype=XSD.nonNegativeInteger)


XSD_FLOAT = IRI("http://www.w3.org/2001/XMLSchema#float")

# One object property of the belt per restriction form. Its range constrains the value property
# of an anonymous parameter node, as the TransferUnit ontology does for a belt's speed.
LIVE_FORMS = {
    "someValuesFrom": [("someValuesFrom", XSD_FLOAT)],
    "allValuesFrom": [("allValuesFrom", XSD_FLOAT)],
    "minCardinality": [("allValuesFrom", XSD_FLOAT), ("minCardinality", count(1))],
    "cardinality": [("allValuesFrom", XSD_FLOAT), ("cardinality", count(1))],
}


@pytest.fixture(scope="module")
def ontology(db):
    """A belt class with one parameter property per form, in a namespace no other test uses."""
    ns = f"https://example.org/owlminimums_{uuid.uuid4().hex[:8]}#"
    belt, value = IRI(ns + "Belt"), IRI(ns + "hasValue")
    triples = [
        (belt, IRI("rdf:type"), IRI("owl:Class")),
        (value, IRI("rdf:type"), IRI("owl:DatatypeProperty")),
    ]
    parameters = {}
    for form, facets in LIVE_FORMS.items():
        parameter = IRI(ns + "has" + form.replace(" ", ""))
        parameters[form] = parameter
        triples += [
            (parameter, IRI("rdf:type"), IRI("owl:ObjectProperty")),
            (parameter, IRI("rdfs:domain"), belt),
        ] + restriction_range(parameter, value, *facets)

    pair, setpoint = IRI(ns + "hasPair"), IRI(ns + "hasSetpoint")
    triples += [
        (pair, IRI("rdf:type"), IRI("owl:ObjectProperty")),
        (pair, IRI("rdfs:domain"), belt),
        *restriction_range(
            pair, value, ("allValuesFrom", XSD_FLOAT), ("maxCardinality", count(2))
        ),
        (setpoint, IRI("rdf:type"), IRI("owl:DatatypeProperty")),
        (setpoint, IRI("rdf:type"), IRI("owl:FunctionalProperty")),
        (setpoint, IRI("rdfs:domain"), belt),
        (setpoint, IRI("rdfs:range"), XSD_FLOAT),
    ]
    assert db.triples_add(triples, check_exist=False)
    return dict(
        ns=ns,
        belt=belt,
        value=value,
        parameters=parameters,
        pair=pair,
        setpoint=setpoint,
    )


class TestEachRestrictionReadFromAStore:
    """The reported failure: an instance with a parameter that has no value yet."""

    @pytest.mark.parametrize("form", LIVE_FORMS)
    def test_create_a_parameter_without_a_value(self, ogm: OGM, ontology, form: str):
        parameter, value = ontology["parameters"][form], ontology["value"]

        node = ogm.create(
            class_iri=ontology["belt"],
            data={parameter: [{}]},
            class_scope=ClassScope.from_property_chains([[parameter, value]]),
            persist=False,
        )

        stored = getattr(node.materialize(), parameter.lined)
        assert [getattr(item, value.lined) for item in stored] == [[]]

    def test_commit_refuses_values_over_the_maximum_and_writes_nothing(
        self, ogm: OGM, db, ontology
    ):
        pair, value, setpoint = (
            ontology["pair"],
            ontology["value"],
            ontology["setpoint"],
        )
        scope = ClassScope.from_property_chains([[pair, value], [setpoint]])
        belt = IRI(ontology["ns"] + f"belt_{uuid.uuid4().hex[:8]}")
        ogm.create(
            class_iri=ontology["belt"],
            instance_iri=belt,
            data={pair: [{value: [1.0, 2.0]}]},
            class_scope=scope,
        )
        before = sorted(map(str, db.triples_get(sub=belt)))

        with pytest.raises(ValidationError):
            ogm.commit(instance_iri=belt, data={pair: [{value: [1.0, 2.0, 3.0]}]})
        with pytest.raises(ValidationError):
            ogm.commit(instance_iri=belt, data={setpoint: [1.0, 2.0]})

        assert sorted(map(str, db.triples_get(sub=belt))) == before
