"""Validation logic for Node data structures against ClassSpec definitions."""

from __future__ import annotations
from typing import TYPE_CHECKING, Optional
import logging

from kapps_ogm.mapping.property_spec import PropertyValueKind

if TYPE_CHECKING:
    from .core import Node
    from kapps_ogm.mapping.class_spec import ClassSpec

logger = logging.getLogger("kapps_node_validator")
logger.setLevel(logging.INFO)


class NodeValidator:
    """Validates Node data against ClassSpec constraints."""

    @staticmethod
    def validate(
        node: Node,
        strict: bool = False,
    ) -> None:
        """
        Validate the nodes data against its ClassSpec.

        Args:
            node: The Node instance to validate.
            strict: If True, enforce strict validation rules. In this case, no unknown
                   properties are permitted. Defaults to False.

        Raises:
            ValueError: If validation fails or required data/ClassSpec is missing.
        """
        if node.data is None:
            raise ValueError("Node does not contain data to validate")

        if node.class_spec is None:
            raise ValueError("Cannot validate data without ClassSpec")

        # Check that node IRI is instance of ClassSpec IRI
        if node.id is not None and node.class_spec.iri is not None:
            if node.ogm.db.iri_exists(
                node.id, as_sub=True, as_pred=True, as_obj=True
            ) and not node.ogm.db.is_subclass(node.id, node.class_spec.iri):
                raise ValueError(
                    f"Node IRI {node.id} is known but is not an instance of ClassSpec {node.class_spec.iri}"
                )

        # Check that data properties conform to ClassSpec properties
        data_properties = set(node.data.keys())
        permitted_properties = set(node.class_spec.properties.keys())
        required_properties = set(
            p for p, s in node.class_spec.properties.items() if s.required
        )

        if data_properties < required_properties:
            missing_properties = required_properties - data_properties
            raise ValueError(
                f"Missing required properties in data for ClassSpec {node.class_spec.iri}: {missing_properties}"
            )

        if data_properties > permitted_properties:
            unknown_properties = data_properties - permitted_properties
            logger.warning(
                f"Unknown properties in data for ClassSpec {node.class_spec.iri}: {unknown_properties}"
            )
            if strict:
                raise ValueError(
                    f"Unknown properties in data for ClassSpec {node.class_spec.iri}: {unknown_properties}"
                )

        known_properties = data_properties & permitted_properties

        # Check each property against its specification
        for property_iri in known_properties:
            domain_list = node.data[property_iri]
            prop_spec = node.class_spec.properties[property_iri]

            # Check cardinality. sh:minCount only enforced in strict, according to the OWA.
            if prop_spec.min_count and len(domain_list) < prop_spec.min_count:
                logger.warning(
                    f"Node {node.id} property {property_iri} has fewer items ({len(domain_list)}) than min_count ({prop_spec.min_count})"
                )
                if strict:
                    raise ValueError(
                        f"Node {node.id} property {property_iri} has fewer items ({len(domain_list)}) than min_count ({prop_spec.min_count})"
                    )

            if prop_spec.max_count and len(domain_list) > prop_spec.max_count:
                logger.warning(
                    f"Node {node.id} property {property_iri} has more items ({len(domain_list)}) than max_count ({prop_spec.max_count})"
                )
                if strict:
                    raise ValueError(
                        f"Node {node.id} property {property_iri} has more items ({len(domain_list)}) than max_count ({prop_spec.max_count})"
                    )

            # Check type
            match prop_spec.value_kind:
                case PropertyValueKind.OBJECT | PropertyValueKind.COMPLEX:
                    for domain_instance in domain_list:
                        # Import here to avoid circular dependency
                        from .core import Node

                        if not isinstance(domain_instance, Node):
                            raise ValueError(
                                f"Node {node.id} property {property_iri} expected Node instances, got literal {domain_instance}"
                            )
                        # Recursively validate nested nodes
                        NodeValidator.validate(domain_instance, strict=strict)

                case PropertyValueKind.LITERAL:
                    permitted_types = prop_spec.python_range_type

                    if not permitted_types:
                        logger.debug(
                            f"PropSpec {prop_spec.iri} has no permitted types defined, skipping type check."
                        )
                        continue

                    # owl:allValuesFrom
                    if prop_spec.all_from and not all(
                        isinstance(domain_instance, permitted_types)
                        for domain_instance in domain_list
                    ):
                        raise ValueError(
                            f"Node {node.id} property {property_iri} expected owl:allValuesFrom {permitted_types}, got {type(domain_instance)}"
                        )

                    # owl:someValuesFrom. Only enforced in strict, according to the OWA.
                    if prop_spec.some_from and not any(
                        isinstance(domain_instance, permitted_types)
                        for domain_instance in domain_list
                    ):
                        logger.warning(
                            f"Node {node.id} property {property_iri} expected owl:someValuesFrom {permitted_types}, got {type(domain_instance)}"
                        )
                        if strict:
                            raise ValueError(
                                f"Node {node.id} property {property_iri} expected owl:someValuesFrom {permitted_types}, got {type(domain_instance)}"
                            )
