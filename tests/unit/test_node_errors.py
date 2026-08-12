"""
Unit tests for Node error conditions and edge cases.

Tests Phase 2.1: Node Error Conditions
- to_triples() without materialized instance
- to_json_ld() error handling
- Node state management edge cases
- None/null value handling
"""

import pytest
from unittest.mock import Mock
from pydantic import BaseModel

from kapps_triplestore_interface import IRI
from kapps_ogm.node.core import Node
from kapps_ogm.mapping.class_spec import ClassSpec
from kapps_ogm.ogm import OGM

from .conftest import INSTANCE_IRI


class TestNodeToTriplesErrors:
    """Test error conditions in to_triples() method."""

    def test_to_triples_without_instance_raises(self, simple_class_spec: Mock):
        """Test that to_triples() raises RuntimeError when node not materialized."""
        # Setup: Node without instance
        node = Node(
            id=INSTANCE_IRI,
            class_spec=simple_class_spec,
            ogm=None,
        )
        assert node.instance is None

        # Execute & Assert
        with pytest.raises(RuntimeError, match="Node must be materialized"):
            node.to_triples()

    def test_to_triples_without_iri_raises(self, simple_class_spec: Mock):
        """Test that to_triples() raises RuntimeError when node has no IRI."""
        # Setup: Node with instance but no IRI
        mock_instance = Mock(spec=BaseModel)
        mock_instance.__class__._iri_fields = {}

        node = Node(
            id=None,  # No IRI
            class_spec=simple_class_spec,
            instance=mock_instance,
            ogm=None,
        )

        # Execute & Assert
        with pytest.raises(RuntimeError, match="Node has no IRI"):
            node.to_triples()

    def test_to_triples_without_class_spec_or_iri_fields_raises(self):
        """Test that to_triples() raises when neither ClassSpec nor _iri_fields present."""

        # Setup: Node with instance but no _iri_fields
        # Create a real class without _iri_fields
        class MinimalModel(BaseModel):
            pass

        mock_instance = MinimalModel()

        node = Node(
            id=INSTANCE_IRI,
            class_spec=None,  # No ClassSpec
            instance=mock_instance,
            ogm=None,
        )

        # Execute & Assert
        with pytest.raises(RuntimeError, match="ClassSpec or _iri_fields"):
            node.to_triples()

    def test_to_triples_handles_none_property_values(self, simple_class_spec: Mock):
        """Test that to_triples() skips None/null property values."""
        # Setup: Instance with None values
        mock_instance = Mock(spec=BaseModel)
        mock_instance.__class__._iri_fields = {
            "property1": IRI("https://example.org/prop1"),
            "property2": IRI("https://example.org/prop2"),
        }
        mock_instance.property1 = "value1"
        mock_instance.property2 = None  # None value

        node = Node(
            id=INSTANCE_IRI,
            class_spec=simple_class_spec,
            instance=mock_instance,
            ogm=None,
        )

        # Execute
        triples = node.to_triples()

        # Assert: Only property1 appears in triples
        predicates = {p for s, p, o in triples if isinstance(p, IRI)}
        assert IRI("https://example.org/prop1") in predicates
        # property2 should not appear since its value is None
        triple_strs = [str(t) for t in triples]
        assert not any("prop2" in str(t) for t in triple_strs)

    def test_to_triples_with_blank_node_class_spec(self):
        """Test to_triples() with blank node (no class IRI)."""
        # Setup: ClassSpec without IRI (blank node)
        blank_class_spec = Mock(spec=ClassSpec)
        blank_class_spec.iri = None  # Blank node
        blank_class_spec.properties = {}

        mock_instance = Mock(spec=BaseModel)
        mock_instance.__class__._iri_fields = {}

        node = Node(
            id=INSTANCE_IRI,
            class_spec=blank_class_spec,
            instance=mock_instance,
            ogm=None,
        )

        # Execute
        triples = node.to_triples()

        # Assert: No rdf:type triples added for blank node class
        type_triples = [t for t in triples if "rdf:type" in str(t)]
        assert len(type_triples) == 0


class TestNodeToJSONLDErrors:
    """Test error conditions in to_json_ld() method."""

    def test_to_json_ld_with_empty_triples(self, simple_class_spec: Mock):
        """Test to_json_ld() with minimal/empty triple set."""
        # Setup: Node with instance that has no properties
        mock_instance = Mock(spec=BaseModel)
        mock_instance.__class__._iri_fields = {}

        node = Node(
            id=INSTANCE_IRI,
            class_spec=simple_class_spec,
            instance=mock_instance,
            ogm=None,
        )

        # Execute
        json_ld = node.to_json_ld()

        # Assert: Should return valid JSON-LD structure even with minimal data
        assert "@context" in json_ld
        assert "@graph" in json_ld
        assert isinstance(json_ld["@graph"], list)

    def test_to_json_ld_without_context(self, simple_class_spec: Mock):
        """Test to_json_ld() without context uses full URIs."""
        # Setup
        mock_instance = Mock(spec=BaseModel)
        mock_instance.__class__._iri_fields = {
            "test_property": IRI("https://example.org/testProp")
        }
        mock_instance.test_property = "test_value"

        node = Node(
            id=INSTANCE_IRI,
            class_spec=simple_class_spec,
            instance=mock_instance,
            ogm=None,
        )

        # Execute: No context provided
        json_ld = node.to_json_ld(context=None)

        # Assert: Should still work, URIs not compacted
        assert "@graph" in json_ld
        assert len(json_ld["@graph"]) > 0

    def test_to_json_ld_with_custom_context(self, simple_class_spec: Mock):
        """Test to_json_ld() with custom context."""
        # Setup
        mock_instance = Mock(spec=BaseModel)
        mock_instance.__class__._iri_fields = {}

        node = Node(
            id=INSTANCE_IRI,
            class_spec=simple_class_spec,
            instance=mock_instance,
            ogm=None,
        )

        # Execute: Context with valid namespace
        valid_context = {"ex": "https://example.org/"}
        json_ld = node.to_json_ld(context=valid_context)

        # Assert: Returns valid structure
        assert "@context" in json_ld
        assert "@graph" in json_ld


class TestNodeStateManagement:
    """Test Node state and lifecycle edge cases."""

    def test_node_with_all_none_values(self, simple_class_spec: Mock):
        """Test node handles case where all properties are None."""
        # Setup
        node = Node(
            id=INSTANCE_IRI,
            class_spec=simple_class_spec,
            ogm=None,
        )
        node.data = None
        node.instance = None

        # Assert: Node exists but has no data
        assert node.id == INSTANCE_IRI
        assert node.data is None
        assert node.instance is None
        assert node.class_spec == simple_class_spec

    def test_node_repr_with_long_iri(self, simple_class_spec: Mock):
        """Test __repr__ handles very long IRIs."""
        # Setup: Very long IRI
        long_iri = IRI("https://example.org/" + "a" * 200)
        node = Node(
            id=long_iri,
            class_spec=simple_class_spec,
            ogm=None,
        )

        # Execute
        repr_str = repr(node)

        # Assert: Should not raise, returns string
        assert isinstance(repr_str, str)
        assert "Node<ref" in repr_str

    def test_node_with_circular_reference_in_data(
        self, simple_class_spec: Mock, ogm: OGM
    ):
        """Test node handles circular references in data dict."""
        # Setup: Circular data structure: Self-reference
        circular_data = {}
        circular_data[IRI("https://example.org/property")] = [circular_data]

        node = Node(
            id=INSTANCE_IRI,
            class_spec=simple_class_spec,
            ogm=ogm,
        )

        # Should be able to assign circular data without error
        node.data = circular_data

        # Assert: Data assigned
        assert node.data == {IRI("https://example.org/property"): [node]}

        # Multi-hop circular reference
        data_node_1 = {}
        data_node_2 = {}
        data_node_1[IRI("https://example.org/property_1_to_2")] = [data_node_2]
        data_node_2[IRI("https://example.org/property_2_to_1")] = [data_node_1]

        node_1 = Node(
            id=INSTANCE_IRI,
            class_spec=simple_class_spec,
            ogm=ogm,
        )

        # Should be able to assign circular data without error
        node_1.data = data_node_1

        node_2 = node_1.data[IRI("https://example.org/property_1_to_2")][0]

        # Assert: Data assigned
        assert isinstance(node_2, Node)
        assert node_2.data == {IRI("https://example.org/property_2_to_1"): [node_1]}

    def test_node_equality_comparison(self, simple_class_spec):
        """Test node equality based on IRI."""
        # Setup: Two nodes with same IRI
        node1 = Node(
            id=INSTANCE_IRI,
            class_spec=simple_class_spec,
            ogm=None,
        )
        node2 = Node(
            id=INSTANCE_IRI,
            class_spec=simple_class_spec,
            ogm=None,
        )

        # Different node objects but same IRI
        assert node1 is not node2
        # Note: Node doesn't implement __eq__, so they won't be equal
        # This documents current behavior
        assert node1 != node2

    def test_node_with_empty_class_spec_properties(self):
        """Test node with ClassSpec that has no properties."""
        # Setup
        empty_class_spec = Mock(spec=ClassSpec)
        empty_class_spec.iri = INSTANCE_IRI
        empty_class_spec.properties = {}  # No properties

        node = Node(
            id=INSTANCE_IRI,
            class_spec=empty_class_spec,
            ogm=None,
        )

        # Assert: Node created successfully
        assert node.class_spec == empty_class_spec
        assert node.id == INSTANCE_IRI


class TestNodeListHandling:
    """Test Node handling of list-valued properties."""

    def test_to_triples_with_list_properties(self, simple_class_spec):
        """Test that list-valued properties generate multiple triples."""
        # Setup: Instance with list property
        mock_instance = Mock(spec=BaseModel)
        mock_instance.__class__._iri_fields = {
            "multi_valued": IRI("https://example.org/multiProp")
        }
        mock_instance.multi_valued = ["value1", "value2", "value3"]

        node = Node(
            id=INSTANCE_IRI,
            class_spec=simple_class_spec,
            instance=mock_instance,
            ogm=None,
        )

        # Execute
        triples = node.to_triples()

        # Assert: Multiple triples for the list property
        prop_triples = [
            t
            for t in triples
            if isinstance(t, tuple)
            and len(t) == 3
            and t[1] == IRI("https://example.org/multiProp")
        ]
        assert len(prop_triples) == 3

    def test_to_triples_with_empty_list(self, simple_class_spec):
        """Test that empty list properties don't generate triples."""
        # Setup: Instance with empty list
        mock_instance = Mock(spec=BaseModel)
        mock_instance.__class__._iri_fields = {
            "empty_list": IRI("https://example.org/emptyProp")
        }
        mock_instance.empty_list = []

        node = Node(
            id=INSTANCE_IRI,
            class_spec=simple_class_spec,
            instance=mock_instance,
            ogm=None,
        )

        # Execute
        triples = node.to_triples()

        # Assert: No triples for empty list (only type triples)
        prop_triples = [
            t
            for t in triples
            if isinstance(t, tuple)
            and len(t) == 3
            and t[1] == IRI("https://example.org/emptyProp")
        ]
        assert len(prop_triples) == 0
