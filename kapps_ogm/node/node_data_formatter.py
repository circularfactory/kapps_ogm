"""Data formatting and sanitization utilities for Node instances."""

from __future__ import annotations
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union
import logging

from rdflib import BNode
from kapps_triplestore_interface import IRI

if TYPE_CHECKING:
    from .core import Node
    from kapps_ogm.ogm import OGM

logger = logging.getLogger("kapps_node_formatter")
logger.setLevel(logging.INFO)


def sanitize_data(
    data: Dict,
    node: "Node",
    known_nodes: Optional[Dict[int, "Node"]] = None,
) -> tuple[dict[IRI, List[Any]], Optional[Union[IRI, BNode]]]:
    """
    Recursively converts provided data dict into a unified format.

    The output is a dict with items in any of the forms:
        - IRI: List[Any] # literal property -> list of literal values
        - IRI: List[Node] # class property -> list of Node instances with IRI ids
        - IRI: List[Node] # complex property -> list of Node instances with blank ids
    In the input, property keys can be either str or IRI. Class and complex property
    values may be represented as dicts.

    Args:
        data: Raw data dictionary to sanitize.
        ogm: OGM instance for creating nested Node instances.

    Returns:
        tuple[dict[IRI, List[Any]], Optional[Union[IRI, BNode]]]: Sanitized data with IRI keys and
            list values, and the node ID if the data carried one.

    Raises:
        ValueError: If data cannot be converted into the expected format.
    """
    from .core import Node

    if not isinstance(data, dict):
        raise ValueError("Data must be a dictionary")

    sanitized_data = {}
    node_id = None

    for property_iri, domain_list in data.items():
        # Catch special cases
        if property_iri == "id":
            # An anonymous node fetched from the store arrives already identified — as an IRI once
            # skolemised, as a BNode while still in the store's blank-node form. Either is the
            # node's actual address and must survive verbatim.
            node_id = (
                domain_list
                if isinstance(domain_list, (IRI, BNode))
                else IRI(domain_list)
            )
            continue

        try:
            property_iri = IRI(property_iri)
        except Exception as e:
            try:
                property_iri = IRI.from_lined(property_iri)
            except Exception:
                raise ValueError(f"Invalid property in data: {property_iri}") from e

        if not isinstance(domain_list, list):
            raise ValueError(
                f"Property {property_iri} data must be a list, got {type(domain_list)}"
            )

        sanitized_data[property_iri] = []

        for domain_instance in domain_list:
            if isinstance(domain_instance, Node):
                # Already a Node, use as is
                sanitized_data[property_iri].append(domain_instance)
            elif isinstance(domain_instance, dict):
                dict_id = id(domain_instance)
                if known_nodes is not None and dict_id in known_nodes:
                    # Reuse existing Node to avoid cycles
                    node = known_nodes[dict_id]
                else:
                    # Convert dict to Node
                    node = Node.from_sanitize_data(
                        ogm=node.ogm,
                        data=domain_instance,
                        known_nodes=known_nodes,
                    )

                sanitized_data[property_iri].append(node)
            else:
                sanitized_data[property_iri].append(domain_instance)
        logger.debug(
            f"Converted property {property_iri} with {len(sanitized_data[property_iri])} items to Node list"
        )

    return sanitized_data, node_id


def format_for_instance(node: Node) -> Dict[str, List[Any]]:
    """
    Recursively resolve property data to a model.

    Converts Nodes to their data, property IRIs to their lined representation.

    Args:
        node: The Node instance to format data for.

    Returns:
        Dict[str, List[Any]]: Formatted data ready for Pydantic validation.

    Notes:
        - Assigns a new ID to the node if it doesn't have one.
        - Recursively formats nested Node instances.
    """
    if not node.id:
        node.ogm._assign_id(node)

    formatted_data = {"id": node.id}
    for property_iri, domain_list in node.data.items():
        property_str = property_iri.lined
        if domain_list and isinstance(domain_list[0], node.__class__):
            formatted_data[property_str] = [
                format_for_instance(domain_item) for domain_item in domain_list
            ]
        else:
            formatted_data[property_str] = domain_list.copy()
    return formatted_data
