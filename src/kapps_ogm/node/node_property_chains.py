"""Property chain extraction utilities for Node instances."""

from __future__ import annotations
from typing import TYPE_CHECKING, Any, Union
import logging

from kapps_triplestore_interface import IRI
from kapps_triplestore_interface.exceptions import InvalidIRIError

if TYPE_CHECKING:
    from .core import Node

logger = logging.getLogger("kapps_node_chains")
logger.setLevel(logging.INFO)


def extract_property_chains(self: "Node") -> list[list[IRI | str]]:
    """
    Extract property chains from nested node data.

    Reconstructs full IRIs from lined keys using hybrid lookup:
    1. Direct IRI construction (already full IRI)
    2. _iri_fields mapping (top-level properties from ClassSpec)
    3. Decode from lined format (nested properties)
    4. Fallback to string if all fail

    Returns:
        list[list[IRI | str]]: Property chains from root to each terminal value,
                                with all keys normalized to full IRIs where possible.
    """
    property_chains: list[list[IRI | str]] = []

    if not self.data:
        logger.debug("No data to extract property chains from.")
        return property_chains

    def normalize_key(key: Union[str, IRI]) -> IRI | str:
        """
        Normalize property key to IRI with hybrid lookup strategy.

        Resolves lined keys back to full IRIs in this order:
        0. If already an IRI object, return it directly
        1. Try direct IRI construction (already full IRI)
        2. Try _iri_fields mapping lookup (fast path for known top-level props)
        3. Try decode from lined format (nested/dynamic props)
        4. Return as string if all fail
        """
        # 0. Already an IRI object
        if isinstance(key, IRI):
            return key

        # 1. Already a full IRI
        if "://" in key:
            try:
                return IRI(key)
            except (InvalidIRIError, TypeError):
                pass

        # 2. Try mapping lookup (fast path)
        if self.class_spec:
            model = self.class_spec.to_pydantic_model()
            iri_fields = getattr(model, "_iri_fields", {})
            if key in iri_fields:
                return iri_fields[key]

        # 3. Fallback: decode from lined format
        # Markers _c_, _s_, _d_, _h_ indicate a lined key
        if any(marker in key for marker in ("_c_", "_s_", "_d_", "_h_")):
            try:
                return IRI.from_lined(key)
            except (InvalidIRIError, TypeError, ValueError):
                pass

        # 4. Return as string
        return key

    def walk(value: Any, path: list[IRI | str]) -> None:
        """Recursively walk nested structure, extracting terminal property paths."""
        from .core import Node

        if not isinstance(value, list):
            return
        for item in value:
            # Handle both dict and Node items
            if isinstance(item, Node):
                # If it's a Node, use its data
                if item.data is None:
                    continue
                item_data = item.data
            elif isinstance(item, dict):
                # If it's a dict, use it directly
                item_data = item
            else:
                # Primitive value in list - don't go deeper
                continue

            # Walk through the item's properties
            for key, child_value in item_data.items():
                if key == "id":
                    continue
                new_path = path + [normalize_key(key)]
                # Check if child_value contains more nested dicts or Nodes
                has_nested_dicts = False
                if isinstance(child_value, list):
                    for sub_item in child_value:
                        if isinstance(sub_item, (dict, Node)):
                            has_nested_dicts = True
                            break
                if has_nested_dicts:
                    # Continue walking
                    walk(child_value, new_path)
                else:
                    # Terminal - child_value is primitives or empty
                    property_chains.append(new_path)

    for key, value in self.data.items():
        if key == "id":
            continue
        walk(value, [normalize_key(key)])

    return property_chains
