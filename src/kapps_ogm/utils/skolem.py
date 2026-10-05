"""Skolem IRI minting and recognition utilities.

Per RDF 1.1 Concepts section 3.5 [^1], Skolem IRIs provide a mechanism for converting
blank nodes into globally unique IRIs without changing the meaning of the graph. Two
normative rules govern their use:

1. Skolem IRIs are globally unique and never reused — each minted IRI identifies exactly
   one anonymous node for the lifetime of the system.

2. Nothing may be asserted about a Skolem IRI — no rdf:type, no class membership, no
   annotation — because section 3.5's meaning-preservation guarantee is conditional on
   the IRI remaining opaque and uninterpreted.

The DEFAULT_SKOLEM_NAMESPACE is a placeholder default: the real minting authority is an
ontology-governance decision, which is why the namespace is configurable per OGM instance.

[^1] https://www.w3.org/TR/rdf11-concepts/#section-skolemization
"""

from __future__ import annotations

import uuid
from typing import Any

from kapps_triplestore_interface import IRI


WELL_KNOWN_GENID_PATH = "/.well-known/genid/"
DEFAULT_SKOLEM_NAMESPACE = "https://w3id.org/circularfactory/.well-known/genid/"


def validate_skolem_namespace(namespace: str) -> str:
    """Normalise a namespace and check it will mint IRIs this module can recognise again.

    Only the minting *authority* is an open governance decision; the `/.well-known/genid/` path
    is fixed by section 3.5's recognisability provision. A namespace omitting it would mint
    addresses that `is_skolem_iri` cannot identify, which would silently disable the guard in
    `OGM.fetch` — so it is rejected here rather than surfacing as a missing check later.

    Args:
        namespace: The candidate namespace. A missing trailing '/' is supplied.

    Returns:
        The namespace in canonical form, with a trailing '/'.

    Raises:
        ValueError: If the namespace does not contain WELL_KNOWN_GENID_PATH.
    """
    if not namespace.endswith("/"):
        namespace = namespace + "/"
    if WELL_KNOWN_GENID_PATH not in namespace:
        raise ValueError(
            f"Skolem namespace {namespace!r} must contain {WELL_KNOWN_GENID_PATH!r}. "
            "RDF 1.1 Concepts section 3.5 fixes that path so a Skolem IRI is recognisable "
            "outside the system that minted it; only the authority preceding it is yours to "
            "choose."
        )
    return namespace


def mint_skolem_iri(namespace: str = DEFAULT_SKOLEM_NAMESPACE) -> IRI:
    """Mint a new Skolem IRI for an anonymous node.

    Args:
        namespace: The base namespace for the Skolem IRI. If it does not end with '/',
            one will be appended before the UUID.

    Returns:
        A new IRI instance containing the namespace followed by a UUID hex string.
        Each call produces a unique value that will never be reused.

    Raises:
        ValueError: If the namespace would mint an unrecognisable IRI.
    """
    namespace = validate_skolem_namespace(namespace)
    unique_id = uuid.uuid4().hex
    return IRI(f"{namespace}{unique_id}")


def is_skolem_iri(value: Any) -> bool:
    """Check whether a value is a Skolem IRI.

    Args:
        value: The value to check.

    Returns:
        True if and only if value is an IRI instance whose string representation
        contains WELL_KNOWN_GENID_PATH. Returns False for None, plain strings,
        or any other type — never raises.
    """
    if not isinstance(value, IRI):
        return False
    return WELL_KNOWN_GENID_PATH in str(value)
