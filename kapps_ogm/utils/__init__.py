"""Utilities module for OGM operations."""

from .constants import PROPERTY_TYPES, PROPERTY_CHARACTERISTICS
from .pretty_print import (
    format_triples_turtle,
    format_node_data,
    format_property_spec,
    format_class_spec,
)
from .json_ogm_encoder import OGMEncoder
from .class_scope import ClassScope
from .errors import (
    AmbiguousNodeAlignmentError,
    AnonymousNodeFetchError,
    UnresolvableNodeAddressError,
)
from .skolem import (
    DEFAULT_SKOLEM_NAMESPACE,
    WELL_KNOWN_GENID_PATH,
    is_skolem_iri,
    mint_skolem_iri,
    validate_skolem_namespace,
)

__all__ = [
    "PROPERTY_TYPES",
    "PROPERTY_CHARACTERISTICS",
    "format_triples_turtle",
    "format_node_data",
    "format_property_spec",
    "format_class_spec",
    "OGMEncoder",
    "ClassScope",
    "AmbiguousNodeAlignmentError",
    "AnonymousNodeFetchError",
    "UnresolvableNodeAddressError",
    "DEFAULT_SKOLEM_NAMESPACE",
    "WELL_KNOWN_GENID_PATH",
    "is_skolem_iri",
    "mint_skolem_iri",
    "validate_skolem_namespace",
]
