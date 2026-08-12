from __future__ import annotations

from typing import Optional, Type, TYPE_CHECKING, Any, Union, Annotated, Callable
from enum import Enum
from dataclasses import dataclass
from kapps_triplestore_interface import IRI, XSDToPythonTypes
import logging
from pydantic import BeforeValidator, Field, conlist
from rdflib import BNode

from kapps_ogm.utils.constants import (
    PROPERTY_TYPES,
    PROPERTY_CHARACTERISTICS,
)
from kapps_ogm.utils.class_scope import ClassScope

if TYPE_CHECKING:
    from kapps_ogm.mapping.class_spec import ClassSpec, ClassHydrationLevel
    from kapps_ogm.ogm import OGM

logger = logging.getLogger("kapps_pspec")
logger.setLevel(logging.INFO)


class PropertyValueKind(Enum):
    LITERAL = "literal"
    OBJECT = "object"
    COMPLEX = "complex"


def chain_ranges(prop_iri: IRI, range_var: str, ancestor_var: str = "?ancestor") -> str:
    """The SPARQL prelude binding `range_var` to every rdfs:range along a property's chain.

    RDFS entails no rdfs:range triple for a subproperty and GraphDB materializes none, so
    every query that needs a property's effective shape has to walk the chain itself. The
    `*` path is a transitive closure, which is what makes a cyclic rdfs:subPropertyOf
    assertion terminate rather than recurse. Shared so the four call sites cannot drift.
    """
    # Kept to one line so it interpolates cleanly at any indentation.
    return (
        f"<{prop_iri}> <http://www.w3.org/2000/01/rdf-schema#subPropertyOf>* {ancestor_var} . "
        f"{ancestor_var} <http://www.w3.org/2000/01/rdf-schema#range> {range_var} ."
    )


XSD_NAMESPACE = "http://www.w3.org/2001/XMLSchema#"

# Datatypes that are not in the XSD namespace. rdfs:Literal is the top datatype, and the
# other two are RDF's own.
NON_XSD_DATATYPES = frozenset(
    {
        IRI("http://www.w3.org/1999/02/22-rdf-syntax-ns#XMLLiteral"),
        IRI("http://www.w3.org/1999/02/22-rdf-syntax-ns#langString"),
        IRI("http://www.w3.org/2000/01/rdf-schema#Literal"),
    }
)


def _names_a_datatype(target: IRI) -> bool:
    """Whether a restriction's target names a datatype rather than a class.

    Decided by namespace, deliberately not by membership of `XSDToPythonTypes`. That map
    covers 33 datatypes, so `xsd:gMonth`, `xsd:gDay` and `xsd:dateTimeStamp` are absent
    from it — and reading a miss as "then it must be a class" would silently turn a
    literal restriction into an object one.
    """
    return str(target).startswith(XSD_NAMESPACE) or target in NON_XSD_DATATYPES


def _target_label(target: Union[IRI, type]) -> str:
    """A restriction target as it should appear in an error message.

    A resolved python type renders as `str` rather than `<class 'str'>`, which is closer
    to the `xsd:string` the ontology author actually wrote.
    """
    return getattr(target, "__name__", str(target))


def _satisfies_constraint(value: Any, constraint: Union[IRI, type]) -> bool:
    """Whether one value meets a some/allValuesFrom constraint.

    A class-valued constraint arrives as an IRI, and class membership cannot be decided
    without the store, so the check is only that the value is a reference. It runs as a
    BeforeValidator, ahead of pydantic's coercion, so a plain string that the `IRI` field
    type would go on to accept has to pass here too — hence `str` rather than `IRI`.
    """
    if isinstance(constraint, IRI):
        return isinstance(value, str)
    return isinstance(value, constraint)


def _tighten_bound(
    mine: Optional[int], theirs: Optional[int], combine: Callable[[int, int], int]
) -> Optional[int]:
    """Combine two cardinality bounds conjunctively; an absent bound constrains nothing."""
    if mine is None:
        return theirs
    if theirs is None:
        return mine
    return combine(mine, theirs)


@dataclass
class PropertySpec:
    iri: IRI
    value_kind: PropertyValueKind  # 'data' or 'object' or 'complex'
    python_range_type: Optional[Type] = None
    min_count: Optional[int] = None
    max_count: Optional[int] = None
    some_from: Optional[Union[IRI, Type]] = None
    all_from: Optional[Union[IRI, Type]] = None
    nested: Optional["ClassSpec"] = None

    @property
    def required(self) -> bool:
        if self.min_count is not None and self.min_count >= 1:
            return True
        return False

    @property
    def restriction_target(self) -> Optional[Union[IRI, Type]]:
        """The datatype or class this restriction constrains values to.

        None for a cardinality-only restriction, which says nothing about the value's type
        and so cannot conflict with one that does. `python_range_type` and `all_from` are
        mutually exclusive as built by `_specify_complex_property` — one holds a resolved
        datatype, the other a class IRI — so the order below only decides which a
        hand-built spec reports.
        """
        for candidate in (self.python_range_type, self.all_from, self.some_from):
            if candidate is not None:
                return candidate
        return None

    def to_string(self) -> str:
        from ..utils.pretty_print import format_property_spec

        return format_property_spec(self)

    def to_pydantic_field(self) -> tuple[Any, Any]:
        """Convert this PropertySpec into a Pydantic field with validators."""
        from .class_spec import ClassHydrationLevel

        if not self.value_kind in PropertyValueKind:
            raise ValueError(f"Unknown value_kind: {self.value_kind}")

        logger.debug(
            f"Converting PropertySpec ({self.value_kind.value}) '{self.iri.fragment}' to pydantic field"
        )

        validators = []

        match self.value_kind:
            case PropertyValueKind.LITERAL:
                # If all_from is set, use it as type restriction
                if self.all_from:
                    if isinstance(self.all_from, IRI):
                        raise ValueError(
                            f"Literal property {self.iri} cannot have allValuesFrom as Object IRI"
                        )
                    base_type = self.all_from
                else:
                    base_type = self.python_range_type or Any
            case PropertyValueKind.OBJECT:
                # Nested hydrated class becomes Pydantic model; else fallback to IRI
                if (
                    not self.nested
                    or self.nested.hydration_level is ClassHydrationLevel.REFERENCE
                ):
                    base_type = IRI
                else:
                    nested_model = self.nested.to_pydantic_model()
                    base_type = nested_model

                    def coerce_object(value):
                        parse = getattr(
                            nested_model, "model_validate", None
                        ) or getattr(nested_model, "parse_obj", None)
                        if value is None:
                            return value
                        if isinstance(value, nested_model):
                            return value
                        try:
                            from kapps_ogm.node.core import (
                                Node,
                            )  # Lazy import to avoid cycles
                        except Exception:
                            Node = None

                        if Node is not None and isinstance(value, Node):
                            payload = getattr(value, "instance", None) or getattr(
                                value, "data", None
                            )
                            if payload is not None and parse is not None:
                                return parse(payload)
                            return value

                        if isinstance(value, dict) and parse is not None:
                            return parse(value)
                        return value

                    validators.append(BeforeValidator(coerce_object))
            case PropertyValueKind.COMPLEX:
                # Complex properties have nested ClassSpec that should be converted to Pydantic model
                if (
                    not self.nested
                    or self.nested.hydration_level is ClassHydrationLevel.REFERENCE
                ):
                    base_type = Any
                else:
                    nested_model = self.nested.to_pydantic_model()
                    base_type = nested_model

                    def coerce_complex(value):
                        parse = getattr(
                            nested_model, "model_validate", None
                        ) or getattr(nested_model, "parse_obj", None)
                        if value is None:
                            return value
                        if isinstance(value, nested_model):
                            return value
                        try:
                            from kapps_ogm.node.core import (
                                Node,
                            )  # Lazy import to avoid cycles
                        except Exception:
                            Node = None

                        if Node is not None and isinstance(value, Node):
                            payload = getattr(value, "instance", None) or getattr(
                                value, "data", None
                            )
                            if payload is not None and parse is not None:
                                return parse(payload)
                            return value

                        if isinstance(value, dict) and parse is not None:
                            return parse(value)
                        return value

                    validators.append(BeforeValidator(coerce_complex))
            case _:
                raise RuntimeError

        # cardinality
        min_count = self.min_count or 0
        max_count = self.max_count

        # Always treat multiple cardinality as list
        is_multi = max_count is None or max_count > 1 or min_count > 1
        if is_multi:
            field_type = conlist(base_type, min_length=min_count, max_length=max_count)
            default = ... if self.required else []
        else:
            field_type = base_type
            default = ... if self.required else None

        # Apply some_from / all_from validators using Annotated types
        if self.some_from or self.all_from:

            def validate_some_all(v):
                if v is None:
                    return v
                values = v if isinstance(v, list) else [v]

                if self.some_from is not None:
                    if not any(
                        _satisfies_constraint(x, self.some_from) for x in values
                    ):
                        raise ValueError(
                            f"Property {self.iri} requires at least one value of type {self.some_from}"
                        )

                if self.all_from is not None:
                    if not all(_satisfies_constraint(x, self.all_from) for x in values):
                        raise ValueError(
                            f"Property {self.iri} requires all values to be of type {self.all_from}"
                        )

                return v

            validators.append(BeforeValidator(validate_some_all))

        if validators:
            field_type = Annotated[field_type, *validators]

        # Wrap in Optional if not required
        if not self.required:
            field_type = Optional[field_type]

        # Create Pydantic Field
        field = Field(
            default=default,
            title=str(self.iri),
            description=f"PropertySpec for {self.iri}",
        )

        return field_type, field

    @classmethod
    def _resolve_effective_ranges(
        cls, prop_iri: IRI, ogm: "OGM"
    ) -> set[Union[IRI, BNode, type]]:
        """Resolve all effective rdfs:range assertions across the rdfs:subPropertyOf* chain.

        Returns every rdfs:range asserted on prop_iri or on any of its rdfs:subPropertyOf
        ancestors, after the most-specific-named-class filter. The set is deliberately
        heterogeneous: a named class arrives as an IRI, an XSD datatype as a python type,
        and an anonymous restriction as a BNode, and the caller dispatches on which.
        """
        range_query = f"""
        SELECT DISTINCT ?obj
        WHERE {{
            {chain_ranges(prop_iri, "?obj")}
            FILTER NOT EXISTS {{
                {chain_ranges(prop_iri, "?sub", ancestor_var="?otherAncestor")}
                # The subsumption filter picks the most specific *named* class. Without the
                # isIRI guard it could also discard an anonymous restriction range, silently
                # dropping half a merge.
                FILTER (?sub != ?obj && isIRI(?sub) && isIRI(?obj))
                {{
                    ?sub <http://www.w3.org/2000/01/rdf-schema#subClassOf>+ ?obj
                }}
                UNION
                {{
                    ?sub <http://www.w3.org/2000/01/rdf-schema#subPropertyOf>+ ?obj
                }}
            }}
        }}
        """

        range_query_result = ogm.db.query(range_query, convert_bindings=True)
        range_set = set(
            d["obj"] for d in range_query_result.get("results", {}).get("bindings", [])
        )

        # owl:Thing constrains nothing, and since the isIRI guard above confines the
        # subsumption filter to named ranges it can no longer be filtered out by an
        # anonymous sibling. Dropping it here is what stops an ancestor declaring
        # `rdfs:range owl:Thing` from being read as a named range mixed with an
        # anonymous one. (Merge note: this line is bcb7840.)
        range_set -= {IRI("http://www.w3.org/2002/07/owl#Thing")}

        return range_set

    def merge_conjunctive(
        self, other: "PropertySpec", owner_iri: IRI
    ) -> "PropertySpec":
        """Conjunctive merge of two restrictions on the same nested property.

        Returns a new PropertySpec; does not mutate either input. owner_iri appears in error
        messages so a failure names the property whose range is malformed.
        """
        if self.iri != other.iri:
            raise ValueError(
                f"Cannot merge PropertySpecs with different IRIs: {self.iri} vs {other.iri}"
            )

        # A datatype target and a class target are irreconcilable: the value cannot be both
        # a literal and an instance of a class. Caught here rather than in to_pydantic_field
        # so the message can name the ontology at fault. Both sides must carry a target, so
        # a cardinality-only restriction still merges with a typed one.
        mine_target = self.restriction_target
        their_target = other.restriction_target
        if (
            mine_target is not None
            and their_target is not None
            and isinstance(mine_target, IRI) != isinstance(their_target, IRI)
        ):
            raise ValueError(
                f"Property {self.iri} is constrained to both a datatype and a class by two "
                f"rdfs:range restrictions of {owner_iri}: "
                f"{_target_label(mine_target)} and {_target_label(their_target)}"
            )

        def reconcile_type(owl_term: str, mine: Any, theirs: Any) -> Any:
            """One side's constraint, or the shared one; two different ones are an ontology error."""
            if mine is not None and theirs is not None and mine != theirs:
                raise ValueError(
                    f"Property {self.iri} is constrained to incompatible {owl_term} by two "
                    f"rdfs:range restrictions of {owner_iri}: {mine} and {theirs}"
                )
            return mine if mine is not None else theirs

        merged_python_range_type = reconcile_type(
            "allValuesFrom", self.python_range_type, other.python_range_type
        )
        merged_some_from = reconcile_type(
            "someValuesFrom", self.some_from, other.some_from
        )
        merged_all_from = reconcile_type("allValuesFrom", self.all_from, other.all_from)

        # Cardinality — most restrictive wins
        merged_min_count = _tighten_bound(self.min_count, other.min_count, max)
        merged_max_count = _tighten_bound(self.max_count, other.max_count, min)

        if (
            merged_min_count is not None
            and merged_max_count is not None
            and merged_min_count > merged_max_count
        ):
            raise ValueError(
                f"Property {self.iri} is unsatisfiable after merging the rdfs:range restrictions "
                f"of {owner_iri}: minimum cardinality {merged_min_count} exceeds maximum cardinality {merged_max_count}"
            )

        # A cardinality-only restriction carries no type information, so it must not
        # downgrade a typed one: LITERAL wins over OBJECT.
        if PropertyValueKind.LITERAL in (self.value_kind, other.value_kind):
            merged_value_kind = PropertyValueKind.LITERAL
        else:
            merged_value_kind = self.value_kind

        # nested — take whichever side is set; if both are set, keep self.nested
        merged_nested = self.nested if self.nested is not None else other.nested

        return PropertySpec(
            iri=self.iri,
            value_kind=merged_value_kind,
            python_range_type=merged_python_range_type,
            min_count=merged_min_count,
            max_count=merged_max_count,
            some_from=merged_some_from,
            all_from=merged_all_from,
            nested=merged_nested,
        )

    @classmethod
    def specify(
        cls,
        prop_iri: IRI,
        nested_scope: Optional["ClassScope"],
        ogm: "OGM",
        hydration_level: "ClassHydrationLevel",
    ) -> PropertySpec:
        # Categorize the property regarding its type and characteristics
        type_query_result = ogm.db.triples_get(
            sub=prop_iri, pred="rdf:type", include_implicit=True
        )
        property_types = [triple[2] for triple in type_query_result]

        if not property_types:
            raise ValueError(f"Property {prop_iri} has no rdf:type defined.")

        base_types = []
        characteristics = []

        for ptype in property_types:
            if ptype in PROPERTY_TYPES:
                base_types.append(PROPERTY_TYPES[ptype])
            elif ptype in PROPERTY_CHARACTERISTICS:
                characteristics.append(PROPERTY_CHARACTERISTICS[ptype])

        # Determine the property specification based on its range
        range_set = cls._resolve_effective_ranges(prop_iri=prop_iri, ogm=ogm)

        anonymous_ranges = {r for r in range_set if isinstance(r, BNode)}
        named_ranges = range_set - anonymous_ranges

        if not range_set:
            raise ValueError(f"Property {prop_iri} has no rdfs:range defined.")

        if anonymous_ranges and named_ranges:
            raise ValueError(
                f"Property {prop_iri} resolves both a named rdfs:range and an anonymous "
                f"restriction range, which cannot be merged: named {sorted(str(r) for r in named_ranges)}, "
                f"{len(anonymous_ranges)} anonymous restriction(s)"
            )

        if anonymous_ranges:
            # An anonymous range is only usable if it is a class expression we can project
            # a shape from; anything else is an ontology we do not understand.
            query_is_complex_type = f"""
                ASK {{
                    {chain_ranges(prop_iri, "?range")}
                    {{
                        ?range a <http://www.w3.org/2002/07/owl#Restriction>
                    }}
                    UNION
                    {{
                        ?range <http://www.w3.org/2002/07/owl#intersectionOf> ?x
                    }}
                    UNION
                    {{
                        ?range <http://www.w3.org/2002/07/owl#unionOf> ?y
                    }}
                    UNION
                    {{
                        ?range <http://www.w3.org/2002/07/owl#complementOf> ?z
                    }}
                    UNION
                    {{
                        ?range <http://www.w3.org/2002/07/owl#oneOf> ?w
                    }}
                }}
            """
            if ogm.db.query(query_is_complex_type).get("boolean", False):
                property_spec = cls._specify_complex_property(
                    prop_iri=prop_iri,
                    ogm=ogm,
                )
            else:
                raise ValueError(
                    f"Unknown property_type: {sorted(str(r) for r in anonymous_ranges)} for property {prop_iri}"
                )
        elif len(named_ranges) > 1:
            raise ValueError(
                f"Property {prop_iri} has multiple independent rdfs:range defined, this is not supported: {named_ranges}"
            )
        else:
            prop_range = named_ranges.pop()
            if isinstance(prop_range, type):
                if nested_scope:
                    raise ValueError(
                        f"Property {prop_iri} cannot be part of a property chain as it has a literal range {prop_range}"
                    )
                property_spec = cls._specify_literal_property(
                    prop_iri=prop_iri,
                    python_type=prop_range,
                )
            elif isinstance(prop_range, IRI):
                property_spec = cls._specify_class_property(
                    prop_iri=prop_iri,
                    range_iri=prop_range,
                    nested_scope=nested_scope,
                    ogm=ogm,
                    hydration_level=hydration_level,
                )
            else:
                raise ValueError(
                    f"Unknown property_type: {prop_range} for property {prop_iri}"
                )

        # Apply characteristics to the property_spec
        if "functional" in characteristics:
            property_spec.max_count = 1

        return property_spec

    @classmethod
    def _specify_literal_property(
        cls,
        prop_iri: IRI,
        python_type: type,
    ) -> PropertySpec:
        property_spec = cls(
            iri=prop_iri,
            value_kind=PropertyValueKind.LITERAL,
            python_range_type=python_type,
            max_count=None,
            min_count=None,
            nested=None,
        )
        return property_spec

    @classmethod
    def _specify_class_property(
        cls,
        prop_iri: IRI,
        range_iri: IRI,
        nested_scope: Optional["ClassScope"],
        ogm: "OGM",
        hydration_level: "ClassHydrationLevel",
    ) -> PropertySpec:
        from .class_spec import ClassSpec

        nested_class_spec = ClassSpec.specify(
            ogm=ogm,
            class_iri=range_iri,
            class_scope=nested_scope,
            hydration_level=hydration_level,
        )

        property_spec = cls(
            iri=prop_iri,
            value_kind=PropertyValueKind.OBJECT,
            python_range_type=None,  # Will be another ClassSpec
            max_count=None,
            min_count=None,
            nested=nested_class_spec,
        )

        return property_spec

    @staticmethod
    def _datatype_of(
        target: IRI, nested_property: IRI, owner_iri: IRI
    ) -> Optional[Type]:
        """The python type a restriction target resolves to, or None when it names a class.

        A datatype we recognise but cannot map raises rather than falling through to the
        class branch: silently treating `xsd:gMonth` as a class would give the property an
        IRI field type and demand references where the ontology asked for literals.
        """
        if not _names_a_datatype(target):
            return None

        python_type = XSDToPythonTypes.get(target)
        if python_type is None:
            raise ValueError(
                f"Property {nested_property}, restricted by the rdfs:range of {owner_iri}, "
                f"has datatype target {target}, which kapps_ogm cannot map to a python type"
            )
        return python_type

    @classmethod
    def _specify_complex_property(
        cls,
        prop_iri: IRI,
        ogm: "OGM",
    ) -> PropertySpec:
        """
        Processes a complex OWL property and returns a PropertySpec with a nested ClassSpec
        that includes intersection, union, complement, and enumerated restrictions.
        """
        from .class_spec import ClassSpec, ClassHydrationLevel

        # Initialize top-level PropertySpec
        property_spec = cls(
            iri=prop_iri,
            value_kind=PropertyValueKind.COMPLEX,
            python_range_type=None,
            min_count=None,
            max_count=None,
            nested=None,
        )

        # Build empty anonymous nested ClassSpec up front
        property_spec.nested = ClassSpec(
            iri=None,
            label=None,
            properties={},
            metadata={},
            hydration_level=ClassHydrationLevel.FULL,  # Anonymous class
        )

        # Query 1 — restrictions and their details
        query_restrictions = f"""SELECT DISTINCT
            ?restriction ?onProperty ?someValuesFrom ?allValuesFrom
            ?effectiveMinCardinality ?effectiveMaxCardinality
            ?intersectionList
        WHERE {{
            {chain_ranges(prop_iri, "?range")}
            {{
                ?range <http://www.w3.org/2002/07/owl#intersectionOf> ?intersectionList .
                ?intersectionList <http://www.w3.org/1999/02/22-rdf-syntax-ns#rest>*/<http://www.w3.org/1999/02/22-rdf-syntax-ns#first> ?restriction .
            }}
            UNION
            {{
                ?range <http://www.w3.org/2002/07/owl#unionOf> ?unionList .
                ?unionList <http://www.w3.org/1999/02/22-rdf-syntax-ns#rest>*/<http://www.w3.org/1999/02/22-rdf-syntax-ns#first> ?restriction .
            }}
            UNION
            {{
                ?range a <http://www.w3.org/2002/07/owl#Restriction> .
                BIND(?range AS ?restriction)
            }}
            # Binds ?restriction before the detail OPTIONALs below. Leave it out and a bare
            # owl:Restriction range, which matches neither structural branch, leaves the
            # variable unbound — and every OPTIONAL then matches every restriction in the
            # repository rather than this property's.
            ?restriction a <http://www.w3.org/2002/07/owl#Restriction> .
            OPTIONAL {{ ?restriction <http://www.w3.org/2002/07/owl#onProperty> ?onProperty }}
            OPTIONAL {{ ?restriction <http://www.w3.org/2002/07/owl#someValuesFrom> ?someValuesFrom }}
            OPTIONAL {{ ?restriction <http://www.w3.org/2002/07/owl#allValuesFrom> ?allValuesFrom }}
            OPTIONAL {{ ?restriction <http://www.w3.org/2002/07/owl#minCardinality> ?minCardinality }}
            OPTIONAL {{ ?restriction <http://www.w3.org/2002/07/owl#maxCardinality> ?maxCardinality }}
            OPTIONAL {{ ?restriction <http://www.w3.org/2002/07/owl#cardinality> ?cardinality }}
            BIND(IF(BOUND(?cardinality), ?cardinality, ?minCardinality) AS ?effectiveMinCardinality)
            BIND(IF(BOUND(?cardinality), ?cardinality, ?maxCardinality) AS ?effectiveMaxCardinality)
        }}"""

        # Execute query 1
        query_result = ogm.db.query(query_restrictions)
        bindings = query_result["results"]["bindings"]

        # Process each restriction
        for restriction in bindings:
            if "onProperty" in restriction:
                nested_property = IRI(restriction["onProperty"]["value"])
                nested_spec = cls(
                    iri=nested_property,
                    # Refined below from the restriction's target. A restriction carrying
                    # only a cardinality says nothing about the value's type, so OBJECT is
                    # the honest default.
                    value_kind=PropertyValueKind.OBJECT,
                    python_range_type=None,
                    min_count=None,
                    max_count=None,
                    nested=None,
                )

                # The target decides the kind, not the keyword: owl:allValuesFrom
                # xsd:string constrains a literal, owl:allValuesFrom cfc:Unit constrains
                # an object. A class target leaves value_kind at its OBJECT default.
                if "someValuesFrom" in restriction:
                    target = IRI(restriction["someValuesFrom"]["value"])
                    python_type = cls._datatype_of(target, nested_property, prop_iri)
                    if python_type is not None:
                        nested_spec.some_from = python_type
                        nested_spec.value_kind = PropertyValueKind.LITERAL
                    else:
                        nested_spec.some_from = target
                    nested_spec.min_count = 1

                elif "allValuesFrom" in restriction:
                    target = IRI(restriction["allValuesFrom"]["value"])
                    python_type = cls._datatype_of(target, nested_property, prop_iri)
                    if python_type is not None:
                        nested_spec.python_range_type = python_type
                        nested_spec.value_kind = PropertyValueKind.LITERAL
                    else:
                        nested_spec.all_from = target

                # Cardinality
                if "effectiveMinCardinality" in restriction:
                    nested_spec.min_count = int(
                        restriction["effectiveMinCardinality"]["value"]
                    )

                if "effectiveMaxCardinality" in restriction:
                    nested_spec.max_count = int(
                        restriction["effectiveMaxCardinality"]["value"]
                    )

                # Merge or add nested property to ClassSpec
                existing = property_spec.nested.properties.get(nested_property)
                if existing is None:
                    property_spec.nested.properties[nested_property] = nested_spec
                else:
                    property_spec.nested.properties[nested_property] = (
                        existing.merge_conjunctive(nested_spec, owner_iri=prop_iri)
                    )

                # Update metadata for intersectionOf, guarding against duplicates
                if "intersectionList" in restriction:
                    if "intersectionOf" not in property_spec.nested.metadata:
                        property_spec.nested.metadata["intersectionOf"] = []
                    nested_prop_str = str(nested_property)
                    if (
                        nested_prop_str
                        not in property_spec.nested.metadata["intersectionOf"]
                    ):
                        property_spec.nested.metadata["intersectionOf"].append(
                            nested_prop_str
                        )

        # Query 2 — structural metadata
        query_metadata = f"""SELECT DISTINCT ?unionList ?complementClass ?oneOfList
        WHERE {{
            {chain_ranges(prop_iri, "?range")}
            {{
                ?range <http://www.w3.org/2002/07/owl#unionOf> ?unionList
            }}
            UNION
            {{
                ?range <http://www.w3.org/2002/07/owl#complementOf> ?complementClass
            }}
            UNION
            {{
                ?range <http://www.w3.org/2002/07/owl#oneOf> ?oneOfList
            }}
        }}"""

        # Execute query 2
        metadata_result = ogm.db.query(query_metadata)
        metadata_bindings = metadata_result["results"]["bindings"]

        # Iterate all of query 2's bindings; first one seen wins
        for binding in metadata_bindings:
            if "unionList" in binding:
                # Note: resolve_rdf_list method would need to be implemented in GraphDB
                if "unionOf" not in property_spec.nested.metadata:
                    property_spec.nested.metadata["unionOf"] = binding["unionList"][
                        "value"
                    ]

            if "complementClass" in binding:
                if "complementOf" not in property_spec.nested.metadata:
                    property_spec.nested.metadata["complementOf"] = binding[
                        "complementClass"
                    ]["value"]

            if "oneOfList" in binding:
                # Note: resolve_rdf_list method would need to be implemented in GraphDB
                if "oneOf" not in property_spec.nested.metadata:
                    property_spec.nested.metadata["oneOf"] = binding["oneOfList"][
                        "value"
                    ]

        return property_spec
