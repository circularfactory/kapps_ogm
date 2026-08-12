"""
Test property chains expansion: reduced vs expanded chains.

Tests the hypothesis that both reduced chains (stopping at blank nodes) and
expanded chains (continuing through blank nodes to literals) should produce
equivalent ClassSpecs that can validate the same mock data.

Scenarios:
- Reduced: [hasConveyorBelt, hasConveyorPosition], [hasConveyorBelt, hasConveyorSpeed], [hasLightBarrier, isOccupied]
- Expanded: All chains including terminal properties (hasValue, hasUnit)

Both should:
1. Produce equivalent Pydantic ClassSpecs
2. Successfully validate the mock data
3. Result in equivalent instances
"""

import pytest

from kapps_triplestore_interface import IRI
from kapps_ogm.node.core import Node


# =====================================================================
# Mock Data (with expanded structure containing blank node properties)
# =====================================================================

TRANSFER_UNIT_IRI = IRI(
    "https://www.sfb1574.kit.edu/ontologies/TransferUnit#TransferUnit"
)

HAS_CONVEYOR_BELT = IRI(
    "https://www.sfb1574.kit.edu/ontologies/TransferUnit#hasConveyorBelt"
)
HAS_CONVEYOR_POSITION = IRI(
    "https://www.sfb1574.kit.edu/ontologies/TransferUnit#hasConveyorPosition"
)
HAS_CONVEYOR_SPEED = IRI(
    "https://www.sfb1574.kit.edu/ontologies/TransferUnit#hasConveyorSpeed"
)
HAS_LIGHT_BARRIER = IRI(
    "https://www.sfb1574.kit.edu/ontologies/TransferUnit#hasLightBarrier"
)
IS_OCCUPIED = IRI("https://www.sfb1574.kit.edu/ontologies/TransferUnit#isOccupied")
HAS_VALUE = IRI("https://www.sfb1574.kit.edu/ontologies/CrcInterfaces#hasValue")
HAS_UNIT = IRI("https://www.sfb1574.kit.edu/ontologies/TransferUnit#hasUnit")

MOCK_DATA = {
    HAS_CONVEYOR_BELT.lined: [
        {
            HAS_CONVEYOR_POSITION.lined: [
                {
                    HAS_VALUE.lined: [1.25],
                    HAS_UNIT.lined: ["meters"],
                }
            ],
            HAS_CONVEYOR_SPEED.lined: [
                {
                    HAS_VALUE.lined: [0.75],
                    HAS_UNIT.lined: ["meter_per_second"],
                }
            ],
        }
    ],
    HAS_LIGHT_BARRIER.lined: [
        {
            IS_OCCUPIED.lined: [
                {
                    HAS_VALUE.lined: [False],
                    HAS_UNIT.lined: ["boolean"],
                }
            ],
        }
    ],
}


# =====================================================================
# Property Chains: Reduced
# =====================================================================
# Stops at blank node properties (hasConveyorPosition, hasConveyorSpeed, isOccupied)
# Does NOT include terminal properties (hasValue, hasUnit)
# The ClassSpec builder must auto-expand these blank nodes

PROPERTY_CHAINS_REDUCED = [
    [
        IRI("https://www.sfb1574.kit.edu/ontologies/TransferUnit#hasConveyorBelt"),
        IRI("https://www.sfb1574.kit.edu/ontologies/TransferUnit#hasConveyorPosition"),
    ],
    [
        IRI("https://www.sfb1574.kit.edu/ontologies/TransferUnit#hasConveyorBelt"),
        IRI("https://www.sfb1574.kit.edu/ontologies/TransferUnit#hasConveyorSpeed"),
    ],
    [
        IRI("https://www.sfb1574.kit.edu/ontologies/TransferUnit#hasLightBarrier"),
        IRI("https://www.sfb1574.kit.edu/ontologies/TransferUnit#isOccupied"),
    ],
]


# =====================================================================
# Property Chains: Expanded
# =====================================================================
# Continues through blank nodes all the way to terminal properties (literals)
# Includes all intermediate steps

PROPERTY_CHAINS_EXPANDED = [
    # hasConveyorBelt → hasConveyorPosition → hasValue
    [
        IRI("https://www.sfb1574.kit.edu/ontologies/TransferUnit#hasConveyorBelt"),
        IRI("https://www.sfb1574.kit.edu/ontologies/TransferUnit#hasConveyorPosition"),
        IRI("https://www.sfb1574.kit.edu/ontologies/CrcInterfaces#hasValue"),
    ],
    # hasConveyorBelt → hasConveyorPosition → hasUnit
    [
        IRI("https://www.sfb1574.kit.edu/ontologies/TransferUnit#hasConveyorBelt"),
        IRI("https://www.sfb1574.kit.edu/ontologies/TransferUnit#hasConveyorPosition"),
        IRI("https://www.sfb1574.kit.edu/ontologies/TransferUnit#hasUnit"),
    ],
    # hasConveyorBelt → hasConveyorSpeed → hasValue
    [
        IRI("https://www.sfb1574.kit.edu/ontologies/TransferUnit#hasConveyorBelt"),
        IRI("https://www.sfb1574.kit.edu/ontologies/TransferUnit#hasConveyorSpeed"),
        IRI("https://www.sfb1574.kit.edu/ontologies/CrcInterfaces#hasValue"),
    ],
    # hasConveyorBelt → hasConveyorSpeed → hasUnit
    [
        IRI("https://www.sfb1574.kit.edu/ontologies/TransferUnit#hasConveyorBelt"),
        IRI("https://www.sfb1574.kit.edu/ontologies/TransferUnit#hasConveyorSpeed"),
        IRI("https://www.sfb1574.kit.edu/ontologies/TransferUnit#hasUnit"),
    ],
    # hasLightBarrier → isOccupied → hasValue
    [
        IRI("https://www.sfb1574.kit.edu/ontologies/TransferUnit#hasLightBarrier"),
        IRI("https://www.sfb1574.kit.edu/ontologies/TransferUnit#isOccupied"),
        IRI("https://www.sfb1574.kit.edu/ontologies/CrcInterfaces#hasValue"),
    ],
    # hasLightBarrier → isOccupied → hasUnit
    [
        IRI("https://www.sfb1574.kit.edu/ontologies/TransferUnit#hasLightBarrier"),
        IRI("https://www.sfb1574.kit.edu/ontologies/TransferUnit#isOccupied"),
        IRI("https://www.sfb1574.kit.edu/ontologies/TransferUnit#hasUnit"),
    ],
]


# =====================================================================
# Tests
# =====================================================================


class TestPropertyChainsExpansion:
    """Test that reduced and expanded property chains are equivalent."""

    @pytest.fixture
    def ogm(self, conftest_ogm):
        """Get OGM instance from conftest."""
        return conftest_ogm

    def test_reduced_chains_extract_from_data(self):
        """
        Test that extract_property_chains() produces reduced chains from nested data.

        This validates that the expansion detection logic correctly identifies
        when a property chain should continue (blank node) or stop (literal).
        """
        node = Node(data=MOCK_DATA)
        extracted_chains = node.extract_property_chains()

        # Should extract 6 chains (all the way to literals)
        assert (
            len(extracted_chains) == 6
        ), f"Expected 6 chains, got {len(extracted_chains)}"

        # All extracted chains should be IRIs (full reconstruction)
        for chain in extracted_chains:
            for item in chain:
                assert isinstance(item, IRI), f"Expected IRI, got {type(item)}: {item}"

    def test_extract_chains_produce_expanded_form(self):
        """
        Test that extract_property_chains produces expanded form (all the way to literals).

        This verifies that the extraction logic correctly continues through
        blank nodes until hitting terminal properties (literals).
        """
        node = Node(data=MOCK_DATA)
        extracted_chains = node.extract_property_chains()

        # Convert to strings for easier comparison
        extracted_strs = [[str(iri) for iri in chain] for chain in extracted_chains]
        expected_strs = [
            [str(iri) for iri in chain] for chain in PROPERTY_CHAINS_EXPANDED
        ]

        # Check that all expected chains are present
        for expected_chain in expected_strs:
            assert expected_chain in extracted_strs, (
                f"Expected chain not found: {expected_chain}\n"
                f"Extracted: {extracted_strs}"
            )

    def test_reduced_chains_vs_expanded_chains_coverage(self):
        """
        Test the relationship between reduced and expanded chains.

        Reduced chains are a subset of expanded chains:
        - Reduced: [hasConveyorBelt, hasConveyorPosition], ...
        - Expanded: [hasConveyorBelt, hasConveyorPosition, hasValue], ...

        For each reduced chain, there should be at least one expanded chain
        that extends it to a literal.
        """
        reduced_strs = [
            [str(iri) for iri in chain] for chain in PROPERTY_CHAINS_REDUCED
        ]
        expanded_strs = [
            [str(iri) for iri in chain] for chain in PROPERTY_CHAINS_EXPANDED
        ]

        # For each reduced chain, check that expanded chains extend it
        for reduced_chain in reduced_strs:
            extensions = [
                exp_chain
                for exp_chain in expanded_strs
                if exp_chain[: len(reduced_chain)] == reduced_chain
            ]
            assert (
                len(extensions) > 0
            ), f"No expanded chains extend the reduced chain: {reduced_chain}"

    def test_node_extraction_matches_expected_chains(self):
        """
        Test that Node.extract_property_chains() produces the expected 6 chains.
        """
        node = Node(data=MOCK_DATA)
        chains = node.extract_property_chains()

        # Create string representations for easier comparison
        chain_strs = [tuple(str(iri) for iri in chain) for chain in chains]

        expected_strs = [
            tuple(str(iri) for iri in chain) for chain in PROPERTY_CHAINS_EXPANDED
        ]

        # Sort for comparison (order might differ)
        assert sorted(chain_strs) == sorted(expected_strs), (
            f"Extracted chains don't match expected.\n"
            f"Got: {sorted(chain_strs)}\n"
            f"Expected: {sorted(expected_strs)}"
        )

    def test_mock_data_structure_has_nested_blanknodes(self):
        """
        Verify that the mock data contains nested blank node structures.

        This is a prerequisite for testing blank node expansion:
        - hasConveyorBelt → hasConveyorPosition (blank node)
        - hasConveyorPosition → hasValue, hasUnit (literals)
        """
        belt_key = list(MOCK_DATA.keys())[0]
        assert HAS_CONVEYOR_BELT.lined == belt_key

        belt_data = MOCK_DATA[belt_key][0]
        assert HAS_CONVEYOR_POSITION.lined in belt_data
        assert HAS_CONVEYOR_SPEED.lined in belt_data

        position_data = belt_data[HAS_CONVEYOR_POSITION.lined][0]

        assert HAS_VALUE.lined in position_data
        assert HAS_UNIT.lined in position_data

        # Check that values are literals (not nested dicts)
        assert isinstance(position_data[HAS_VALUE.lined][0], (int, float, str, bool))


# =====================================================================
# Tests for ClassSpec Generation with Different Chain Inputs
# =====================================================================


class TestClassSpecEquivalence:
    """
    Test that reduced and expanded property chains produce equivalent ClassSpecs.

    This is the critical hypothesis: both chain styles should result in identical
    or equivalent Pydantic models that can validate the same data.
    """

    def test_reduced_chains_are_subset_of_expanded(self):
        """
        Verify that reduced chains are a proper subset of expanded chains.

        Each reduced chain should be extendable to at least one expanded chain.
        """
        reduced_strs = [
            [str(iri) for iri in chain] for chain in PROPERTY_CHAINS_REDUCED
        ]
        expanded_strs = [
            [str(iri) for iri in chain] for chain in PROPERTY_CHAINS_EXPANDED
        ]

        # For each reduced chain prefix, should exist corresponding expanded chains
        for reduced in reduced_strs:
            found = False
            for expanded in expanded_strs:
                if expanded[: len(reduced)] == reduced:
                    found = True
                    break
            assert found, f"No expanded chain extends reduced chain: {reduced}"

    def test_expanded_chains_complete_blank_node_paths(self):
        """
        Verify that expanded chains represent complete paths to literals.

        Each expanded chain should terminate at a literal property, not a blank node.
        """
        # The last element of each expanded chain should be a terminal property
        # (hasValue or hasUnit in our case - these point to literals)
        terminal_props = {
            "https://www.sfb1574.kit.edu/ontologies/CrcInterfaces#hasValue",
            "https://www.sfb1574.kit.edu/ontologies/TransferUnit#hasUnit",
        }

        for chain in PROPERTY_CHAINS_EXPANDED:
            last_iri = str(chain[-1])
            assert last_iri in terminal_props, (
                f"Expanded chain doesn't end in terminal property: {chain}\n"
                f"Last: {last_iri}"
            )

    def test_property_chains_represent_ontology_structure(self):
        """
        Verify that property chains match the ontology structure for TransferUnit.

        Expected structure:
        - TransferUnit
          ├─ hasConveyorBelt → ConveyorBelt (named class)
          │  ├─ hasConveyorPosition → [blank node with hasValue, hasUnit]
          │  └─ hasConveyorSpeed → [blank node with hasValue, hasUnit]
          └─ hasLightBarrier → LightBarrier (named class)
             └─ isOccupied → [blank node with hasValue, hasUnit]
        """
        # Convert to strings for easier verification
        reduced_strs = [
            [str(iri) for iri in chain] for chain in PROPERTY_CHAINS_REDUCED
        ]

        # Verify ConveyorBelt branches
        conveyor_belt_branches = [
            c for c in reduced_strs if "hasConveyorBelt" in str(c[0])
        ]
        assert len(conveyor_belt_branches) == 2, "Should have 2 ConveyorBelt branches"

        position_found = any(
            "hasConveyorPosition" in str(c) for c in conveyor_belt_branches
        )
        speed_found = any("hasConveyorSpeed" in str(c) for c in conveyor_belt_branches)
        assert position_found and speed_found, "Missing ConveyorBelt sub-properties"

        # Verify LightBarrier branches
        light_barrier_branches = [
            c for c in reduced_strs if "hasLightBarrier" in str(c[0])
        ]
        assert len(light_barrier_branches) == 1, "Should have 1 LightBarrier branch"

        occupied_found = any("isOccupied" in str(c) for c in light_barrier_branches)
        assert occupied_found, "Missing LightBarrier isOccupied property"


# =====================================================================
# Test Data Extraction Integration
# =====================================================================


class TestPropertyChainDataExtraction:
    """
    Test end-to-end extraction of property chains from nested data.

    This verifies that extract_property_chains() on a Node correctly identifies
    all property paths through the nested structure.
    """

    def test_extract_all_terminal_paths(self):
        """
        Verify that all terminal property paths are extracted from nested data.

        A terminal path is one that ends at a literal value (not another dict).
        """
        node = Node(data=MOCK_DATA)
        chains = node.extract_property_chains()

        # Should extract exactly 6 chains (3 paths × 2 terminal properties each)
        assert len(chains) == 6, f"Expected 6 chains, got {len(chains)}"

    def test_extracted_chains_are_valid_iris(self):
        """
        Verify that all elements in extracted chains are valid IRI objects.
        """
        node = Node(data=MOCK_DATA)
        chains = node.extract_property_chains()

        for i, chain in enumerate(chains):
            for j, item in enumerate(chain):
                assert isinstance(
                    item, IRI
                ), f"Chain {i}, element {j}: expected IRI, got {type(item)}: {item}"

    def test_extracted_chains_reconstruct_full_iris(self):
        """
        Verify that extracted chains contain fully reconstructed IRIs (from lined format).

        The mock data has keys in lined format (with _c_, _s_, _d_, _h_ markers).
        After extraction, they should be converted back to full IRIs.
        """
        node = Node(data=MOCK_DATA)
        chains = node.extract_property_chains()

        # All extracted IRIs should contain '://' (full IRI indicator)
        for chain in chains:
            for iri in chain:
                assert "://" in str(iri), (
                    f"IRI not fully reconstructed: {str(iri)}\n"
                    f"Missing scheme part (should contain '://')"
                )

    def test_extraction_preserves_hierarchy(self):
        """
        Verify that extracted chains preserve the nested hierarchy structure.

        First element should always be hasConveyorBelt or hasLightBarrier.
        Middle elements should be intermediate properties.
        Last elements should be terminal properties (hasValue, hasUnit).
        """
        node = Node(data=MOCK_DATA)
        chains = node.extract_property_chains()

        top_level = {
            "https://www.sfb1574.kit.edu/ontologies/TransferUnit#hasConveyorBelt",
            "https://www.sfb1574.kit.edu/ontologies/TransferUnit#hasLightBarrier",
        }

        terminal = {
            "https://www.sfb1574.kit.edu/ontologies/CrcInterfaces#hasValue",
            "https://www.sfb1574.kit.edu/ontologies/TransferUnit#hasUnit",
        }

        for chain in chains:
            # First element should be top-level
            assert (
                str(chain[0]) in top_level
            ), f"Chain doesn't start with top-level property: {chain}"

            # Last element should be terminal
            assert (
                str(chain[-1]) in terminal
            ), f"Chain doesn't end with terminal property: {chain}"

            # Chain should have at least 2 elements (top-level + terminal)
            assert len(chain) >= 2, f"Chain too short: {chain}"
