"""
Unit tests for OGM create operations and ID injection.

Tests Phase 1.3: Node ID Injection
- Auto-generation of nested object IDs
- Preservation of existing IDs
- Duplicate IRI detection
- Pydantic validation during creation
"""

import pytest
from unittest.mock import Mock, patch
from pydantic import ValidationError, BaseModel

from kapps_triplestore_interface import IRI
from kapps_ogm.node.core import Node
from kapps_ogm.mapping.class_spec import ClassSpec
from kapps_ogm.mapping.property_spec import PropertySpec

from .conftest import (
    TRANSFER_UNIT_IRI,
    INSTANCE_IRI,
)


class TestOGMCreate:
    """Test OGM.create() method."""

    def test_create_with_explicit_iri(self, ogm_with_mock_db, mock_db):
        """Test creating node with explicitly provided IRI."""
        # Setup
        mock_db.iri_exists.return_value = False
        data = {IRI("rdfs:label"): ["Test Unit"]}

        mock_class_spec = Mock(spec=ClassSpec)
        mock_class_spec.iri = TRANSFER_UNIT_IRI
        mock_class_spec.properties = {}

        # Mock Pydantic model
        mock_model_cls = Mock()
        mock_instance = Mock(spec=BaseModel)
        mock_model_cls.model_validate.return_value = mock_instance
        mock_class_spec.to_pydantic_model.return_value = mock_model_cls

        with patch.object(
            ogm_with_mock_db, "get_class_spec", return_value=mock_class_spec
        ):
            # Execute
            node = ogm_with_mock_db.create(
                class_iri=TRANSFER_UNIT_IRI, data=data, instance_iri=INSTANCE_IRI
            )

        # Assert
        assert isinstance(node, Node)
        assert node.id == INSTANCE_IRI
        assert node.data == data
        mock_db.iri_exists.assert_called_once_with(
            INSTANCE_IRI, as_sub=True, as_pred=True, as_obj=True
        )
        assert node.instance == mock_instance

    def test_create_with_id_in_data(self, ogm_with_mock_db, mock_db):
        """Test creating node with ID provided in data dict."""
        # Setup
        mock_db.iri_exists.return_value = False
        provided_iri = IRI("https://example.org/my_instance")
        data = {"id": str(provided_iri), IRI("rdfs:label"): ["Test"]}
        data_without_id = {IRI("rdfs:label"): ["Test"]}

        mock_class_spec = Mock(spec=ClassSpec)
        mock_class_spec.iri = TRANSFER_UNIT_IRI
        mock_class_spec.properties = {}

        mock_model_cls = Mock()
        mock_instance = Mock(spec=BaseModel)
        mock_model_cls.model_validate.return_value = mock_instance
        mock_class_spec.to_pydantic_model.return_value = mock_model_cls

        with patch.object(
            ogm_with_mock_db, "get_class_spec", return_value=mock_class_spec
        ):
            # Execute
            node = ogm_with_mock_db.create(class_iri=TRANSFER_UNIT_IRI, data=data)

        # Assert
        assert node.id == provided_iri
        assert node.data == data_without_id
        assert node.instance == mock_instance

    def test_create_auto_generates_id(self, ogm_with_mock_db, mock_db):
        """Test that create auto-generates ID when none provided."""
        # Setup
        data = {IRI("rdfs:label"): ["Test"]}
        generated_iri = IRI("https://example.org/generated_123")

        ogm_with_mock_db.naming_schema = Mock(return_value=generated_iri)

        mock_class_spec = Mock(spec=ClassSpec)
        mock_class_spec.iri = TRANSFER_UNIT_IRI
        mock_class_spec.properties = {}

        mock_model_cls = Mock()
        mock_instance = Mock(spec=BaseModel)
        mock_model_cls.model_validate.return_value = mock_instance
        mock_class_spec.to_pydantic_model.return_value = mock_model_cls

        with patch.object(
            ogm_with_mock_db, "get_class_spec", return_value=mock_class_spec
        ):
            # Execute
            node = ogm_with_mock_db.create(class_iri=TRANSFER_UNIT_IRI, data=data)

        # Assert
        assert node.id == generated_iri
        ogm_with_mock_db.naming_schema.assert_called_once_with(TRANSFER_UNIT_IRI)

    def test_create_validates_with_pydantic(self, ogm_with_mock_db, mock_db):
        """Test that create validates data with Pydantic model."""
        # Setup: Invalid data that will fail Pydantic validation
        mock_db.iri_exists.return_value = False
        data = {"invalid_field": ["bad_value"]}

        mock_class_spec = Mock(spec=ClassSpec)
        mock_class_spec.properties = {}

        # Mock Pydantic model that raises ValidationError
        mock_model_cls = Mock(
            side_effect=ValidationError.from_exception_data(
                "Test",
                [
                    {
                        "type": "missing",
                        "loc": ("required_field",),
                        "msg": "Field required",
                        "input": data,
                    }
                ],
            )
        )
        mock_class_spec.to_pydantic_model.return_value = mock_model_cls

        with patch.object(
            ogm_with_mock_db, "get_class_spec", return_value=mock_class_spec
        ):
            # Execute & Assert
            with pytest.raises(ValueError, match="Invalid property in data:"):
                ogm_with_mock_db.create(
                    class_iri=TRANSFER_UNIT_IRI, data=data, instance_iri=INSTANCE_IRI
                )
