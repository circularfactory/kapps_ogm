"""Serialization utilities for Node instances to RDF and JSON-LD formats."""

from __future__ import annotations
from typing import TYPE_CHECKING, Any, Dict, Optional, Union
from collections import defaultdict

from rdflib import BNode, Literal
from kapps_triplestore_interface import IRI, to_literal
from kapps_triplestore_interface.utils.types import Triple

from kapps_ogm.utils.errors import UnresolvableNodeAddressError
from kapps_ogm.utils.skolem import WELL_KNOWN_GENID_PATH

if TYPE_CHECKING:
    from .core import Node


def to_triples(self: "Node") -> set[Triple]:
    """Serialize the nodes current instance into RDF triples for persistence.

    Uses the in-memory materialized instance and cached data. Updates in
    memory will be reflected in the serialized triples.

    Args:
        node: The Node instance to serialize.

    Returns:
        set[Triple]: RDF triples representing this node, including nested objects.

    Raises:
        RuntimeError: If the node has no ClassSpec or IRI.

    Notes:
        - Intended for persisting the current instance state.
        - Always uses the cached instance; does not refresh from the database.
    """
    if self.instance is None:
        raise RuntimeError("Node must be materialized before calling to_triples")

    iri_field_map: dict[str, IRI] = getattr(self.instance.__class__, "_iri_fields", {})

    # We need either a ClassSpec or _iri_fields to know how to serialize
    if self.class_spec is None and not iri_field_map:
        raise RuntimeError(
            "Node must have a ClassSpec or _iri_fields to serialize to triples"
        )

    triples: set[Triple] = set()

    subject = self.id
    if subject is None:
        raise RuntimeError("Node has no IRI")

    # Add type triples only for named classes (those with a class IRI)
    if self.class_spec and self.class_spec.iri:
        triples.add((subject, "rdf:type", self.class_spec.iri))
        triples.add((subject, "rdf:type", "owl:NamedIndividual"))

    # Serialize all properties from the Pydantic model
    for field_name, prop_iri in iri_field_map.items():
        value = getattr(self.instance, field_name, None)
        if value is None:
            continue

        # Always treat as list
        values = value if isinstance(value, list) else [value]

        # Node.data is the authoritative carrier of an anonymous node's address. Its entries are
        # positionally aligned with the materialized instance's, because format_for_instance
        # builds the payload by walking the same lists in the same order.
        data_values = (self.data or {}).get(prop_iri) or []

        for index, v in enumerate(values):
            triples |= _value_to_triples(
                self=self,
                subject=subject,
                predicate=prop_iri,
                value=v,
                data_node=data_values[index] if index < len(data_values) else None,
            )

    return triples


def to_json_ld(
    self: "Node", context: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """
    Serialize the nodes instance into JSON-LD format.

    Converts RDF triples to JSON-LD with proper handling of IRIs, blank nodes,
    and literals. Blank nodes are inlined recursively for cleaner output.

    Args:
        node: The Node instance to serialize.
        context: Optional @context dictionary mapping prefixes to namespace URIs.
                If provided, URIs are compacted using these prefixes.

    Returns:
        Dict[str, Any]: JSON-LD document with @context and @graph keys.
                        Main subject appears first in @graph, followed by other
                        named nodes. Blank nodes are inlined as nested objects.

    Notes:
        - Calls to_triples() internally to obtain RDF representation
        - Literal datatypes are preserved as native Python types
        - Multiple property values are represented as JSON arrays
        - Context prefixes are registered globally in IRI.PREFIXES
    """
    triples = to_triples(self)

    # Register context prefixes for compaction
    if context:
        for prefix, namespace in context.items():
            IRI.add_prefix(prefix, namespace)

    def is_anonymous_ref(ref: str) -> bool:
        """Whether a reference stands in for a blank node and should therefore be inlined.

        A Skolem IRI *is* a blank node's stand-in (RDF 1.1 Concepts §3.5), so it inlines exactly
        as the blank node it replaced did. Keeping it inline also keeps the address out of this
        projection, which R4 requires of everything served northbound.
        """
        return ref.startswith("genid-") or WELL_KNOWN_GENID_PATH in ref

    def compact_iri(iri_str: str) -> str:
        """Compact a full IRI to prefix:name form if possible."""
        if is_anonymous_ref(iri_str):
            return iri_str  # Don't compact blank nodes or their Skolem stand-ins
        try:
            iri = IRI(iri_str)
            return iri.short
        except:
            return iri_str

    # Group triples by subject
    subjects: Dict[Union[str, BNode], Dict[str, Any]] = defaultdict(dict)
    blank_nodes: set[BNode] = set()

    for s, p, o in triples:
        # Track blank nodes
        if isinstance(s, BNode):
            blank_nodes.add(s)
        if isinstance(o, BNode):
            blank_nodes.add(o)

        # Convert subject to string
        subj_str = str(s) if isinstance(s, (IRI, BNode)) else s

        # Handle type declarations
        if p == "rdf:type" or (
            isinstance(p, IRI) and str(p).endswith("rdf-syntax-ns#type")
        ):
            if "@type" not in subjects[subj_str]:
                subjects[subj_str]["@type"] = []
            # Convert object to string and compact
            type_val = compact_iri(str(o)) if isinstance(o, IRI) else o
            if type_val not in subjects[subj_str]["@type"]:
                subjects[subj_str]["@type"].append(type_val)
            continue

        # Convert predicate to string and compact
        pred_str = compact_iri(str(p)) if isinstance(p, IRI) else p

        # Convert object to appropriate JSON-LD value
        if isinstance(o, Literal):
            # Handle rdflib Literal with datatype
            value = o.toPython()  # Convert to native Python type
        elif isinstance(o, BNode):
            # Blank node - will be inlined later
            value = {"@id": str(o)}
        elif isinstance(o, IRI):
            # IRI reference - compact the IRI
            value = {"@id": compact_iri(str(o))}
        else:
            # Plain value
            value = o

        # Handle multiple values per predicate
        if pred_str in subjects[subj_str]:
            existing = subjects[subj_str][pred_str]
            if isinstance(existing, list):
                existing.append(value)
            else:
                subjects[subj_str][pred_str] = [existing, value]
        else:
            subjects[subj_str][pred_str] = value

    # Inline blank nodes recursively
    def inline_blank_nodes(node_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Recursively inline blank node references."""
        result = {}
        for key, value in node_dict.items():
            if key == "@id":
                # Keep @id as is
                result[key] = value
            elif isinstance(value, dict) and "@id" in value:
                # Check if this is a blank node reference
                ref_id = value["@id"]
                if ref_id in subjects and is_anonymous_ref(ref_id):
                    # Inline the blank node
                    blank_node_data = subjects[ref_id].copy()
                    # Don't include @id for inline blank nodes
                    blank_node_data.pop("@id", None)
                    # Recursively inline nested blank nodes
                    result[key] = inline_blank_nodes(blank_node_data)
                else:
                    # Keep as reference
                    result[key] = value
            elif isinstance(value, list):
                # Handle arrays
                result[key] = [
                    (
                        inline_blank_nodes(item)
                        if isinstance(item, dict)
                        and "@id" in item
                        and item["@id"] in subjects
                        and is_anonymous_ref(item["@id"])
                        else (
                            inline_blank_nodes(subjects[item["@id"]].copy())
                            if isinstance(item, dict)
                            and "@id" in item
                            and is_anonymous_ref(item["@id"])
                            else item
                        )
                    )
                    for item in value
                ]
            else:
                result[key] = value
        return result

    # Build JSON-LD graph with blank nodes inlined
    json_ld_nodes = []
    main_subject = str(self.id)

    # Add main subject first
    if main_subject in subjects:
        json_node = {"@id": compact_iri(main_subject)}
        json_node.update(inline_blank_nodes(subjects[main_subject]))
        json_ld_nodes.append(json_node)

    # Add other named (non-blank) nodes
    for subj, props in subjects.items():
        if subj != main_subject and not is_anonymous_ref(str(subj)):
            json_node = {"@id": compact_iri(subj)}
            json_node.update(inline_blank_nodes(props))
            json_ld_nodes.append(json_node)

    return {"@context": context or {}, "@graph": json_ld_nodes}


def _resolve_anonymous_address(
    self: "Node",
    predicate: IRI,
    value: Any,
    data_node: Any,
) -> Any:
    """
    Resolve the RDF address of an anonymous nested node. Never mints one.

    An anonymous node is addressed rather than re-created, so that a write touches only what
    actually changed and leaves undeclared triples — MQTT topics, broker addresses, anything the
    range restriction does not declare — attached to the node that carries them.

    Resolution order:
        1. ``Node.data`` — the authoritative carrier, populated at fetch and by ``_assign_id``.
        2. the ``_node_iri`` mirror on an ``AnonymousNodeModel``, for a node not reachable through
           ``Node.data`` (a doubly-nested anonymous node serializes from its instance alone).
        3. raise.

    Minting belongs to ``OGM._assign_id``, which runs only for a node that has no address yet.
    Minting here would silently turn an unresolvable target into a new node and orphan every
    triple the ClassSpec does not declare — the exact failure this resolution order exists to
    prevent.

    Args:
        self: The parent Node instance.
        predicate: The property IRI relating the parent to this value.
        value: The nested Pydantic model being serialized.
        data_node: The Node recorded for this value in the parent's data, if any.

    Returns:
        The resolved address, an IRI (or a BNode for a node still held in the store's blank-node
        form, which the next write relocates to a Skolem IRI).

    Raises:
        UnresolvableNodeAddressError: If no address can be resolved.
    """
    from .core import Node

    if isinstance(data_node, Node) and data_node.id is not None:
        return data_node.id

    mirrored_address = getattr(value, "_node_iri", None)
    if mirrored_address is not None:
        return mirrored_address

    raise UnresolvableNodeAddressError(
        f"Cannot resolve the address of the anonymous node under {predicate} on {self.id}. "
        "It is absent from Node.data and carries no mirrored address, so writing it would "
        "silently create a new node and orphan the triples attached to the existing one. "
        "Materialize through the OGM (fetch or commit) so the address is carried."
    )


def _value_to_triples(
    self: "Node",
    subject: IRI,
    predicate: IRI,
    value: Any,
    data_node: Any = None,
) -> set[Triple]:
    """
    Convert a property value into RDF triples.

    Handles nested Pydantic objects (serialized to their own IRI, or to the resolved address of
    an anonymous node), IRI references (linked directly), and literals (converted with type
    annotations). Nested objects are serialized recursively.

    Args:
        node: The parent Node instance (for accessing ogm).
        subject: The subject IRI for the generated triples.
        predicate: The property IRI relating subject to value.
        value: The value to serialize (Pydantic model, IRI, or primitive).
        data_node: The Node recorded for this value in the parent's data, if any. Carries the
            address of an anonymous node.

    Returns:
        set[Triple]: RDF triples representing the value. Single triple for
                    literals/IRIs, multiple for nested objects.

    Notes:
        - Internal helper called by to_triples()
        - Anonymous nodes are addressed, never re-minted; see _resolve_anonymous_address.
    """
    from pydantic import BaseModel
    from .core import Node

    triples: set[Triple] = set()

    # Case 1: Nested Pydantic object
    if isinstance(value, BaseModel):
        # Check if nested object has its own ID (named instance)
        nested_id = getattr(value, "id", None)
        if nested_id:
            # Use the object's own IRI
            obj = nested_id if isinstance(nested_id, IRI) else IRI(nested_id)
        else:
            # Anonymous nested object: resolve its address, never mint a replacement
            obj = _resolve_anonymous_address(
                self=self,
                predicate=predicate,
                value=value,
                data_node=data_node,
            )

        triples.add((subject, predicate, obj))

        # If the nested object only provides an id and has no schema info,
        # relax serialization by emitting a reference triple only.
        iri_field_map: dict[str, IRI] = getattr(value.__class__, "_iri_fields", {})
        if nested_id and not iri_field_map:
            return triples

        # Recurse - nested object serializes its own properties
        property_spec = (
            self.class_spec.properties.get(predicate, None) if self.class_spec else None
        )
        class_spec = (
            property_spec.nested if (property_spec and property_spec.nested) else None
        )

        nested_node = Node(
            id=obj,
            instance=value,
            class_spec=class_spec,  # Properties encoded in Pydantic model
            # Carry the recorded data down so anonymous nodes nested below this one can resolve
            # their own addresses from the authoritative carrier rather than the model mirror.
            data=data_node.data if isinstance(data_node, Node) else None,
            ogm=self.ogm,
        )
        triples |= to_triples(nested_node)

    # Case 2: IRI object
    elif isinstance(value, IRI):
        triples.add((subject, predicate, value))

    # Case 3: Literal
    else:
        triples.add((subject, predicate, to_literal(value)))

    return triples
