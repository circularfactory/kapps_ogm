"""The interfaces of a parameter property, resolved per instance, and the merge depth.

A parameter property's range gives its node the domain shape: a value and its unit. An
**interface** is a property whose own ``rdfs:range`` adds to that shape, for example the
connection details of one protocol. It contributes to a parameter node from three sources,
merged as a union:

1. **ABox evidence**: the stored node carries a property that the range of an interface
   restricts, and that the shape does not declare yet.
2. **A caller's declaration**: an ``Interface``, which resolves the parameter property as if it
   were ``rdfs:subPropertyOf`` the interface. This is how a first write knows the shape.
3. **A TBox marker**: an ``rdfs:subPropertyOf`` assertion, which the walk over the property's
   superproperties follows.

The **merge depth** selects the view. It counts the levels of the interface hierarchy from its
most general property down: a property without a superproperty is at level 1, and each other
property is one level below its deepest superproperty. The parameter property's own range is
always merged. A depth of 1 merges the most general interface only; no depth merges all of them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable, Mapping, Optional, cast

from kapps_triplestore_interface import IRI, SPARQLQuery

from kapps_ogm.utils.errors import OGMUsageError

if TYPE_CHECKING:
    from kapps_ogm.ogm import OGM

RDFS = "http://www.w3.org/2000/01/rdf-schema#"
OWL = "http://www.w3.org/2002/07/owl#"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"


@dataclass(frozen=True)
class Interface:
    """A caller's declaration: resolve ``property_iri`` as if it were ``rdfs:subPropertyOf``
    ``interface_iri``.

    The declaration adds the range of the interface, and the ranges of its superproperties, to
    the shape of the parameter node. It merges with the interfaces that the stored node gives
    as evidence, unless it is **authoritative**: then the declarations of the property select
    the shape alone, the evidence is ignored, and a commit removes the stored values of the
    properties that only an evidenced interface restricts.

    Args:
        property_iri: The parameter property, whose range gives the node its domain shape.
        interface_iri: The interface property whose range adds to that shape.
        authoritative: Whether the declarations of ``property_iri`` select the shape alone.
    """

    property_iri: IRI
    interface_iri: IRI
    authoritative: bool = False


@dataclass(frozen=True)
class InterfaceResolution:
    """The declared interfaces of one call, by parameter property, and its merge depth."""

    declared: Mapping[IRI, frozenset[IRI]] = field(default_factory=dict)
    authoritative: frozenset[IRI] = frozenset()
    merge_depth: Optional[int] = None

    @classmethod
    def of(
        cls,
        interfaces: Optional[Iterable[Interface]] = None,
        merge_depth: Optional[int] = None,
    ) -> "InterfaceResolution":
        """The resolution of ``interfaces`` at ``merge_depth``.

        Raises:
            OGMUsageError: If an entry is not an ``Interface``, if the declarations of one
                property disagree on ``authoritative``, or if ``merge_depth`` is negative. Fix
                the call.
        """
        if merge_depth is not None and merge_depth < 0:
            raise OGMUsageError(f"A merge depth is 0 or more, got {merge_depth}.")
        declared: dict[IRI, set[IRI]] = {}
        flags: dict[IRI, bool] = {}
        for interface in interfaces or ():
            if not isinstance(interface, Interface):
                raise OGMUsageError(
                    f"interfaces takes Interface entries, got {type(interface).__name__}"
                )
            prop = IRI(interface.property_iri)
            if flags.setdefault(prop, interface.authoritative) != interface.authoritative:
                raise OGMUsageError(
                    f"The interfaces declared for {prop} disagree on authoritative. "
                    "Mark all of them authoritative, or none."
                )
            declared.setdefault(prop, set()).add(IRI(interface.interface_iri))
        return cls(
            declared={prop: frozenset(ifaces) for prop, ifaces in declared.items()},
            authoritative=frozenset(prop for prop, flag in flags.items() if flag),
            merge_depth=merge_depth,
        )

    def interfaces_of(self, prop: IRI) -> frozenset[IRI]:
        """The interfaces declared for the parameter property ``prop``."""
        return self.declared.get(prop, frozenset())

    def to_interfaces(self) -> tuple[Interface, ...]:
        """The declarations as ``Interface`` entries, sorted: ``of`` gives this resolution back
        from them."""
        return tuple(
            Interface(prop, iface, authoritative=prop in self.authoritative)
            for prop in sorted(self.declared)
            for iface in sorted(self.declared[prop])
        )


DEFAULT_RESOLUTION = InterfaceResolution()


def _super_properties(ogm: "OGM", start: Iterable[IRI]) -> dict[IRI, set[IRI]]:
    """The direct superproperties of each property reachable upward from ``start``.

    Explicit statements only: a store that infers the reflexive and transitive closure of
    ``rdfs:subPropertyOf`` would put every property at the level of its most general ancestor.
    """
    values = " ".join(f"<{prop}>" for prop in sorted(set(start)))
    if not values:
        return {}
    query = SPARQLQuery.select(
        variables=["?sub", "?sup"],
        where_clauses=[
            f"VALUES ?start {{ {values} }}",
            f"?start <{RDFS}subPropertyOf>* ?sub .",
            f"?sub <{RDFS}subPropertyOf> ?sup .",
            "FILTER(?sub != ?sup && isIRI(?sup))",
        ],
        include_implicit=False,
    )
    # A SELECT answers with a dictionary; only an ASK answers with a boolean.
    answer = cast(
        dict, ogm.db.query(query.to_string(validate=False), convert_bindings=True)
    )
    bindings = answer.get("results", {}).get("bindings", [])
    parents: dict[IRI, set[IRI]] = {}
    for binding in bindings:
        parents.setdefault(IRI(binding["sub"]), set()).add(IRI(binding["sup"]))
    return parents


def _reachable(parents: Mapping[IRI, set[IRI]], start: Iterable[IRI]) -> set[IRI]:
    """``start`` and every property above it."""
    seen: set[IRI] = set()
    pending = list(start)
    while pending:
        prop = pending.pop()
        if prop in seen:
            continue
        seen.add(prop)
        pending.extend(parents.get(prop, ()))
    return seen


def _levels(parents: Mapping[IRI, set[IRI]], props: Iterable[IRI]) -> dict[IRI, int]:
    """The level of each property: 1 without a superproperty, else one below the deepest.

    A cycle of ``rdfs:subPropertyOf`` assertions is cut where the walk meets it again.
    """
    levels: dict[IRI, int] = {}

    def level(prop: IRI, path: frozenset[IRI]) -> int:
        if prop in levels:
            return levels[prop]
        above = [sup for sup in parents.get(prop, ()) if sup not in path]
        value = 1 + max((level(sup, path | {prop}) for sup in above), default=0)
        levels[prop] = value
        return value

    for prop in props:
        level(prop, frozenset())
    return levels


def range_sources(
    ogm: "OGM",
    prop: IRI,
    interfaces: frozenset[IRI],
    merge_depth: Optional[int],
) -> tuple[IRI, ...]:
    """The properties whose own ``rdfs:range`` makes up the shape of ``prop``, sorted.

    That is ``prop`` itself, its superproperties, each interface and its superproperties, and
    of these all but ``prop`` only down to ``merge_depth``.
    """
    parents = _super_properties(ogm, {prop} | interfaces)
    contributing = (_reachable(parents, {prop}) | _reachable(parents, interfaces)) - {prop}
    if merge_depth is not None:
        levels = _levels(parents, contributing)
        contributing = {p for p in contributing if levels[p] <= merge_depth}
    return tuple(sorted(contributing | {prop}))


def evidenced_interfaces(ogm: "OGM", prop: IRI, evidence: Iterable[IRI]) -> frozenset[IRI]:
    """The interfaces whose own range restricts a property in ``evidence``, apart from ``prop``.

    Every one of them is kept, also one that is a superproperty of another: each restricts a
    property the stored node carries.
    """
    values = " ".join(f"<{p}>" for p in sorted(set(evidence)))
    if not values:
        return frozenset()
    query = f"""
        SELECT DISTINCT ?interface WHERE {{
            VALUES ?evidence {{ {values} }}
            ?interface <{RDFS}range> ?range .
            {{
                ?range <{OWL}intersectionOf> ?list .
                ?list <{RDF}rest>*/<{RDF}first> ?restriction .
            }}
            UNION
            {{
                ?range <{OWL}unionOf> ?list .
                ?list <{RDF}rest>*/<{RDF}first> ?restriction .
            }}
            UNION
            {{
                ?range a <{OWL}Restriction> .
                BIND(?range AS ?restriction)
            }}
            ?restriction <{OWL}onProperty> ?evidence .
        }}
    """
    # A SELECT answers with a dictionary; only an ASK answers with a boolean.
    answer = cast(dict, ogm.db.query(query, convert_bindings=True))
    bindings = answer.get("results", {}).get("bindings", [])
    return frozenset(
        IRI(binding["interface"]) for binding in bindings if "interface" in binding
    ) - {prop}
