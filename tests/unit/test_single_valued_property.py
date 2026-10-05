"""A single-valued property holds its one value, as a list of at most one.

A property whose maximum is one is single-valued. Its maximum comes from `owl:FunctionalProperty`,
or from an `rdfs:range` restriction with `owl:cardinality 1` or `owl:maxCardinality 1`. Its field
is a list like every other field: `[value]` with its one value, and `[]` without one. So the shape
of the data never depends on a cardinality declaration, and a fetch, `model_dump()`, commit cycle
needs no conversion. A second value is refused before the write, and the error names the maximum.

Before, the field was a scalar. The node passes every property as a list, so the model refused
the one value as well as two, and a materializing fetch of a stored value failed. So did a commit,
because it reads the stored instance as a model before it changes it.

The first classes check the field on hand-built specs, and the refusal on a mocked store. The live
classes read each declaration from a real store and go through `OGM.create`, `OGM.commit` and a
materializing `OGM.fetch`.
"""

import uuid
from unittest.mock import patch

import pytest
from kapps_triplestore_interface import IRI, to_literal
from pydantic import ValidationError
from rdflib import XSD

from kapps_ogm.mapping.class_spec import ClassHydrationLevel, ClassSpec
from kapps_ogm.mapping.property_spec import PropertySpec, PropertyValueKind
from kapps_ogm.ogm import OGM
from kapps_ogm.utils.class_scope import ClassScope

from .test_owl_minimums_not_required import count, restriction_range

NS = "https://example.org/singlevalued#"
BELT = IRI(NS + "Belt")
BELT1 = IRI(NS + "belt1")
BELT2 = IRI(NS + "belt2")
BELT3 = IRI(NS + "belt3")
HAS_SETPOINT = IRI(NS + "hasSetpoint")
HAS_PREDECESSOR = IRI(NS + "hasPredecessor")

# The pydantic message for a list over its maximum of one: it names the maximum.
OVER_THE_MAXIMUM = "at most 1 item"


def belt_spec() -> ClassSpec:
    """A belt with a single-valued literal and a single-valued reference to another belt."""
    return ClassSpec(
        iri=BELT,
        properties={
            HAS_SETPOINT: PropertySpec(
                iri=HAS_SETPOINT,
                value_kind=PropertyValueKind.LITERAL,
                python_range_type=float,
                max_count=1,
            ),
            HAS_PREDECESSOR: PropertySpec(
                iri=HAS_PREDECESSOR,
                value_kind=PropertyValueKind.OBJECT,
                max_count=1,
            ),
        },
        hydration_level=ClassHydrationLevel.FULL,
    )


def belt_model(data: dict):
    return belt_spec().to_pydantic_model().model_validate({"id": BELT1, **data})


class TestTheFieldIsAListOfAtMostOne:
    def test_without_a_value_it_is_an_empty_list(self):
        model = belt_model({})

        assert getattr(model, HAS_SETPOINT.lined) == []
        assert getattr(model, HAS_PREDECESSOR.lined) == []

    def test_one_value_is_a_list_of_one(self):
        model = belt_model({HAS_SETPOINT.lined: [1.5], HAS_PREDECESSOR.lined: [BELT2]})

        assert getattr(model, HAS_SETPOINT.lined) == [1.5]
        assert getattr(model, HAS_PREDECESSOR.lined) == [BELT2]

    def test_the_dump_has_the_shape_of_the_data(self):
        dumped = belt_model({HAS_SETPOINT.lined: [1.5]}).model_dump()

        assert dumped[HAS_SETPOINT.lined] == [1.5]
        assert dumped[HAS_PREDECESSOR.lined] == []

    @pytest.mark.parametrize(
        ("prop", "values"),
        [(HAS_SETPOINT, [1.5, 2.5]), (HAS_PREDECESSOR, [BELT2, BELT3])],
        ids=["literal", "reference"],
    )
    def test_a_second_value_is_refused_and_the_error_names_the_maximum(
        self, prop: IRI, values: list
    ):
        with pytest.raises(ValidationError, match=OVER_THE_MAXIMUM):
            belt_model({prop.lined: values})


# ---------------------------------------------------------------------------
# create and commit refuse a second value before they write
# ---------------------------------------------------------------------------

STORED_SETPOINT = 1.5


@pytest.fixture
def stored_belt(mock_db):
    """The mocked store holds `belt1` with one setpoint and no predecessor."""
    mock_db.owl_get_classes_of_individual.return_value = [BELT]
    mock_db.triples_get.side_effect = lambda sub=None, pred=None, **_: (
        [(BELT1, HAS_SETPOINT, STORED_SETPOINT)]
        if (sub, pred) == (BELT1, HAS_SETPOINT)
        else []
    )
    return mock_db


def assert_nothing_written(mock_db) -> None:
    for write in ("triples_add", "triples_update", "triples_delete", "query"):
        getattr(mock_db, write).assert_not_called()


class TestASecondValueIsRefusedBeforeTheWrite:
    def test_create_writes_nothing(self, ogm_with_mock_db: OGM, mock_db):
        with (
            patch.object(ogm_with_mock_db, "get_class_spec", return_value=belt_spec()),
            pytest.raises(ValidationError, match=OVER_THE_MAXIMUM),
        ):
            ogm_with_mock_db.create(
                class_iri=BELT, instance_iri=BELT1, data={HAS_SETPOINT: [1.0, 2.0]}
            )

        assert_nothing_written(mock_db)

    def test_commit_reads_the_stored_value_and_writes_nothing(
        self, ogm_with_mock_db: OGM, stored_belt
    ):
        with (
            patch.object(ogm_with_mock_db, "get_class_spec", return_value=belt_spec()),
            pytest.raises(ValidationError, match=OVER_THE_MAXIMUM),
        ):
            ogm_with_mock_db.commit(instance_iri=BELT1, data={HAS_SETPOINT: [1.0, 2.0]})

        assert_nothing_written(stored_belt)

    def test_commit_of_one_value_replaces_the_stored_one(
        self, ogm_with_mock_db: OGM, stored_belt
    ):
        """The commit reads the stored instance as a model first, so its one value must read."""
        with patch.object(ogm_with_mock_db, "get_class_spec", return_value=belt_spec()):
            ogm_with_mock_db.commit(instance_iri=BELT1, data={HAS_SETPOINT: [2.5]})

        call = stored_belt.triples_update.call_args
        assert call.kwargs["old_triples"] == {
            (BELT1, HAS_SETPOINT, to_literal(STORED_SETPOINT))
        }
        assert call.kwargs["new_triples"] == {(BELT1, HAS_SETPOINT, to_literal(2.5))}


# ---------------------------------------------------------------------------
# Each declaration read from a store, through create, commit and fetch
# ---------------------------------------------------------------------------

XSD_DOUBLE = IRI(str(XSD.double))

FORMS = ("owl:FunctionalProperty", "owl:cardinality 1", "owl:maxCardinality 1")


@pytest.fixture(scope="module")
def ontology(db):
    """A belt with one single-valued property per form, in a namespace no other test uses.

    The functional property is a literal of the belt itself. Each restriction constrains the value
    property of an anonymous parameter node, as the TransferUnit ontology does for a belt's speed.
    `hasPredecessor` is a functional reference to another belt.
    """
    ns = f"https://example.org/singlevalued_{uuid.uuid4().hex[:8]}#"
    belt, value = IRI(ns + "Belt"), IRI(ns + "hasValue")
    setpoint, predecessor = IRI(ns + "hasSetpoint"), IRI(ns + "hasPredecessor")
    exact, capped = IRI(ns + "hasExactSpeed"), IRI(ns + "hasCappedSpeed")
    functional = IRI("owl:FunctionalProperty")
    triples = [
        (belt, IRI("rdf:type"), IRI("owl:Class")),
        (value, IRI("rdf:type"), IRI("owl:DatatypeProperty")),
        (setpoint, IRI("rdf:type"), IRI("owl:DatatypeProperty")),
        (setpoint, IRI("rdf:type"), functional),
        (setpoint, IRI("rdfs:domain"), belt),
        (setpoint, IRI("rdfs:range"), XSD_DOUBLE),
        (predecessor, IRI("rdf:type"), IRI("owl:ObjectProperty")),
        (predecessor, IRI("rdf:type"), functional),
        (predecessor, IRI("rdfs:domain"), belt),
        (predecessor, IRI("rdfs:range"), belt),
    ]
    for parameter, cardinality in ((exact, "cardinality"), (capped, "maxCardinality")):
        triples += [
            (parameter, IRI("rdf:type"), IRI("owl:ObjectProperty")),
            (parameter, IRI("rdfs:domain"), belt),
            *restriction_range(
                parameter, value, ("allValuesFrom", XSD_DOUBLE), (cardinality, count(1))
            ),
        ]
    assert db.triples_add(triples, check_exist=False)
    return {
        "ns": ns,
        "belt": belt,
        "value": value,
        "predecessor": predecessor,
        "forms": {
            "owl:FunctionalProperty": setpoint,
            "owl:cardinality 1": exact,
            "owl:maxCardinality 1": capped,
        },
    }


def chain(ontology, form: str) -> list[IRI]:
    """The property chain from the belt to the single-valued property of `form`."""
    prop = ontology["forms"][form]
    return [prop] if form == "owl:FunctionalProperty" else [prop, ontology["value"]]


def form_scope(ontology, form: str) -> ClassScope:
    return ClassScope.from_property_chains([chain(ontology, form)])


def form_data(ontology, form: str, values: list) -> dict:
    """The data that gives the single-valued property of `form` the list `values`."""
    prop = ontology["forms"][form]
    if form == "owl:FunctionalProperty":
        return {prop: values}
    return {prop: [{ontology["value"]: values}]}


def form_values(ontology, form: str, model) -> list:
    """The field of the single-valued property of `form` in a materialized belt."""
    held = getattr(model, ontology["forms"][form].lined)
    if form == "owl:FunctionalProperty":
        return held
    (parameter,) = held
    return getattr(parameter, ontology["value"].lined)


def new_belt(ontology) -> IRI:
    return IRI(ontology["ns"] + f"belt_{uuid.uuid4().hex[:8]}")


def materialized(ogm: OGM, ontology, form: str, belt: IRI) -> list:
    node = ogm.fetch(
        instance_iri=belt, class_scope=form_scope(ontology, form), materialize=True
    )
    return form_values(ontology, form, node.instance)


class TestEachDeclarationReadFromAStore:
    @pytest.mark.parametrize("form", FORMS)
    def test_the_maximum_is_one(self, ogm: OGM, ontology, form: str):
        spec = ogm.get_class_spec(
            class_iri=ontology["belt"], class_scope=form_scope(ontology, form)
        )
        prop_spec = spec.properties[ontology["forms"][form]]
        if form != "owl:FunctionalProperty":
            prop_spec = prop_spec.nested.properties[ontology["value"]]

        assert prop_spec.max_count == 1

    @pytest.mark.parametrize("form", FORMS)
    @pytest.mark.parametrize("values", [[], [1.5]], ids=["no value", "one value"])
    def test_the_field_is_a_list_of_at_most_one(
        self, ogm: OGM, ontology, form: str, values: list
    ):
        node = ogm.create(
            class_iri=ontology["belt"],
            data=form_data(ontology, form, values),
            class_scope=form_scope(ontology, form),
            persist=False,
        )

        assert form_values(ontology, form, node.materialize()) == values


class TestOneValueThroughCreateCommitAndFetch:
    @pytest.mark.parametrize("form", FORMS)
    def test_create_commit_and_a_materializing_fetch(
        self, ogm: OGM, ontology, form: str
    ):
        belt = new_belt(ontology)
        ogm.create(
            class_iri=ontology["belt"],
            instance_iri=belt,
            data=form_data(ontology, form, [1.5]),
            class_scope=form_scope(ontology, form),
        )
        assert materialized(ogm, ontology, form, belt) == [1.5]

        ogm.commit(instance_iri=belt, data=form_data(ontology, form, [2.5]))

        assert materialized(ogm, ontology, form, belt) == [2.5]

    def test_a_fetched_model_commits_back_as_it_dumps(self, ogm: OGM, ontology):
        """The round trip needs no conversion: the dump has the shape of the data."""
        form = "owl:FunctionalProperty"
        setpoint, belt = ontology["forms"][form], new_belt(ontology)
        scope = form_scope(ontology, form)
        ogm.create(
            class_iri=ontology["belt"],
            instance_iri=belt,
            data={setpoint: [1.5]},
            class_scope=scope,
        )
        fetched = ogm.fetch(instance_iri=belt, class_scope=scope, materialize=True)
        payload = fetched.instance.model_dump()
        payload[setpoint.lined] = [3.5]

        ogm.commit(instance_iri=belt, data=payload)

        assert materialized(ogm, ontology, form, belt) == [3.5]

    def test_a_single_valued_reference(self, ogm: OGM, ontology):
        predecessor, belt, other = (
            ontology["predecessor"],
            new_belt(ontology),
            new_belt(ontology),
        )
        scope = ClassScope.from_property_chains([[predecessor]])
        ogm.create(class_iri=ontology["belt"], instance_iri=other, data={})
        ogm.create(
            class_iri=ontology["belt"],
            instance_iri=belt,
            data={predecessor: [{"id": str(other)}]},
            class_scope=scope,
        )

        model = ogm.fetch(instance_iri=belt, class_scope=scope, materialize=True)

        assert [item.id for item in getattr(model.instance, predecessor.lined)] == [
            other
        ]


class TestASecondValueIsRefusedOnAStore:
    """create and commit refuse a second value before they write, for each declaration."""

    @pytest.mark.parametrize("form", FORMS)
    def test_create_refuses_and_writes_nothing(self, ogm: OGM, db, ontology, form):
        belt = new_belt(ontology)

        with pytest.raises(ValidationError, match=OVER_THE_MAXIMUM):
            ogm.create(
                class_iri=ontology["belt"],
                instance_iri=belt,
                data=form_data(ontology, form, [1.5, 2.5]),
                class_scope=form_scope(ontology, form),
            )

        assert not db.triples_get(sub=belt)

    @pytest.mark.parametrize("form", FORMS)
    def test_commit_refuses_and_the_store_keeps_its_value(
        self, ogm: OGM, db, ontology, form
    ):
        belt = new_belt(ontology)
        ogm.create(
            class_iri=ontology["belt"],
            instance_iri=belt,
            data=form_data(ontology, form, [1.5]),
            class_scope=form_scope(ontology, form),
        )

        with pytest.raises(ValidationError, match=OVER_THE_MAXIMUM):
            ogm.commit(instance_iri=belt, data=form_data(ontology, form, [1.5, 2.5]))

        assert materialized(ogm, ontology, form, belt) == [1.5]
