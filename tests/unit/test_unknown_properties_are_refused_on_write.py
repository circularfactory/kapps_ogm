"""Pin the contract: a write refuses a property its class does not declare, a read ignores it.

Both write entry points, ``OGM.create`` and ``OGM.commit``, refuse data that names a property the
class does not declare, here or in a nested value. They raise ``OGMDataError`` that names the
instance, the property and the class, before any triple is written. A read tolerates undeclared
statements: under the open world a graph carries more than any schema declares, so the model of a
class ignores an undeclared key and materializing a fetched node only warns about it.

These tests run on a mocked store with the spec of the contract, ``TripleStore``.
"""

import logging
from unittest.mock import Mock, patch

import pytest

from kapps_triplestore_interface import IRI, TripleStore

from kapps_ogm import OGM, OGMDataError, Create, Update
from kapps_ogm.mapping.class_spec import ClassHydrationLevel, ClassSpec
from kapps_ogm.mapping.property_spec import PropertySpec, PropertyValueKind
from kapps_ogm.node.core import Node
from kapps_ogm.utils import mint_skolem_iri

ONTO = "http://www.sfb1574.kit.edu/ontologies/TransferUnit"
HAS_UNIT = IRI("hasUnit", ONTO)

NS = "http://example.org/unknownproperties#"
BELT = IRI(NS + "Belt")
BELT1 = IRI(NS + "belt1")
MOTOR = IRI(NS + "Motor")
MOTOR1 = IRI(NS + "motor1")
HAS_SPEED = IRI(NS + "hasSpeed")
HAS_MOTOR = IRI(NS + "hasMotor")
HAS_COLOUR = IRI(NS + "hasColour")
HAS_PARAMETER = IRI(NS + "hasParameter")
HAS_VALUE = IRI(NS + "hasValue")
HAS_TOPIC = IRI(NS + "hasTopic")

WRITES = ("triples_add", "triples_update", "triples_delete")


def motor_spec() -> ClassSpec:
    return ClassSpec(
        iri=MOTOR,
        properties={
            HAS_SPEED: PropertySpec(
                iri=HAS_SPEED,
                value_kind=PropertyValueKind.LITERAL,
                python_range_type=float,
            )
        },
        hydration_level=ClassHydrationLevel.SCOPE,
    )


def belt_spec() -> ClassSpec:
    """A belt with a literal speed and a motor, which has a literal speed of its own."""
    return ClassSpec(
        iri=BELT,
        properties={
            HAS_SPEED: PropertySpec(
                iri=HAS_SPEED,
                value_kind=PropertyValueKind.LITERAL,
                python_range_type=float,
            ),
            HAS_MOTOR: PropertySpec(
                iri=HAS_MOTOR,
                value_kind=PropertyValueKind.OBJECT,
                nested=motor_spec(),
            ),
        },
        hydration_level=ClassHydrationLevel.SCOPE,
    )


@pytest.fixture
def store() -> Mock:
    db = Mock(spec=TripleStore)
    db.triples_get.return_value = []
    db.query.return_value = {"results": {"bindings": []}}
    db.iri_exists.return_value = False
    db.owl_get_classes_of_individual.return_value = set()
    return db


@pytest.fixture
def ogm(store: Mock) -> OGM:
    """An OGM on the mocked store. It shadows the live ``ogm`` of the root conftest here."""
    return OGM(db=store)


def assert_nothing_written(store: Mock) -> None:
    for write in WRITES:
        getattr(store, write).assert_not_called()


# The data that each write refuses, and the instance, property and class the error names.
UNKNOWN = {
    "literal": (
        {HAS_SPEED: [1.0], HAS_COLOUR: ["red"]},
        (BELT1, HAS_COLOUR, BELT),
    ),
    "in a nested value": (
        {HAS_MOTOR: [{"id": MOTOR1, HAS_SPEED: [2.0], HAS_COLOUR: ["red"]}]},
        (MOTOR1, HAS_COLOUR, MOTOR),
    ),
    "object property in a nested value": (
        {HAS_MOTOR: [{"id": MOTOR1, HAS_MOTOR: [{"id": IRI(NS + "motor2")}]}]},
        (MOTOR1, HAS_MOTOR, MOTOR),
    ),
}


def assert_names(error: OGMDataError, expected: tuple) -> None:
    assert (error.instance_iri, error.property_iri, error.class_iri) == expected


# ---------------------------------------------------------------------------
# Read: an undeclared key is ignored
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("named", [True, False], ids=["named", "anonymous"])
def test_the_model_drops_an_unknown_key_without_error(named: bool) -> None:
    spec = ClassSpec(
        iri=IRI("ConveyorBelt", ONTO) if named else None,
        types=[],
        properties={
            HAS_UNIT: PropertySpec(
                iri=HAS_UNIT,
                value_kind=PropertyValueKind.LITERAL,
                python_range_type=str,
            )
        },
        hydration_level=ClassHydrationLevel.FULL,
    )
    model = spec.to_pydantic_model()
    data: dict = {HAS_UNIT.lined: ["m"], "someComment": "not declared"}
    if named:
        data["id"] = IRI("belt1", ONTO)

    instance = model.model_validate(data)

    assert getattr(instance, HAS_UNIT.lined) == ["m"]
    assert "someComment" not in instance.model_dump()
    assert model.model_config.get("extra", "ignore") == "ignore"


def test_a_fetched_node_with_an_undeclared_statement_materializes_with_a_warning(
    ogm: OGM, caplog
) -> None:
    """The read path materializes as ``fetch(materialize=True)`` does: no refusal."""
    node = Node(
        id=BELT1,
        class_spec=belt_spec(),
        data={HAS_SPEED: [1.0], HAS_COLOUR: ["red"]},
        ogm=ogm,
    )

    with caplog.at_level(logging.WARNING, logger="kapps_node_validator"):
        instance = node.materialize()

    assert getattr(instance, HAS_SPEED.lined) == [1.0]
    assert HAS_COLOUR.lined not in instance.model_dump()
    assert str(HAS_COLOUR) in caplog.text


def test_fetch_tolerates_an_undeclared_statement_with_a_warning(
    ogm: OGM, store: Mock, caplog
) -> None:
    """An anonymous parameter node carries a statement its class does not declare, as the
    connection metadata of a parameter does: ``fetch`` reads it, warns, and does not refuse.
    """
    parameter = mint_skolem_iri(ogm.skolem_namespace)
    spec = ClassSpec(
        iri=BELT,
        properties={
            HAS_PARAMETER: PropertySpec(
                iri=HAS_PARAMETER,
                value_kind=PropertyValueKind.COMPLEX,
                nested=ClassSpec(
                    iri=None,
                    properties={
                        HAS_VALUE: PropertySpec(
                            iri=HAS_VALUE,
                            value_kind=PropertyValueKind.LITERAL,
                            python_range_type=float,
                        )
                    },
                    hydration_level=ClassHydrationLevel.FULL,
                ),
            )
        },
        hydration_level=ClassHydrationLevel.SCOPE,
    )
    store.query.return_value = {
        "results": {
            "bindings": [
                {"bnode": parameter, "property": HAS_VALUE, "value": 1.5},
                {"bnode": parameter, "property": HAS_TOPIC, "value": "belt/speed"},
            ]
        }
    }

    with caplog.at_level(logging.WARNING, logger="kapps_node_validator"):
        node = ogm.fetch(instance_iri=BELT1, class_spec=spec, materialize=True)

    (fetched,) = getattr(node.instance, HAS_PARAMETER.lined)
    assert getattr(fetched, HAS_VALUE.lined) == [1.5]
    assert HAS_TOPIC.lined not in fetched.model_dump()
    assert str(HAS_TOPIC) in caplog.text
    assert_nothing_written(store)


# ---------------------------------------------------------------------------
# Write: create refuses, and nothing is written
# ---------------------------------------------------------------------------


class TestCreateRefuses:
    @pytest.mark.parametrize("case", UNKNOWN)
    def test_an_unknown_property(self, ogm: OGM, store: Mock, case: str) -> None:
        data, expected = UNKNOWN[case]

        with (
            patch.object(ogm, "get_class_spec", return_value=belt_spec()),
            pytest.raises(OGMDataError) as caught,
        ):
            ogm.create(class_iri=BELT, instance_iri=BELT1, data=data)

        assert_names(caught.value, expected)
        assert_nothing_written(store)

    def test_the_minimum_stays_unenforced(self, ogm: OGM, store: Mock) -> None:
        """Refusing unknown properties does not turn the OWL minimums on: a property with fewer
        values than its minimum is written."""
        spec = belt_spec()
        spec.properties[HAS_SPEED].min_count = 2

        with patch.object(ogm, "get_class_spec", return_value=spec):
            ogm.create(class_iri=BELT, instance_iri=BELT1, data={HAS_SPEED: [1.0]})

        store.triples_add.assert_called_once()


# ---------------------------------------------------------------------------
# Write: commit refuses, and nothing is written
# ---------------------------------------------------------------------------


class TestCommitRefuses:
    @pytest.mark.parametrize("case", UNKNOWN)
    def test_an_unknown_property_in_a_create(
        self, ogm: OGM, store: Mock, case: str
    ) -> None:
        data, expected = UNKNOWN[case]

        with (
            patch.object(ogm, "get_class_spec", return_value=belt_spec()),
            pytest.raises(OGMDataError) as caught,
        ):
            ogm.commit(changes=[Create(class_iri=BELT, instance_iri=BELT1, data=data)])

        assert_names(caught.value, expected)
        assert_nothing_written(store)

    @pytest.mark.parametrize("case", UNKNOWN)
    def test_an_unknown_property_in_an_update(
        self, ogm: OGM, store: Mock, case: str
    ) -> None:
        """The class specification admits the data's properties, the validation refuses."""
        data, expected = UNKNOWN[case]
        store.owl_get_classes_of_individual.return_value = {BELT}

        with (
            patch.object(ogm, "get_class_spec", return_value=belt_spec()),
            pytest.raises(OGMDataError) as caught,
        ):
            ogm.commit(changes=[Update(instance_iri=BELT1, data=data)])

        assert_names(caught.value, expected)
        assert_nothing_written(store)


# ---------------------------------------------------------------------------
# Write: commit refuses on the real path, where the scope comes from the data
# ---------------------------------------------------------------------------

TASK = IRI(NS + "Task")
T1 = IRI(NS + "t1")
T2 = IRI(NS + "t2")
HAS_REFERENCE_TASK = IRI(NS + "hasReferenceTask")
HAS_NAME = IRI(NS + "hasName")

# The ontology the mocked store answers: each class with its properties, each property with its
# rdf:type and its range. A task refers to another task, so the class refers to itself.
CLASSES = {
    BELT: [HAS_SPEED, HAS_MOTOR],
    MOTOR: [HAS_SPEED],
    TASK: [HAS_NAME, HAS_REFERENCE_TASK],
}
PROPERTIES = {
    HAS_SPEED: ("owl:DatatypeProperty", {float}),
    HAS_NAME: ("owl:DatatypeProperty", {str}),
    HAS_MOTOR: ("owl:ObjectProperty", {MOTOR}),
    HAS_REFERENCE_TASK: ("owl:ObjectProperty", {TASK}),
}
INSTANCES = {BELT1: BELT, T1: TASK}


@pytest.fixture
def ontology_store(store: Mock):
    """The mocked store answers the reads that build a class specification from a scope."""

    def triples_get(sub=None, pred=None, **_):
        if pred == "rdf:type" and sub in CLASSES:
            return [(sub, IRI("rdf:type"), IRI("owl:Class"))]
        if pred == "rdf:type" and sub in PROPERTIES:
            return [(sub, IRI("rdf:type"), IRI(PROPERTIES[sub][0]))]
        return []

    def query(sparql, **_):
        text = sparql.to_string() if hasattr(sparql, "to_string") else str(sparql)
        bindings = []
        if "isBlank" not in text:  # the property discovery, not the maximum counts
            for cls, properties in CLASSES.items():
                if (
                    f"<{cls}> <http://www.w3.org/2000/01/rdf-schema#subClassOf>*"
                    in text
                ):
                    bindings = [{"property": prop} for prop in properties]
        return {"results": {"bindings": bindings}}

    store.triples_get.side_effect = triples_get
    store.query.side_effect = query
    store.owl_get_classes_of_individual.side_effect = lambda iri, **_: (
        {INSTANCES[iri]} if iri in INSTANCES else set()
    )
    with patch.object(
        PropertySpec,
        "_resolve_effective_ranges",
        side_effect=lambda prop_iri, ogm, **_: set(PROPERTIES[prop_iri][1]),
    ):
        yield store


class TestCommitRefusesOnTheRealPath:
    """Nothing of the class specification is patched: the scope that ``commit`` derives from the
    data names the unknown property, so building the class specification refuses it. The error
    names the instance that carries the property."""

    @pytest.mark.parametrize(
        "instance, data, expected",
        [
            (
                BELT1,
                {HAS_SPEED: [1.0], HAS_COLOUR: ["red"]},
                (BELT1, HAS_COLOUR, BELT),
            ),
            (
                BELT1,
                {HAS_MOTOR: [{"id": MOTOR1, HAS_SPEED: [2.0], HAS_COLOUR: ["red"]}]},
                (MOTOR1, HAS_COLOUR, MOTOR),
            ),
            (
                BELT1,
                {HAS_MOTOR: [{HAS_COLOUR: ["red"]}]},
                (None, HAS_COLOUR, MOTOR),
            ),
            (
                T1,
                {HAS_REFERENCE_TASK: [{"id": T2, HAS_COLOUR: ["red"]}]},
                (T2, HAS_COLOUR, TASK),
            ),
            (
                T1,
                {HAS_COLOUR: ["red"], HAS_REFERENCE_TASK: [{"id": T2}]},
                (T1, HAS_COLOUR, TASK),
            ),
        ],
        ids=[
            "on the instance",
            "on a nested instance with an IRI",
            "on a nested instance without an IRI yet",
            "on a nested instance of the same class",
            "on the instance, which nests one of the same class",
        ],
    )
    def test_an_unknown_property(
        self, ogm: OGM, ontology_store: Mock, instance, data, expected
    ) -> None:
        with pytest.raises(OGMDataError) as caught:
            ogm.commit(instance_iri=instance, data=data)

        assert_names(caught.value, expected)
        assert_nothing_written(ontology_store)

    def test_several_unknown_properties_name_no_property(
        self, ogm: OGM, ontology_store: Mock
    ) -> None:
        """``property_iri`` names one property only; the message lists them all."""
        with pytest.raises(OGMDataError) as caught:
            ogm.commit(
                instance_iri=BELT1,
                data={HAS_COLOUR: ["red"], HAS_NAME: ["belt"]},
            )

        assert_names(caught.value, (BELT1, None, BELT))
        assert str(HAS_COLOUR) in str(caught.value)
        assert str(HAS_NAME) in str(caught.value)
        assert_nothing_written(ontology_store)
