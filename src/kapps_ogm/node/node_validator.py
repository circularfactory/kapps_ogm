"""Validation logic for Node data structures against ClassSpec definitions."""

from __future__ import annotations
from typing import TYPE_CHECKING, Optional
import logging

from kapps_ogm.mapping.property_spec import PropertyValueKind
from kapps_ogm.utils.errors import (
    OGMDataError,
    OGMUsageError,
    PropertyOutsideScopeError,
    _one_or_none,
)

if TYPE_CHECKING:
    from .core import Node
    from kapps_ogm.mapping.class_spec import ClassSpec

logger = logging.getLogger("kapps_node_validator")
logger.setLevel(logging.INFO)


def _values_with_types(values: list) -> str:
    """Each value with its type, in order: ``'fast' (str), 7 (int)``."""
    return ", ".join(f"{value!r} ({type(value).__name__})" for value in values)


def _type_names(values: list) -> str:
    """The distinct types of the values, in order of first appearance."""
    return ", ".join(dict.fromkeys(type(value).__name__ for value in values))


class NodeValidator:
    """Validates Node data against ClassSpec constraints."""

    @staticmethod
    def refuse(node: Node, unknown_properties: set) -> None:
        """Raise for properties that the node's (scoped) ClassSpec does not hold.

        A property that the class does not declare raises ``OGMDataError``. Otherwise the class
        declares it and the scope leaves it out, and ``PropertyOutsideScopeError`` says so. The
        class's own properties are read only here, when a property is refused. A ClassSpec
        without an IRI (an anonymous node) or a node without an OGM cannot be asked, and gets an
        error that does not say which kind it is."""
        class_spec = node.class_spec
        assert class_spec is not None
        if class_spec.iri is None or node.ogm is None:
            # The class cannot be asked, so the message claims nothing about which kind it is.
            raise OGMDataError(
                f"Node {node.id!r} data names properties that ClassSpec {class_spec.iri} does not hold: "
                f"{unknown_properties}. The class does not declare them, or the scope leaves them out.",
                instance_iri=node.id,
                property_iri=_one_or_none(unknown_properties),
                class_iri=class_spec.iri,
            )
        declared = class_spec.declared_properties(class_spec.iri, node.ogm)
        undeclared = unknown_properties - declared
        if undeclared:
            raise OGMDataError(
                f"Node {node.id!r} data names properties that ClassSpec {class_spec.iri} does not declare: {undeclared}",
                instance_iri=node.id,
                property_iri=_one_or_none(undeclared),
                class_iri=class_spec.iri,
            )
        scope = set(class_spec.properties)
        raise PropertyOutsideScopeError(
            f"Node {node.id!r} data names properties that class {class_spec.iri} declares but the class scope leaves out: "
            f"{unknown_properties}. The scope names {sorted(scope)}. A write is confined to its scope: "
            "add the property to the scope, or remove it from the data.",
            instance_iri=node.id,
            property_iri=_one_or_none(unknown_properties),
            class_iri=class_spec.iri,
            scope=scope,
        )

    @staticmethod
    def validate(
        node: Node,
        strict: bool = False,
        *,
        refuse_unknown: bool = False,
    ) -> None:
        """
        Validate the nodes data against its ClassSpec.

        Args:
            node: The Node instance to validate.
            strict: If True, enforce strict validation rules. In this case, no unknown
                   properties are permitted. The write paths of the OGM never pass it, so
                   minimum counts stay unenforced on write. Defaults to False.
            refuse_unknown: If True, a property that the ClassSpec does not declare raises,
                   in this node and in every nested node, and nothing else becomes stricter.
                   The write paths of the OGM pass it; the read path does not, because a graph
                   may carry more than any schema declares. Defaults to False.

        Raises:
            OGMDataError: If validation fails.
            OGMUsageError: If the node has no data or no ClassSpec.
        """
        if node.data is None:
            raise OGMUsageError("Node does not contain data to validate")

        if node.class_spec is None:
            raise OGMUsageError("Cannot validate data without ClassSpec")

        # Check that data properties conform to ClassSpec properties
        data_properties = set(node.data.keys())
        permitted_properties = set(node.class_spec.properties.keys())
        required_properties = set(
            p for p, s in node.class_spec.properties.items() if s.required
        )

        if data_properties < required_properties:
            missing_properties = required_properties - data_properties
            raise OGMDataError(
                f"Missing required properties in data for ClassSpec {node.class_spec.iri}: {missing_properties}",
                instance_iri=node.id,
                property_iri=_one_or_none(missing_properties),
                class_iri=node.class_spec.iri,
            )

        unknown_properties = data_properties - permitted_properties
        if unknown_properties:
            if strict or refuse_unknown:
                NodeValidator.refuse(node, unknown_properties)
            logger.warning(
                f"Node {node.id!r} data names properties that ClassSpec {node.class_spec.iri} does not declare, ignored: {unknown_properties}"
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
                    raise OGMDataError(
                        f"Node {node.id} property {property_iri} has fewer items ({len(domain_list)}) than min_count ({prop_spec.min_count})",
                        instance_iri=node.id,
                        property_iri=property_iri,
                        class_iri=node.class_spec.iri,
                        value=domain_list,
                    )

            if prop_spec.max_count and len(domain_list) > prop_spec.max_count:
                logger.warning(
                    f"Node {node.id} property {property_iri} has more items ({len(domain_list)}) than max_count ({prop_spec.max_count})"
                )
                if strict:
                    raise OGMDataError(
                        f"Node {node.id} property {property_iri} has more items ({len(domain_list)}) than max_count ({prop_spec.max_count})",
                        instance_iri=node.id,
                        property_iri=property_iri,
                        class_iri=node.class_spec.iri,
                        value=domain_list,
                    )

            # Check type
            match prop_spec.value_kind:
                case PropertyValueKind.OBJECT | PropertyValueKind.COMPLEX:
                    for domain_instance in domain_list:
                        # Import here to avoid circular dependency
                        from .core import Node

                        if not isinstance(domain_instance, Node):
                            raise OGMDataError(
                                f"Node {node.id} property {property_iri} expected Node instances, got literal {domain_instance}",
                                instance_iri=node.id,
                                property_iri=property_iri,
                                class_iri=node.class_spec.iri,
                                value=domain_instance,
                            )
                        # Recursively validate nested nodes
                        NodeValidator.validate(
                            domain_instance,
                            strict=strict,
                            refuse_unknown=refuse_unknown,
                        )

                case PropertyValueKind.LITERAL:
                    permitted_types = prop_spec.python_range_type

                    if not permitted_types:
                        logger.debug(
                            f"PropSpec {prop_spec.iri} has no permitted types defined, skipping type check."
                        )
                        continue

                    # owl:allValuesFrom
                    if prop_spec.all_from:
                        offending = [
                            value
                            for value in domain_list
                            if not isinstance(value, permitted_types)
                        ]
                        if offending:
                            raise OGMDataError(
                                f"Node {node.id} property {property_iri} expected owl:allValuesFrom {permitted_types}, got {_values_with_types(offending)}",
                                instance_iri=node.id,
                                property_iri=property_iri,
                                class_iri=node.class_spec.iri,
                                value=domain_list,
                            )

                    # owl:someValuesFrom. Only enforced in strict, according to the OWA.
                    if prop_spec.some_from and not any(
                        isinstance(value, permitted_types) for value in domain_list
                    ):
                        message = f"Node {node.id} property {property_iri} expected owl:someValuesFrom {permitted_types}, got values of type {_type_names(domain_list)}"
                        logger.warning(message)
                        if strict:
                            raise OGMDataError(
                                message,
                                instance_iri=node.id,
                                property_iri=property_iri,
                                class_iri=node.class_spec.iri,
                                value=domain_list,
                            )
