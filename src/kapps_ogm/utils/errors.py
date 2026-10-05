"""Exception types for the kapps_ogm package."""

from __future__ import annotations


class AnonymousNodeFetchError(ValueError):
    """Raised when OGM.fetch is called on a Skolem IRI.

    Skolem IRIs identify anonymous nodes that have no rdf:type and can only be reached
    through their parent node. Attempting to fetch them directly would bypass the structural
    context required to interpret them correctly.
    """

    pass


class AmbiguousNodeAlignmentError(ValueError):
    """Raised when a fetched node's anonymous values cannot be aligned with the outgoing ones.

    Anonymous nodes under one property are aligned positionally, since nothing else distinguishes
    them. When the outgoing list is shorter than the stored one, there is no way to tell which
    node was dropped, and guessing would shift a surviving node's address onto the wrong entry —
    silently moving one parameter's properties onto another parameter's node. That is a worse
    failure than losing an address, so it is refused.
    """


class UnresolvableNodeAddressError(ValueError):
    """Raised on the write path when the RDF address of an anonymous node cannot be resolved.

    This exception exists so that an unresolvable target can never silently become a freshly
    minted node, which would orphan every triple the ClassSpec does not declare. It protects
    referential integrity during serialization and commit operations.
    """

    pass
