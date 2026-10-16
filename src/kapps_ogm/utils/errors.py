"""Exception types for the kapps_ogm package.

The OGM's own failures have one base, ``OGMError``, and four categories. Each category names what
the caller must do, and each one also subclasses the built-in that it replaces, so an
``except ValueError`` or ``except TypeError`` keeps catching it:

- ``OGMDataError``: the data breaks the class. Refused.
- ``OGMOntologyError``: the ontology has a shape that the OGM cannot map. Repair.
- ``OGMNotFoundError``: the instance or the node does not exist, or the OGM cannot resolve it.
  Refresh.
- ``OGMUsageError``, and ``OGMUsageTypeError`` for a wrong argument type or form: the API was
  called incorrectly. Fix the call.

The OGM raises them only where it detects the condition. An error of the triple store interface
reaches the caller unchanged, as the same object. A message stays as the OGM wrote it, and the
attributes give the raw context: they interpret nothing, and each is ``None`` where it does not
apply.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from kapps_triplestore_interface import IRI


def _one_or_none(items: Iterable[Any]) -> Any:
    """The item of a collection that holds exactly one, and otherwise None.

    A message that names a set of properties gives ``property_iri`` only when the set holds one
    property: the attribute names one property, and the OGM does not choose one of several.
    """
    items = list(items)
    return items[0] if len(items) == 1 else None


class OGMError(Exception):
    """The base of the OGM's own errors. Catch a category to choose the action."""


class OGMDataError(OGMError, ValueError):
    """The data breaks the class. Action: Refused.

    A required property is missing, a property is unknown, a count is wrong, or a value has the
    wrong type. The OGM and the request are correct, but the class does not permit the data:
    change the data, or ask a domain expert if the class is wrong.

    Attributes:
        instance_iri: The instance whose data breaks the class.
        property_iri: The property, when the condition names one.
        class_iri: The class that the data breaks.
        value: The value or values that break the class.
    """

    def __init__(
        self,
        message: str,
        *,
        instance_iri: Optional[IRI] = None,
        property_iri: Optional[IRI] = None,
        class_iri: Optional[IRI] = None,
        value: Any = None,
    ):
        super().__init__(message)
        self.instance_iri = instance_iri
        self.property_iri = property_iri
        self.class_iri = class_iri
        self.value = value


class OGMOntologyError(OGMError, ValueError):
    """The ontology has a shape that the OGM cannot map. Action: Repair.

    A class or a property has no type, a property has no range, its ranges do not merge, or the
    OGM does not support its range. The data and the call may be correct: the operator repairs
    the ontology, or the KAPPS maintainers extend the OGM.

    Attributes:
        class_iri: The class whose shape the OGM cannot map.
        property_iri: The property whose shape the OGM cannot map.
    """

    def __init__(
        self,
        message: str,
        *,
        class_iri: Optional[IRI] = None,
        property_iri: Optional[IRI] = None,
    ):
        super().__init__(message)
        self.class_iri = class_iri
        self.property_iri = property_iri


class OGMNotFoundError(OGMError, ValueError):
    """The instance or the node does not exist, or the OGM cannot resolve it. Action: Refresh.

    The caller's information is old: read the state again, then decide. The three node errors,
    ``AnonymousNodeFetchError``, ``UnresolvableNodeAddressError`` and
    ``AmbiguousNodeAlignmentError``, are subclasses.

    Attributes:
        iri: The IRI that the OGM could not find or resolve, or the IRI of the instance under
            which it could not resolve a node.
    """

    def __init__(self, message: str, *, iri: Optional[IRI] = None):
        super().__init__(message)
        self.iri = iri


class OGMUsageError(OGMError, ValueError):
    """The API was called incorrectly. Action: Fix the call.

    The OGM raises it where it raised a ``ValueError`` for a wrong call before: for example, two
    changes of one ``commit`` that write one instance, or a ``ClassSpec`` without an IRI to
    hydrate. The consumer's developer fixes the call; the same call fails again.
    """


class OGMUsageTypeError(OGMUsageError, TypeError):
    """The API was called with a wrong argument type or form. Action: Fix the call.

    The OGM raises it where it raised a ``TypeError`` before: for example, ``commit()`` with
    neither or both of its two forms, or a change that is not a ``Create`` or an ``Update``. It is
    a ``TypeError`` and an ``OGMUsageError``, and so also a ``ValueError``.
    """


class AnonymousNodeFetchError(OGMNotFoundError):
    """Raised when OGM.fetch is called on a Skolem IRI. An ``OGMNotFoundError``: Refresh.

    Skolem IRIs identify anonymous nodes that have no rdf:type and can only be reached
    through their parent node. Attempting to fetch them directly would bypass the structural
    context required to interpret them correctly.
    """

    pass


class AmbiguousNodeAlignmentError(OGMNotFoundError):
    """Raised when a fetched node's anonymous values cannot be aligned with the outgoing ones.
    An ``OGMNotFoundError``: Refresh.

    Anonymous nodes under one property are aligned positionally, since nothing else distinguishes
    them. When the outgoing list is shorter than the stored one, there is no way to tell which
    node was dropped, and guessing would shift a surviving node's address onto the wrong entry —
    silently moving one parameter's properties onto another parameter's node. That is a worse
    failure than losing an address, so it is refused.
    """


class UnresolvableNodeAddressError(OGMNotFoundError):
    """Raised on the write path when the RDF address of an anonymous node cannot be resolved.
    An ``OGMNotFoundError``: Refresh.

    This exception exists so that an unresolvable target can never silently become a freshly
    minted node, which would orphan every triple the ClassSpec does not declare. It protects
    referential integrity during serialization and commit operations.
    """

    pass
