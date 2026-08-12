"""
Unit tests for PropertySpec edge cases and validation.

Tests Phase 2.3: PropertySpec Edge Cases
- Cardinality constraints (min/max)
- Datatype validation
- someValuesFrom/allValuesFrom constraints
- Multiple range handling
"""

import pytest
from unittest.mock import Mock

from kapps_triplestore_interface import IRI
from kapps_ogm.mapping.property_spec import PropertySpec, PropertyValueKind
from kapps_ogm.mapping.class_spec import ClassHydrationLevel, ClassSpec
from kapps_ogm.utils.class_scope import ClassScope
from kapps_ogm.ogm import OGM


class TestPropertySpecCardinality:
    """Test PropertySpec cardinality constraints."""

    def test_property_with_min_count_constraint(self):
        """Test PropertySpec with minimum cardinality."""
        # Setup: Property with min_count = 2
        prop_spec = PropertySpec(
            iri=IRI("https://example.org/multiProp"),
            value_kind=PropertyValueKind.LITERAL,
            python_range_type=str,
            min_count=2,
            max_count=None,
            nested=None,
        )

        # Execute
        field_type, field = prop_spec.to_pydantic_field()

        # Assert: List type with min constraint
        assert prop_spec.min_count == 2
        assert prop_spec.max_count is None

    def test_property_with_max_count_constraint(self):
        """Test PropertySpec with maximum cardinality."""
        # Setup: Property with max_count = 1 (functional)
        prop_spec = PropertySpec(
            iri=IRI("https://example.org/singleProp"),
            value_kind=PropertyValueKind.LITERAL,
            python_range_type=str,
            min_count=None,
            max_count=1,
            nested=None,
        )

        # Assert
        assert prop_spec.max_count == 1

    def test_property_with_exact_cardinality(self):
        """Test PropertySpec with exact cardinality (min = max)."""
        # Setup: Exactly 3 values required
        prop_spec = PropertySpec(
            iri=IRI("https://example.org/exactProp"),
            value_kind=PropertyValueKind.LITERAL,
            python_range_type=int,
            min_count=3,
            max_count=3,
            nested=None,
        )

        # Assert
        assert prop_spec.min_count == 3
        assert prop_spec.max_count == 3

    def test_property_with_unbounded_cardinality(self):
        """Test PropertySpec with unbounded cardinality (0..*)."""
        # Setup
        prop_spec = PropertySpec(
            iri=IRI("https://example.org/manyProp"),
            value_kind=PropertyValueKind.LITERAL,
            python_range_type=str,
            min_count=0,
            max_count=None,  # Unbounded
            nested=None,
        )

        # Assert
        assert prop_spec.min_count == 0
        assert prop_spec.max_count is None


class TestPropertySpecDatatypes:
    """Test PropertySpec datatype handling."""

    def test_literal_property_with_string_type(self):
        """Test literal property with string datatype."""
        # Setup
        prop_spec = PropertySpec(
            iri=IRI("https://example.org/nameProp"),
            value_kind=PropertyValueKind.LITERAL,
            python_range_type=str,
            min_count=None,
            max_count=None,
            nested=None,
        )

        # Assert
        assert prop_spec.value_kind is PropertyValueKind.LITERAL
        assert prop_spec.python_range_type == str

    def test_literal_property_with_numeric_type(self):
        """Test literal property with numeric datatypes."""
        # Setup: Integer property
        int_prop = PropertySpec(
            iri=IRI("https://example.org/countProp"),
            value_kind=PropertyValueKind.LITERAL,
            python_range_type=int,
            min_count=None,
            max_count=None,
            nested=None,
        )

        # Assert
        assert int_prop.python_range_type == int

    def test_object_property_with_iri_range(self):
        """Test object property pointing to another class."""
        # Setup: Object property with nested ClassSpec
        nested_class = Mock(spec=ClassSpec)
        nested_class.iri = IRI("https://example.org/TargetClass")
        nested_class.hydration_level = ClassHydrationLevel.FULL
        nested_class.to_pydantic_model = Mock(return_value=Mock)

        prop_spec = PropertySpec(
            iri=IRI("https://example.org/refProp"),
            value_kind=PropertyValueKind.OBJECT,
            python_range_type=None,
            min_count=None,
            max_count=None,
            nested=nested_class,
        )

        # Assert
        assert prop_spec.value_kind is PropertyValueKind.OBJECT
        assert prop_spec.nested == nested_class

    def test_literal_property_with_invalid_object_iri_raises(self):
        """Test that literal property with IRI in allValuesFrom raises error."""
        # Setup: Literal property but allValuesFrom has IRI (invalid)
        prop_spec = PropertySpec(
            iri=IRI("https://example.org/badProp"),
            value_kind=PropertyValueKind.LITERAL,
            python_range_type=str,
            min_count=None,
            max_count=None,
            nested=None,
        )
        prop_spec.all_from = IRI("https://example.org/SomeClass")  # Invalid for literal

        # Execute & Assert
        with pytest.raises(ValueError, match="cannot have allValuesFrom as Object IRI"):
            prop_spec.to_pydantic_field()


class TestPropertySpecConstraints:
    """Test someValuesFrom and allValuesFrom constraints."""

    def test_property_with_some_values_from(self):
        """Test PropertySpec with someValuesFrom constraint."""
        # Setup
        prop_spec = PropertySpec(
            iri=IRI("https://example.org/someProp"),
            value_kind=PropertyValueKind.LITERAL,
            python_range_type=str,
            min_count=None,
            max_count=None,
            nested=None,
        )
        prop_spec.some_from = str

        # Assert
        assert prop_spec.some_from == str

    def test_property_with_all_values_from(self):
        """Test PropertySpec with allValuesFrom constraint."""
        # Setup
        prop_spec = PropertySpec(
            iri=IRI("https://example.org/allProp"),
            value_kind=PropertyValueKind.LITERAL,
            python_range_type=None,
            min_count=None,
            max_count=None,
            nested=None,
        )
        prop_spec.all_from = int

        # Execute
        field_type, field = prop_spec.to_pydantic_field()

        # Assert: all_from used as base type
        assert prop_spec.all_from == int

    def test_property_with_both_some_and_all_values_from(self):
        """Test PropertySpec with both someValuesFrom and allValuesFrom."""
        # Setup
        prop_spec = PropertySpec(
            iri=IRI("https://example.org/bothProp"),
            value_kind=PropertyValueKind.LITERAL,
            python_range_type=str,
            min_count=None,
            max_count=None,
            nested=None,
        )
        prop_spec.some_from = str
        prop_spec.all_from = str

        # Execute
        field_type, field = prop_spec.to_pydantic_field()

        # Assert: Both constraints present
        assert prop_spec.some_from == str
        assert prop_spec.all_from == str


class TestPropertySpecification:
    """Test PropertySpec.specify_* class methods."""

    def test_specify_literal_property_with_no_range_raises(self, ogm: OGM):
        """Test that literal property without rdfs:range raises error."""
        # setup
        prop_iri = IRI("https://example.org/noRangeProp")
        ogm.db.triples_add(
            [
                (prop_iri, IRI("rdf:type"), IRI("owl:DatatypeProperty")),
            ]
        )

        # Execute & Assert
        with pytest.raises(ValueError, match="has no rdfs:range defined"):
            PropertySpec.specify(
                prop_iri=prop_iri,
                ogm=ogm,
                nested_scope=ClassScope(),
                hydration_level=True,
            )

    def test_specify_literal_property_with_multiple_ranges_raises(self, ogm: OGM):
        """Test that literal property with multiple rdfs:range raises error."""
        # Setup: Multiple ranges
        prop_iri = IRI("https://example.org/multiRangeProp")
        ogm.db.triples_add(
            [
                (prop_iri, IRI("rdf:type"), IRI("owl:DatatypeProperty")),
                (prop_iri, IRI("rdfs:range"), IRI("xsd:string")),
                (prop_iri, IRI("rdfs:range"), IRI("xsd:int")),
            ]
        )

        # Execute & Assert
        # Tolerant of the wording change in 3a86137, which narrowed the raise to
        # ranges that are genuinely independent (unrelated by subClassOf/subPropertyOf)
        # but left this assertion behind, so the suite failed on that branch.
        with pytest.raises(ValueError, match="has multiple.*rdfs:range defined"):
            PropertySpec.specify(
                prop_iri=prop_iri,
                ogm=ogm,
                nested_scope=ClassScope(),
                hydration_level=True,
            )

    def test_specify_class_property_creates_nested_class_spec(self, ogm: OGM):
        """Test that class property creates nested ClassSpec."""
        # Setup
        prop_iri = IRI("https://example.org/objectProp")
        target_class = IRI("https://example.org/TargetClass")
        ogm.db.triples_add(
            [
                (prop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty")),
                (prop_iri, IRI("rdfs:range"), target_class),
            ]
        )

        # Execute
        prop_spec = PropertySpec.specify(
            prop_iri=prop_iri,
            ogm=ogm,
            nested_scope=ClassScope(),
            hydration_level=True,
        )

        # Assert
        assert prop_spec.value_kind is PropertyValueKind.OBJECT
        assert prop_spec.nested is not None
        assert prop_spec.nested.iri == target_class

    def test_specify_class_property_with_no_range_raises(self, ogm: OGM):
        """Test that class property without rdfs:range raises error."""
        # Setup
        prop_iri = IRI("https://example.org/noRangeProp")
        ogm.db.triples_add(
            [
                (prop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty")),
            ]
        )

        # Execute & Assert
        with pytest.raises(ValueError, match="has no rdfs:range defined"):
            PropertySpec.specify(
                prop_iri=prop_iri,
                ogm=ogm,
                nested_scope=ClassScope(),
                hydration_level=True,
            )


class TestPropertySpecSerialization:
    """Test PropertySpec serialization methods."""

    def test_to_string_basic(self):
        """Test to_string() returns readable representation."""
        # Setup
        prop_spec = PropertySpec(
            iri=IRI("https://example.org/testProp"),
            value_kind=PropertyValueKind.LITERAL,
            python_range_type=str,
            min_count=1,
            max_count=5,
            nested=None,
        )

        # Execute
        string_repr = prop_spec.to_string()

        # Assert
        assert isinstance(string_repr, str)
        assert "testProp" in string_repr or "example.org" in string_repr

    def test_to_string_with_nested_class(self):
        """Test to_string() includes nested ClassSpec info."""
        # Setup
        nested_class = Mock(spec=ClassSpec)
        nested_class.iri = IRI("https://example.org/NestedClass")
        nested_class.properties = {}
        nested_class.to_string = Mock(return_value="NestedClass(...)")

        prop_spec = PropertySpec(
            iri=IRI("https://example.org/objProp"),
            value_kind=PropertyValueKind.OBJECT,
            python_range_type=None,
            min_count=None,
            max_count=None,
            nested=nested_class,
        )

        # Execute
        string_repr = prop_spec.to_string()

        # Assert
        assert isinstance(string_repr, str)


class TestComplexPropertyHandling:
    """Test complex property specifications."""

    def test_complex_property_with_restrictions(self):
        """Test complex property with OWL restrictions."""
        # Setup: Complex property with nested ClassSpec
        nested_class = ClassSpec(iri=None, types=[], properties={})  # Anonymous class

        prop_spec = PropertySpec(
            iri=IRI("https://example.org/complexProp"),
            value_kind=PropertyValueKind.COMPLEX,
            python_range_type=None,
            min_count=None,
            max_count=None,
            nested=nested_class,
        )

        # Assert
        assert prop_spec.value_kind is PropertyValueKind.COMPLEX
        assert prop_spec.nested is not None
        assert prop_spec.nested.iri is None  # Anonymous

    def test_complex_property_to_pydantic_field(self):
        """Test that complex property converts to Pydantic field."""
        # Setup
        nested_class = Mock(spec=ClassSpec)
        nested_class.to_pydantic_model = Mock(return_value=Mock)

        prop_spec = PropertySpec(
            iri=IRI("https://example.org/complexProp"),
            value_kind=PropertyValueKind.COMPLEX,
            python_range_type=None,
            min_count=None,
            max_count=None,
            nested=nested_class,
        )

        # Execute
        field_type, field = prop_spec.to_pydantic_field()

        # Assert: Nested class converted to Pydantic model
        nested_class.to_pydantic_model.assert_called_once()

    def test_unknown_value_kind_raises_error(self):
        """Test that unknown value_kind raises ValueError."""
        # Setup: Invalid value_kind
        prop_spec = PropertySpec(
            iri=IRI("https://example.org/badProp"),
            value_kind="invalid_kind",  # Invalid
            python_range_type=None,
            min_count=None,
            max_count=None,
            nested=None,
        )

        # Execute & Assert
        with pytest.raises(ValueError, match="Unknown value_kind"):
            prop_spec.to_pydantic_field()
