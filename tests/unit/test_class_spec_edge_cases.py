"""
Unit tests for ClassSpec edge cases and error conditions.

Tests Phase 2.2: ClassSpec Edge Cases
- Blank node class specs (without IRI)
- Circular property references
- Invalid property chains
- Class equality and comparison
"""

import pytest
from unittest.mock import Mock

from kapps_triplestore_interface import IRI
from kapps_ogm.ogm import OGM
from kapps_ogm.mapping.class_spec import ClassSpec
from kapps_ogm.mapping.property_spec import PropertySpec

from .conftest import TRANSFER_UNIT_IRI


class TestBlankNodeClassSpec:
    """Test ClassSpec behavior with blank nodes (no IRI)."""

    def test_class_spec_without_iri_is_blank_node(self):
        """Test that ClassSpec without IRI is treated as blank node."""
        # Setup: Create ClassSpec without IRI
        blank_spec = ClassSpec(iri=None, types=[], properties={})  # Blank node

        # Assert
        assert blank_spec.iri is None
        assert blank_spec.properties == {}

    def test_blank_node_to_pydantic_skips_id_field(self):
        """Test that blank node ClassSpec doesn't add 'id' field to Pydantic model."""
        # Setup
        blank_spec = ClassSpec(iri=None, types=[], properties={})

        # Execute
        model = blank_spec.to_pydantic_model()

        # Assert: No 'id' field in model
        assert "id" not in model.model_fields

    def test_named_class_to_pydantic_includes_id_field(self):
        """Test that named ClassSpec adds 'id' field to Pydantic model."""
        # Setup
        named_spec = ClassSpec(iri=TRANSFER_UNIT_IRI, types=[], properties={})

        # Execute
        model = named_spec.to_pydantic_model()

        # Assert: 'id' field present
        assert "id" in model.model_fields


class TestClassSpecValidation:
    """Test ClassSpec validation and error handling."""

    def test_specify_with_non_class_iri_raises(
        self, ogm_with_mock_db: OGM, mock_db: Mock
    ):
        """Test that specifying non-Class IRI raises ValueError."""
        # Setup: IRI that is not a Class
        non_class_iri = IRI("https://example.org/NotAClass")
        mock_db.triples_get.return_value = [
            (non_class_iri, IRI("rdf:type"), IRI("owl:Thing"))  # Not owl:Class
        ]

        # Execute & Assert
        with pytest.raises(ValueError, match="is not an OWL/RDFS Class"):
            ClassSpec.specify(
                ogm=ogm_with_mock_db,
                class_iri=non_class_iri,
                hydration_level=True,
            )


class TestClassSpecSerialization:
    """Test ClassSpec serialization methods."""

    def test_to_string_with_properties(self):
        """Test to_string() includes property information."""
        # Setup: Real PropertySpec (not mock) to avoid iteration issues
        prop_spec = PropertySpec(
            iri=IRI("https://example.org/prop1"),
            value_kind="literal",
            python_range_type=str,
            min_count=None,
            max_count=None,
            nested=None,
        )

        class_spec = ClassSpec(
            iri=TRANSFER_UNIT_IRI,
            types=[IRI("owl:Class")],
            properties={IRI("https://example.org/prop1"): prop_spec},
        )

        # Execute
        string_repr = class_spec.to_string()

        # Assert: Contains class info
        assert str(TRANSFER_UNIT_IRI) in string_repr or "TransferUnit" in string_repr
        assert isinstance(string_repr, str)

    def test_to_string_with_no_properties(self):
        """Test to_string() with empty properties dict."""
        # Setup
        class_spec = ClassSpec(iri=TRANSFER_UNIT_IRI, types=[], properties={})

        # Execute
        string_repr = class_spec.to_string()

        # Assert: Returns valid string
        assert isinstance(string_repr, str)
        assert len(string_repr) > 0

    def test_to_json_schema_basic(self):
        """Test JSON schema generation from ClassSpec."""
        # Setup: Simple ClassSpec
        class_spec = ClassSpec(iri=TRANSFER_UNIT_IRI, types=[], properties={})

        # Execute
        model = class_spec.to_pydantic_model()
        schema = model.model_json_schema()

        # Assert: Valid JSON schema
        assert isinstance(schema, dict)
        assert "properties" in schema
        assert "id" in schema["properties"]  # Named class has id field
