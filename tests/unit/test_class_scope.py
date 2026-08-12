import pytest
from deepdiff import DeepDiff

from kapps_triplestore_interface import IRI
from kapps_ogm.utils.class_scope import ClassScope


@pytest.fixture
def simple_property_chain():
    """Single length property chain."""
    return [
        [
            IRI("https://ex.org#hasNodeB"),
        ],
    ]


@pytest.fixture
def multi_property_chain():
    """Multi length property chain."""
    return [
        [
            IRI("https://ex.org#hasNodeB"),
            IRI("https://ex.org#hasNodeC"),
        ]
    ]


@pytest.fixture
def multiple_property_chains():
    """Multiple property chains."""
    return [
        [
            IRI("https://ex.org#hasNodeB"),
            IRI("https://ex.org#hasAttributeB"),
        ],
        [
            IRI("https://ex.org#hasNodeC"),
            IRI("https://ex.org#hasAttributeC"),
        ],
    ]


@pytest.fixture
def overlapping_property_chains():
    """Overlapping property chains."""
    return [
        [
            IRI("https://ex.org#hasNodeB"),
            IRI("https://ex.org#hasAttributeB"),
        ],
        [
            IRI("https://ex.org#hasNodeB"),
            IRI("https://ex.org#hasNodeC"),
        ],
    ]


class TestClassScopeDictBehavior:
    def test_is_dict(self):
        """Test that ClassScope is a dict."""
        scope = ClassScope()
        assert isinstance(scope, dict)

    def test_setitem_converts_to_iri(self):
        """Test that setting items converts keys to IRI."""
        scope = ClassScope()
        scope["https://ex.org#hasNodeB"] = {}

        key = IRI("https://ex.org#hasNodeB")
        assert key in scope
        assert isinstance(list(scope.keys())[0], IRI)

    def test_setitem_converts_value_to_classscope(self):
        """Test that setting items converts values to ClassScope."""
        key = IRI("https://ex.org#hasNodeB")
        scope = ClassScope()
        scope[key] = {}

        assert isinstance(scope[key], ClassScope)


class TestClassScopeFromPropertyChains:
    def test_simple_property_chain(self, simple_property_chain):
        """Test creating ClassScope from a single property chain."""
        scope = ClassScope.from_property_chains(simple_property_chain)

        assert len(scope) == 1
        assert IRI("https://ex.org#hasNodeB") in scope

    def test_nested_property_chain(self, multi_property_chain):
        """Test creating ClassScope from nested property chains."""
        scope = ClassScope.from_property_chains(multi_property_chain)

        assert len(scope) == 1

        first_prop = IRI("https://ex.org#hasNodeB")
        assert first_prop in scope

        nested_scope = scope[first_prop]
        assert isinstance(nested_scope, ClassScope)
        assert len(nested_scope) == 1

        second_prop = IRI("https://ex.org#hasNodeC")
        assert second_prop in nested_scope

    def test_multiple_property_chains(self, multiple_property_chains):
        """Test creating ClassScope from multiple property chains."""
        scope = ClassScope.from_property_chains(multiple_property_chains)

        assert len(scope) == 2

        node_b_prop = IRI("https://ex.org#hasNodeB")
        node_c_prop = IRI("https://ex.org#hasNodeC")

        assert node_b_prop in scope
        assert node_c_prop in scope

        node_b_scope = scope[node_b_prop]
        assert len(node_b_scope) == 1
        attribute_b_prop = IRI("https://ex.org#hasAttributeB")
        assert attribute_b_prop in node_b_scope

        node_c_scope = scope[node_c_prop]
        assert len(node_c_scope) == 1
        attribute_c_prop = IRI("https://ex.org#hasAttributeC")
        assert attribute_c_prop in node_c_scope

    def test_empty_property_chains(self):
        """Test creating ClassScope with empty property chains."""
        scope = ClassScope.from_property_chains([])

        assert len(scope) == 0

    def test_overlapping_property_chains(self, overlapping_property_chains):
        """Test creating ClassScope from overlapping property chains.

        Property chains that share a common prefix should be merged,
        with the shared property appearing only once at the root level.
        """
        scope = ClassScope.from_property_chains(overlapping_property_chains)

        # hasNodeB should appear only once at root level
        assert len(scope) == 1

        node_b_prop = IRI("https://ex.org#hasNodeB")
        assert node_b_prop in scope

        # The child scope under hasNodeB should have two properties
        child_scope = scope[node_b_prop]
        assert len(child_scope) == 2

        attribute_b_prop = IRI("https://ex.org#hasAttributeB")
        node_c_prop = IRI("https://ex.org#hasNodeC")

        assert attribute_b_prop in child_scope
        assert node_c_prop in child_scope


class TestClassScopeFromNodeData:
    # Node A has two attributes
    data1 = {
        "https://ex.org#hasNodeA": [
            {
                # Node A
                "https://ex.org#hasAttributeA1": [123],
                "https://ex.org#hasAttributeA2": ["string"],
            },
        ],
    }

    tree1 = {
        IRI("https://ex.org#hasNodeA"): {
            IRI("https://ex.org#hasAttributeA1"): {},
            IRI("https://ex.org#hasAttributeA2"): {},
        }
    }

    # Node A has two instances with different attributes
    data2 = {
        "https://ex.org#hasNodeA": [
            {
                # Node A Version 1
                "https://ex.org#hasAttributeA1": [123],
            },
            {
                # Node A Version 2
                "https://ex.org#hasAttributeA2": ["string"],
            },
        ],
    }

    tree2 = {
        IRI("https://ex.org#hasNodeA"): {
            IRI("https://ex.org#hasAttributeA1"): {},
            IRI("https://ex.org#hasAttributeA2"): {},
        }
    }

    # Node A has two instances with the same subnodes, but different attributes later
    data3 = {
        "https://ex.org#hasNodeA": [
            {
                # Node A Version 1
                "https://ex.org#hasNodeB": [
                    {
                        # Node B Version 1
                        "https://ex.org#hasAttributeB1": [456],
                    }
                ],
            },
            {
                # Node A Version 2
                "https://ex.org#hasNodeB": [
                    {
                        # Node B Version 2
                        "https://ex.org#hasAttributeB2": ["string"],
                    }
                ],
            },
        ],
    }

    tree3 = {
        IRI("https://ex.org#hasNodeA"): {
            IRI("https://ex.org#hasNodeB"): {
                IRI("https://ex.org#hasAttributeB1"): {},
                IRI("https://ex.org#hasAttributeB2"): {},
            }
        }
    }

    def test_from_node_data(self):
        """Test creating ClassScope from simple Node data."""
        from kapps_ogm.node.core import Node

        node1 = Node(data=self.data1)
        scope1 = ClassScope.from_node_data(node1)
        diff1 = DeepDiff(
            scope1,
            self.tree1,
            ignore_order=True,
            ignore_type_in_groups=[(ClassScope, dict)],
        )
        assert diff1 == {}

        node2 = Node(data=self.data2)
        scope2 = ClassScope.from_node_data(node2)
        diff2 = DeepDiff(
            scope2,
            self.tree2,
            ignore_order=True,
            ignore_type_in_groups=[(ClassScope, dict)],
        )
        assert diff2 == {}

        node3 = Node(data=self.data3)
        scope3 = ClassScope.from_node_data(node3)
        diff3 = DeepDiff(
            scope3,
            self.tree3,
            ignore_order=True,
            ignore_type_in_groups=[(ClassScope, dict)],
        )
        assert diff3 == {}


class TestClassScopeToPropertyChains:
    def test_simple_round_trip(self, simple_property_chain):
        """Test converting to property chains and back."""
        scope = ClassScope.from_property_chains(simple_property_chain)
        result_chains = scope.to_property_chains()

        assert len(result_chains) == 1
        assert result_chains[0] == simple_property_chain[0]

    def test_nested_round_trip(self, multi_property_chain):
        """Test round-trip conversion with nested chains."""
        scope = ClassScope.from_property_chains(multi_property_chain)
        result_chains = scope.to_property_chains()

        assert len(result_chains) == 1
        assert result_chains[0] == multi_property_chain[0]

    def test_multiple_chains_round_trip(self, multiple_property_chains):
        """Test round-trip conversion with multiple chains."""
        scope = ClassScope.from_property_chains(multiple_property_chains)
        result_chains = scope.to_property_chains()

        assert len(result_chains) == 2
        assert set(tuple(chain) for chain in result_chains) == set(
            tuple(chain) for chain in multiple_property_chains
        )

    def test_empty_scope(self):
        """Test to_property_chains with empty scope."""
        scope = ClassScope.from_property_chains([])
        result_chains = scope.to_property_chains()

        assert result_chains == []

    def test_overlapping_chains_round_trip(self, overlapping_property_chains):
        """Test round-trip conversion with overlapping property chains."""
        scope = ClassScope.from_property_chains(overlapping_property_chains)
        result_chains = scope.to_property_chains()

        assert len(result_chains) == 2
        # Convert to sets of tuples for comparison (order-independent)
        assert set(tuple(chain) for chain in result_chains) == set(
            tuple(chain) for chain in overlapping_property_chains
        )


class TestClassScopeEdgeCases:
    def test_single_chain_with_one_property(self):
        """Test with a single property in one chain."""
        chains = [[IRI("https://ex.org#prop1")]]
        scope = ClassScope.from_property_chains(chains)

        assert len(scope) == 1
        prop1 = IRI("https://ex.org#prop1")
        assert prop1 in scope
        assert len(scope[prop1]) == 0

    def test_multiple_branches_same_depth(self):
        """Test multiple property chains with same depth."""
        chains = [
            [
                IRI("https://ex.org#hasNodeB"),
                IRI("https://ex.org#hasNodeC"),
            ],
            [
                IRI("https://ex.org#hasNodeD"),
                IRI("https://ex.org#hasNodeE"),
            ],
        ]
        scope = ClassScope.from_property_chains(chains)

        assert len(scope) == 2
        assert all(len(child_scope) == 1 for child_scope in scope.values())

    def test_mixed_depth_chains(self):
        """Test property chains with different depths."""
        chains = [
            [IRI("https://ex.org#hasNodeB")],
            [
                IRI("https://ex.org#hasNodeC"),
                IRI("https://ex.org#hasNodeD"),
                IRI("https://ex.org#hasNodeE"),
            ],
        ]
        scope = ClassScope.from_property_chains(chains)

        assert len(scope) == 2

        # Shallow chain
        node_b = IRI("https://ex.org#hasNodeB")
        assert len(scope[node_b]) == 0

        # Deep chain
        node_c = IRI("https://ex.org#hasNodeC")
        assert len(scope[node_c]) == 1

    def test_property_chains_preserves_structure(self, overlapping_property_chains):
        """Test that property chains accurately represents the scope structure."""
        scope = ClassScope.from_property_chains(overlapping_property_chains)
        chains = scope.to_property_chains()

        # Reconstruct scope from chains and verify structure matches
        reconstructed = ClassScope.from_property_chains(chains)
        reconstructed_chains = reconstructed.to_property_chains()

        assert set(tuple(chain) for chain in chains) == set(
            tuple(chain) for chain in reconstructed_chains
        )

    def test_deep_nesting(self):
        """Test deeply nested property chains."""
        deep_chains = [
            [
                IRI("https://ex.org#hasNodeB"),
                IRI("https://ex.org#hasNodeC"),
                IRI("https://ex.org#hasNodeD"),
            ]
        ]

        scope = ClassScope.from_property_chains(deep_chains)

        # Navigate through nested structure
        prop_b = IRI("https://ex.org#hasNodeB")
        prop_c = IRI("https://ex.org#hasNodeC")
        prop_d = IRI("https://ex.org#hasNodeD")

        assert prop_b in scope
        assert prop_c in scope[prop_b]
        assert prop_d in scope[prop_b][prop_c]
        assert len(scope[prop_b][prop_c][prop_d]) == 0
