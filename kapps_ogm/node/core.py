"""Core Node class for managing RDF-backed entity instances."""

from __future__ import annotations
from typing import TYPE_CHECKING, Any, Dict, Optional, TypeVar, Union, List, Tuple
import logging
import json

from pydantic import BaseModel, GetCoreSchemaHandler
from pydantic_core import CoreSchema, core_schema
from rdflib import BNode
from kapps_triplestore_interface import IRI
from kapps_triplestore_interface.utils import utils
from kapps_triplestore_interface.utils.types import IRILike, Triple

from kapps_ogm.mapping.property_spec import PropertyValueKind

from .node_validator import NodeValidator
from .node_data_formatter import sanitize_data, format_for_instance

if TYPE_CHECKING:
    from kapps_ogm.mapping.class_spec import ClassSpec
    from kapps_ogm.ogm import OGM

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger("kapps_node")
logger.setLevel(logging.INFO)


class Node:
    """Lightweight runtime handle for an RDF-backed instance.

    A Node represents an entity identified by an IRI and may carry:
    - a ClassSpec describing its schema,
    - raw loaded data,
    - a materialized Pydantic instance.

    It coordinates the entity lifecycle, including lazy loading and
    materialization, without embedding persistence logic itself.
    """

    def __init__(
        self,
        *,
        id: Optional[Union[str, IRI, BNode]] = None,
        class_spec: Optional["ClassSpec"] = None,
        data: Optional[Dict[IRILike, List[Any]]] = None,
        instance: Optional[T] = None,
        ogm: Optional[OGM] = None,
    ):
        """
        Initialize a node with identity and optional state.

        Either an explicit IRI/BNode or an instance exposing an ``id`` attribute
        must be provided. If both are given, they must match.
        """
        if instance is not None and hasattr(instance, "id") and instance.id is not None:
            if id is None:
                id = instance.id
            elif id != instance.id:
                raise ValueError("Provided id does not match instance id")

        if data is not None and "id" in data and data["id"] is not None:
            if id is None:
                id = data["id"]
            elif id != data["id"]:
                raise ValueError("Provided id does not match instance id")

        if id is not None and not isinstance(id, (IRI, BNode)):
            id = IRI(id)

        # Core attributes - use provided id or instance id
        self.ogm = ogm
        self.id = id
        self.data = data
        self.class_spec = class_spec

        self.instance = instance

    # -------------------------
    # Lifecycle helpers
    # -------------------------

    @property
    def is_materialized(self) -> bool:
        """Check if the node has a materialized Pydantic instance."""
        return self.instance is not None

    @property
    def has_data(self) -> bool:
        """Check if the node has loaded data."""
        return bool(self._data)

    # -------------------------
    # Data access
    # -------------------------

    @property
    def data(self) -> Optional[Dict[IRI, List[Any]]]:
        """
        Get the raw instance data for this node.

        Returns:
            Optional[Dict[IRI, List[Any]]]: Raw instance data if loaded, else None.
        """
        return self._data

    @data.setter
    def data(self, value: Optional[Dict[IRILike, List[Any]]]) -> None:
        """
        Set or update the raw data for this node.

        Args:
            value (Optional[Dict[IRILike, List[Any]]]): Raw instance data to set on the node.
        """
        self._set_data_check_cyclic(value)

    @classmethod
    def from_sanitize_data(
        cls,
        ogm: OGM,
        data: Dict[IRILike, List[Any]],
        known_nodes: Optional[Dict[int, "Node"]] = None,
    ) -> "Node":
        node = cls(ogm=ogm)
        node._set_data_check_cyclic(data, known_nodes=known_nodes)
        return node

    def _set_data_check_cyclic(
        self,
        data: Optional[Dict[IRILike, List[Any]]],
        known_nodes: Optional[Dict[int, "Node"]] = None,
    ) -> None:
        if data is None:
            self._data = None
            return

        if known_nodes is None:
            known_nodes = {}
        known_nodes[id(data)] = self
        # Sanitize data through the formatter
        sanitized, node_id = sanitize_data(
            data=data, node=self, known_nodes=known_nodes
        )
        self._data = sanitized

        # Update node ID if found in data
        if node_id is not None:
            self.id = node_id

    # -------------------------
    # Materialization
    # -------------------------

    @property
    def class_spec(self) -> Optional["ClassSpec"]:
        """Get the ClassSpec associated with this node."""
        return self._class_spec

    @class_spec.setter
    def class_spec(self, value: Optional["ClassSpec"]) -> None:
        """Set or update the ClassSpec associated with this node."""
        self._recursive_update_nodes_class_spec(value)

    def _recursive_update_nodes_class_spec(self, class_spec: "ClassSpec") -> None:
        """
        Recursively set or update the ClassSpecs for the nodes contained in this nodes data.

        Args:
            class_spec (ClassSpec): The ClassSpec to assign to this node.
        """
        self._class_spec = class_spec

        if class_spec is None:
            return

        if not self.has_data:
            return

        if class_spec is None:
            raise ValueError(f"Node {self} has data but no matching ClassSpec")

        # Recursively set class_specs for nested nodes
        # Find properties present both in data and class_spec that contain nodes, and are OBJECT or COMPLEX respectively
        data_properties = set(
            prop_iri
            for prop_iri, domain_list in self.data.items()
            if domain_list
            and any(
                isinstance(domain_instance, Node) for domain_instance in domain_list
            )
        )
        spec_properties = set(
            prop_iri
            for prop_iri, prop_spec in class_spec.properties.items()
            if prop_spec.value_kind
            in {PropertyValueKind.OBJECT, PropertyValueKind.COMPLEX}
        )
        unknown_properties = data_properties - spec_properties
        if unknown_properties:
            # TODO This error is thrown before self.__repr__ is possible, as self.instance is not yet set.
            raise ValueError(
                f"Node {self} data contains OBJECT or COMPLEX properties not defined in ClassSpec {class_spec}: {unknown_properties}"
            )
        known_properties = data_properties & spec_properties

        # Check each property against its specification
        for property_iri in known_properties:
            domain_list = self.data[property_iri]
            prop_spec = class_spec.properties[property_iri]

            for domain_instance in domain_list:
                if not isinstance(domain_instance, Node):
                    raise ValueError(
                        f"Node {self} property {property_iri} expected Node instances, got literal {domain_instance}"
                    )

                domain_instance.class_spec = prop_spec.nested

    def materialize(self, *, reload: bool = False) -> BaseModel:
        """
        Return a validated Pydantic model instance for this node.

        Lazily materializes the nodes loaded data into a Pydantic model on first
        access and caches the result. Subsequent calls return the cached instance
        without reprocessing.

        If data has not yet been loaded, it is loaded automatically before
        materialization.

        If ``reload`` is True, any cached instance is discarded. The nodes data
        is reloaded from the database and a new instance is materialized.

        Returns:
            BaseModel: A validated Pydantic model corresponding to the nodes
            ClassSpec. The instance is cached on the node.

        Raises:
            RuntimeError: If no OGM is attached to load data.
            ValidationError: If the loaded data violates the ClassSpec constraints.

        Notes:
            - This is the preferred way to access node data in application code.
            - Modifications to the returned instance must be persisted explicitly
            via the OGM.
        """
        if self.instance is not None and not reload:
            return self.instance
        if self.data is None:
            raise RuntimeError("Node has no data")
        if self.class_spec is None:
            raise RuntimeError("Node has no ClassSpec")

        # Validate data
        NodeValidator.validate(self)

        # Format data for Pydantic
        formatted_data = format_for_instance(self)

        model_cls = self.class_spec.to_pydantic_model()
        self.instance = model_cls.model_validate(formatted_data)
        return self.instance

    # -------------------------
    # Change handling
    # -------------------------

    def diff(self, other: Node) -> Tuple[Tuple[Triple], Tuple[Triple]]:
        """
        Compute the triples to update this node to match new_node.

        Args:
            other (Node): The target node state to commit to.
        Returns:
            Tuple[Tuple[Triple], Tuple[Triple]]: A tuple containing two tuples:
                - old triples to remove
                - new triples to add
        Raises:
            NotImplementedError: If other is not a Node instance.
        """

        if not isinstance(other, Node):
            raise NotImplementedError("Can only diff against another Node instance")

        old_node_triples = utils.group_triples_by_bnode(set(self.to_triples()))
        new_node_triples = utils.group_triples_by_bnode(set(other.to_triples()))

        old_triples = set()
        new_triples = set()
        for triple_set in old_node_triples:
            if triple_set not in new_node_triples:
                old_triples.update(triple_set)
        for triple_set in new_node_triples:
            if triple_set not in old_node_triples:
                new_triples.update(triple_set)

        return old_triples, new_triples

    # -------------------------
    # Serialization (delegated)
    # -------------------------

    from .node_serializer import to_triples, to_json_ld

    # -------------------------
    # Property chain extraction (delegated)
    # -------------------------

    from .node_property_chains import extract_property_chains

    # -------------------------
    # Debugging utilities
    # -------------------------

    def log_data_debug(self) -> None:
        """Pretty print data for debugging."""
        if logger.isEnabledFor(logging.DEBUG) and self.data:
            from kapps_ogm.utils.pretty_print import format_node_data

            logger.debug(json.dumps(format_node_data(self.data), indent=2))

    # -------------------------
    # Representation
    # -------------------------

    def __repr__(self) -> str:
        """
        Return a concise string representation of the node for debugging.

        The representation reflects the nodes lifecycle state:
        - Materialized nodes include the Pydantic instance representation.
        - Unmaterialized nodes include the node IRI and flags indicating whether
        data and a ClassSpec are present.

        Returns:
            str: A human-readable representation of the nodes current state.
        """
        if self.instance:
            return f"Node<instance {self.instance!r}>"
        return (
            f"Node<ref {self.id!r}, "
            f"data={self.has_data}, "
            f"class_spec={self.class_spec is not None}>"
        )

    # -------------------------
    # Pydantic integration
    # -------------------------

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        source_type: Any,
        handler: GetCoreSchemaHandler,
    ) -> CoreSchema:
        """
        Provide a permissive Pydantic core schema for IRI fields.

        Args:
            source_type (Any): The source type passed by Pydantic.
            handler (GetCoreSchemaHandler): Pydantic schema handler.

        Returns:
            CoreSchema: A schema accepting any value (validated by IRI itself).
        """
        return core_schema.any_schema()
