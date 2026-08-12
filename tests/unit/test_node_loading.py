"""
Unit tests for Node lazy loading operations.

Tests Phase 1.2: Node Lazy Loading
- materialize() with automatic data loading
- Error handling when OGM is missing
- ValidationError propagation
"""

import pytest
from unittest.mock import Mock
from pydantic import BaseModel

from kapps_triplestore_interface import IRI
from kapps_ogm.node.core import Node

from .conftest import (
    INSTANCE_IRI,
    MOCK_INSTANCE_DATA,
)


class TestNodeRepr:
    """Test Node.__repr__() for different states."""

    def test_repr_with_instance(self, mock_node):
        """Test __repr__ when node has materialized instance."""
        # Setup
        mock_instance = Mock(spec=BaseModel)
        mock_instance.__repr__ = Mock(return_value="<MockInstance>")
        mock_node.instance = mock_instance

        # Execute
        repr_str = repr(mock_node)

        # Assert
        assert "Node<instance" in repr_str
        assert "<MockInstance>" in repr_str

    def test_repr_without_instance(self, mock_node):
        """Test __repr__ when node is not materialized."""
        # Setup: No instance, no data
        assert mock_node.instance is None
        assert mock_node.data is None

        # Execute
        repr_str = repr(mock_node)

        # Assert
        assert "Node<ref" in repr_str
        assert str(INSTANCE_IRI) in repr_str
        assert "data=False" in repr_str
        assert "class_spec=True" in repr_str

    def test_repr_with_data_but_not_materialized(self, mock_node):
        """Test __repr__ when node has data but no instance."""
        # Setup
        mock_node.data = {IRI("https://example.org#some"): ["data"]}

        # Execute
        repr_str = repr(mock_node)

        # Assert
        assert "Node<ref" in repr_str
        assert "data=True" in repr_str
        assert "class_spec=True" in repr_str
