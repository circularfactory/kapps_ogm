"""Unit tests for the anonymous-node pydantic base model.

The anonymous node behind a COMPLEX property carries its RDF address (a Skolem IRI) out of band,
in a ``PrivateAttr``, so that the address is reachable by the serializer but invisible in every
projection. See PRD requirement R4 and RDF 1.1 Concepts section 3.5.
"""

import pytest

from kapps_triplestore_interface import IRI

from kapps_ogm.mapping.anonymous_model import AnonymousNodeModel
from kapps_ogm.mapping.class_spec import ClassHydrationLevel, ClassSpec
from kapps_ogm.mapping.property_spec import PropertySpec, PropertyValueKind

ONTO = "https://www.sfb1574.kit.edu/ontologies/TransferUnit"

HAS_UNIT = IRI("hasUnit", ONTO)
HAS_VALUE = IRI("hasValue", ONTO)
HAS_MEASUREMENT = IRI("hasMeasurement", ONTO)
NAMED_CLASS = IRI("ConveyorBelt", ONTO)

SKOLEM_IRI = IRI("https://w3id.org/circularfactory/.well-known/genid/ab12")
OUTER_SKOLEM_IRI = IRI("https://w3id.org/circularfactory/.well-known/genid/outer01")
INNER_SKOLEM_IRI = IRI("https://w3id.org/circularfactory/.well-known/genid/inner01")

UNIT_FIELD = HAS_UNIT.lined
VALUE_FIELD = HAS_VALUE.lined
MEASUREMENT_FIELD = HAS_MEASUREMENT.lined


def literal_property(prop_iri: IRI, python_type: type) -> PropertySpec:
    return PropertySpec(
        iri=prop_iri,
        value_kind=PropertyValueKind.LITERAL,
        python_range_type=python_type,
    )


def anonymous_spec(**properties: PropertySpec) -> ClassSpec:
    """An anonymous ClassSpec, fully specified in place exactly as the OGM builds one."""
    return ClassSpec(
        iri=None,
        types=[],
        properties={spec.iri: spec for spec in properties.values()},
        hydration_level=ClassHydrationLevel.FULL,
    )


@pytest.fixture
def unit_spec() -> ClassSpec:
    """The parameter-node shape: one declared literal property."""
    return anonymous_spec(unit=literal_property(HAS_UNIT, str))


@pytest.fixture
def unit_model(unit_spec: ClassSpec):
    return unit_spec.to_pydantic_model()


class TestAnonymousNodeModelBase:
    """The base model itself."""

    def test_node_iri_is_a_private_attribute(self):
        assert "_node_iri" in AnonymousNodeModel.__private_attributes__

    def test_node_iri_defaults_to_none(self):
        assert AnonymousNodeModel()._node_iri is None


class TestBaseModelSelection:
    """Which pydantic base a ClassSpec builds on."""

    def test_anonymous_class_spec_builds_on_anonymous_node_model(self, unit_model):
        assert issubclass(unit_model, AnonymousNodeModel)

    def test_anonymous_class_spec_model_has_no_id_field(self, unit_model):
        assert "id" not in unit_model.model_fields

    def test_named_class_spec_does_not_build_on_anonymous_node_model(self):
        named = ClassSpec(iri=NAMED_CLASS, types=[], properties={})
        assert not issubclass(named.to_pydantic_model(), AnonymousNodeModel)

    def test_named_class_spec_keeps_its_id_field(self):
        named = ClassSpec(iri=NAMED_CLASS, types=[], properties={})
        assert "id" in named.to_pydantic_model().model_fields

    def test_an_explicit_base_model_is_still_honoured(self):
        class CustomBase(AnonymousNodeModel):
            pass

        spec = anonymous_spec(unit=literal_property(HAS_UNIT, str))
        spec.pydantic_base_model = CustomBase
        assert issubclass(spec.to_pydantic_model(), CustomBase)


class TestAddressCapture:
    """The ``id`` key is taken out of the payload and kept out of band."""

    def test_the_address_is_reachable_out_of_band(self, unit_model):
        instance = unit_model.model_validate({"id": SKOLEM_IRI, UNIT_FIELD: ["m/s"]})
        assert instance._node_iri == SKOLEM_IRI

    def test_declared_properties_still_round_trip(self, unit_model):
        instance = unit_model.model_validate({"id": SKOLEM_IRI, UNIT_FIELD: ["m/s"]})
        assert getattr(instance, UNIT_FIELD) == ["m/s"]

    def test_a_payload_without_an_address_leaves_it_unset(self, unit_model):
        instance = unit_model.model_validate({UNIT_FIELD: ["m/s"]})
        assert instance._node_iri is None


class TestProjectionInvariance:
    """R4: the address must not leak northbound, into JSON, or into OpenAPI."""

    def test_model_dump_has_no_id_key(self, unit_model):
        instance = unit_model.model_validate({"id": SKOLEM_IRI, UNIT_FIELD: ["m/s"]})
        assert "id" not in instance.model_dump()

    def test_model_dump_is_exactly_the_declared_properties(self, unit_model):
        instance = unit_model.model_validate({"id": SKOLEM_IRI, UNIT_FIELD: ["m/s"]})
        assert instance.model_dump() == {UNIT_FIELD: ["m/s"]}

    def test_model_dump_json_never_mentions_the_skolem_iri(self, unit_model):
        instance = unit_model.model_validate({"id": SKOLEM_IRI, UNIT_FIELD: ["m/s"]})
        assert str(SKOLEM_IRI) not in instance.model_dump_json()

    def test_json_schema_has_no_id_property(self, unit_model):
        assert "id" not in unit_model.model_json_schema().get("properties", {})

    def test_json_schema_never_mentions_the_private_attribute(self, unit_model):
        assert "_node_iri" not in str(unit_model.model_json_schema())


class TestMirrorNotSourceOfTruth:
    """R3/R4: the address deliberately does not survive dump then revalidate."""

    def test_the_address_is_lost_on_dump_then_revalidate(self, unit_model):
        instance = unit_model.model_validate({"id": SKOLEM_IRI, UNIT_FIELD: ["m/s"]})
        revalidated = unit_model.model_validate(instance.model_dump())

        assert revalidated._node_iri is None
        assert getattr(revalidated, UNIT_FIELD) == ["m/s"]


class TestNestedAnonymousNodes:
    """An anonymous node whose own property is itself a list of anonymous nodes."""

    @pytest.fixture
    def outer_model(self):
        inner = anonymous_spec(value=literal_property(HAS_VALUE, float))
        outer = anonymous_spec(
            measurement=PropertySpec(
                iri=HAS_MEASUREMENT,
                value_kind=PropertyValueKind.COMPLEX,
                nested=inner,
            )
        )
        return outer.to_pydantic_model()

    @pytest.fixture
    def nested_instance(self, outer_model):
        return outer_model.model_validate(
            {
                "id": OUTER_SKOLEM_IRI,
                MEASUREMENT_FIELD: [{"id": INNER_SKOLEM_IRI, VALUE_FIELD: [1.5]}],
            }
        )

    def test_every_level_keeps_its_own_address(self, nested_instance):
        inner_instance = getattr(nested_instance, MEASUREMENT_FIELD)[0]

        assert nested_instance._node_iri == OUTER_SKOLEM_IRI
        assert inner_instance._node_iri == INNER_SKOLEM_IRI

    def test_no_level_leaks_an_id_into_the_projection(self, nested_instance):
        dumped = nested_instance.model_dump()

        assert "id" not in dumped
        assert "id" not in dumped[MEASUREMENT_FIELD][0]
        assert dumped[MEASUREMENT_FIELD][0][VALUE_FIELD] == [1.5]
