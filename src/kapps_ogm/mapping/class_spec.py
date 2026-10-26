from __future__ import annotations

from typing import Optional, Type, Any, Dict, List, TYPE_CHECKING
from enum import Enum
from dataclasses import dataclass, field, asdict, replace
import logging
import pydantic as pd



from kapps_triplestore_interface import IRI, SPARQLQuery
from kapps_ogm.utils.pretty_print import format_class_spec
from kapps_ogm.mapping.anonymous_model import AnonymousNodeModel
from kapps_ogm.mapping.property_spec import (
    PropertySpec,
    PropertyValueKind,
    _tighten_bound,
)
from kapps_ogm.mapping.interfaces import InterfaceResolution
from kapps_ogm.utils.class_scope import ClassScope
from kapps_ogm.utils.errors import (
    OGMDataError,
    OGMOntologyError,
    OGMUsageError,
    _one_or_none,
)

if TYPE_CHECKING:
    from kapps_ogm.ogm import OGM

logger = logging.getLogger("kapps_cspec")
logger.setLevel(logging.INFO)


# The ways a property reaches ``?class``, one SPARQL group pattern each. A property belongs to a
# class when one of them matches for the class or one of its superclasses.
_PROPERTY_BRANCHES = (
    # The property's domain is the class.
    "?property <http://www.w3.org/2000/01/rdf-schema#domain> ?class .",
    # The property's domain is a union that has the class as a member.
    "?property <http://www.w3.org/2000/01/rdf-schema#domain> ?union_class . "
    "?union_class <http://www.w3.org/2002/07/owl#unionOf> ?list . "
    "?list <http://www.w3.org/1999/02/22-rdf-syntax-ns#rest>*/"
    "<http://www.w3.org/1999/02/22-rdf-syntax-ns#first> ?class .",
)


def _property_discovery_query(class_iri: IRI) -> SPARQLQuery:
    """The properties of ``class_iri``: every property that a branch above gives the class or one
    of its superclasses.

    The query reads explicit statements only, so that a class gets the properties the ontology
    asserts for it. On `rdfsplus-optimized` the inferred statements change nothing here: the
    inferred superclasses are the closure that the query walks itself. A ruleset that infers more
    could add domains or superclasses. The interface writes that scope in the form its store
    understands.
    """
    branches = "\nUNION\n".join(f"{{ {branch} }}" for branch in _PROPERTY_BRANCHES)
    return SPARQLQuery.select(
        variables=["?property"],
        where_clauses=[
            branches,
            f"<{class_iri}> <http://www.w3.org/2000/01/rdf-schema#subClassOf>* ?class .",
        ],
        include_implicit=False,
    )


class ClassHydrationLevel(Enum):
    REFERENCE = "reference"
    SCOPE = "scope"
    FULL = "full"


@dataclass
class ClassSpec:
    iri: Optional[IRI]
    label: Optional[str] = None
    comment: Optional[str] = None
    properties: Dict[IRI, PropertySpec] = field(default_factory=dict)
    types: List[IRI] = field(default_factory=list)
    superclasses: List[IRI] = field(default_factory=list)
    pydantic_base_model: Optional[Type[pd.BaseModel]] = pd.BaseModel
    metadata: Dict[str, Any] = field(default_factory=dict)
    hydration_level: ClassHydrationLevel = field(default=ClassHydrationLevel.REFERENCE)

    def to_string(self) -> str:
        return format_class_spec(self)

    @property
    def hydrated(self) -> bool:
        """Whether this ClassSpec has been fully hydrated from the ontology."""
        return self.hydration_level == ClassHydrationLevel.FULL

    def hydrate(self, ogm: "OGM") -> ClassSpec:
        """
        Populate this ClassSpec with all details from the ontology.
        Uses `specify` internally and updates this instance in place.
        Returns self
        """
        if not self.iri:
            raise OGMUsageError("Cannot hydrate ClassSpec without an IRI.")

        hydrated_spec = ClassSpec.specify(
            self.iri,
            ogm,
            hydration_level=ClassHydrationLevel.FULL,
        )
        for key, value in asdict(hydrated_spec).items():
            setattr(self, key, value)
        return self

    def to_pydantic_model(self) -> Type[pd.BaseModel]:
        """
        Convert ClassSpec into a Pydantic model.
        Delegates to PropertySpec.to_pydantic_field() for consistent field generation.
        """
        fields: Dict[str, tuple[Any, Any]] = {}

        # Only add id field for named classes (not blank nodes)
        if self.iri:
            logger.debug(
                f"Converting ClassSpec '{self.iri.fragment}' to pydantic model"
            )
            fields["id"] = (IRI, pd.Field(..., description="IRI of the instance"))
        else:
            logger.debug(
                "Converting ClassSpec without IRI to pydantic model; this is a blank node that will not be a standalone node."
            )

        for prop_iri, prop_spec in self.properties.items():
            # Use sanitized IRI for field names
            field_name = prop_iri.lined

            # Delegate to PropertySpec for field generation (includes validators via Annotated types)
            field_type, field = prop_spec.to_pydantic_field()
            fields[field_name] = (field_type, field)

        # Build the Pydantic model
        model_name = (
            self.iri.lined if self.iri else "AnonymousClass"
        )  # TODO: use the store's blank node generator?

        # An anonymous class has no id field, so its node's address has nowhere to live in the
        # projection. AnonymousNodeModel carries it out of band instead. An explicitly configured
        # base is left alone — pydantic_base_model is the documented seam for overriding this.
        base_model = self.pydantic_base_model
        if self.iri is None and base_model is pd.BaseModel:
            base_model = AnonymousNodeModel

        model_cls = pd.create_model(
            model_name,
            __base__=(base_model,),
            **fields,
        )  # type: ignore[call-overload] #TODO: is baseModel correct base here?

        # Unknown properties are ignored by design: the model keeps pydantic's default
        # (extra="ignore"). Under the open world a graph may carry more than any schema declares
        # (a comment, the connection metadata of a parameter node), so rejecting unknown keys
        # would break every fetch of real data. The write paths refuse an unknown property
        # before the model is built: create and commit materialize with refuse_unknown=True, and
        # NodeValidator raises OGMDataError. A closed-world validator belongs to SHACL support.
        # Do not set extra="forbid" here; setting it after create_model has no effect anyway.

        # Keep mapping to IRIs for reference
        setattr(
            model_cls,
            "_iri_fields",
            {prop_iri.lined: prop_iri for prop_iri in self.properties.keys()},
        )
        setattr(model_cls, "_iri_model_name", self.iri if self.iri else None)

        return model_cls

    @staticmethod
    def declared_properties(class_iri: IRI, ogm: "OGM") -> set[IRI]:
        """The properties that ``class_iri`` declares itself, whatever a class scope names.

        It reads the store, so a caller asks only where it needs the answer: the write refusal
        asks when it refuses a property, to tell a property the class does not declare from one
        that the scope leaves out."""
        bindings = (
            ogm.db.query(
                _property_discovery_query(class_iri).to_string(validate=False),
                convert_bindings=True,
            )
            .get("results", {})
            .get("bindings", [])
        )
        return {binding["property"] for binding in bindings}

    @staticmethod
    def _maximum_counts(class_iri: IRI, ogm: "OGM") -> Dict[IRI, int]:
        """The maximum count of each property that ``class_iri`` or one of its superclasses
        bounds with a restriction under ``rdfs:subClassOf``. Where several restrictions bound a
        property, the tightest wins.

        The query walks ``rdfs:subClassOf*`` from the class to the anonymous restrictions and
        reads ``owl:cardinality``, ``owl:maxCardinality``, ``owl:qualifiedCardinality`` and
        ``owl:maxQualifiedCardinality``. It does not check the qualifier, ``owl:onClass`` or
        ``owl:onDataRange``: the bound counts every value of the property. It reads explicit
        statements only, as the property discovery does.
        """
        owl = "http://www.w3.org/2002/07/owl#"
        bounds = "\nUNION\n".join(
            f"{{ ?restriction <{owl}{keyword}> ?bound . }}"
            for keyword in (
                "cardinality",
                "maxCardinality",
                "qualifiedCardinality",
                "maxQualifiedCardinality",
            )
        )
        query = SPARQLQuery.select(
            variables=["?property", "?bound"],
            where_clauses=[
                f"<{class_iri}> <http://www.w3.org/2000/01/rdf-schema#subClassOf>* ?restriction .",
                "FILTER(isBlank(?restriction))",
                f"?restriction <{owl}onProperty> ?property .",
                bounds,
            ],
            include_implicit=False,
        )
        bindings = (
            ogm.db.query(query.to_string(validate=False), convert_bindings=True)
            .get("results", {})
            .get("bindings", [])
        )
        maximum_counts: Dict[IRI, int] = {}
        for binding in bindings:
            prop, bound = binding["property"], int(binding["bound"])
            maximum_counts[prop] = min(bound, maximum_counts.get(prop, bound))
        return maximum_counts

    @classmethod
    def specify_from_instance(
        cls,
        model_cls: Type[pd.BaseModel],
        instance: pd.BaseModel,
        ogm: "OGM",
        hydration_level: ClassHydrationLevel,
    ) -> ClassSpec:
        """
        create a ClassSpec for the given Pydantic model by analyzing its RDF data in the triple store via the OGM instance.

            Args:
                model_cls: The Pydantic model class to specify
                ogm: The OGM instance with access to the triple store
            Returns:
                A ClassSpec instance representing the class specification"""
        iri = getattr(model_cls, "_iri_model_name", None)
        if iri is None:
            raise OGMUsageError(f"Model {model_cls.__name__} has no associated IRI.")

        return cls.specify(
            class_iri=iri,
            ogm=ogm,
            hydration_level=hydration_level,
        )

    @classmethod
    def specify(
        cls,
        class_iri: IRI,
        ogm: "OGM",
        hydration_level: ClassHydrationLevel,
        class_scope: Optional[ClassScope] = None,
        *,
        _path: tuple[IRI, ...] = (),
        _known_types: Optional[list[IRI]] = None,
        _interfaces: Optional[InterfaceResolution] = None,
    ) -> ClassSpec:
        """
        create a ClassSpec for the given IRI by analyzing the ClassScope, its RDF data in the triple store via the OGM instance.

        At the FULL level an object property nests the FULL specification of its range class. A
        range class that is already on the specification path, the classes being specified from
        the root to this one, is nested as an IRI-only link instead: a ClassSpec with the class's
        IRI and no properties, which every reader takes as a link. So a class that its
        properties lead back to gives a finite specification, and a class on two paths without a
        cycle is specified in full on both. The properties are visited in sorted order, so the
        specification is the same in every run. A class scope is followed as it is given.

        Args:
            iri: The IRI of the class to specify
            ogm: The OGM instance with access to the triple store
            class_scope: The ClassScope defining the class and property structure
            hydration_level: Whether to include all immediate properties or not
            _path: The classes being specified above this one, from the root. Internal to
                the recursion.
            _known_types: The class's rdf:type values, when the caller has read them
                already. Internal.
            _interfaces: The interfaces declared for the call and its merge depth, for every
                parameter property on the way down. Internal.

        Returns:
            A ClassSpec instance representing the class specification"""
        db = ogm.db

        ### Collect special properties
        # Get all types of the class
        if _known_types is not None:
            class_types = _known_types
        else:
            triples = db.triples_get(
                sub=class_iri, pred="rdf:type", include_implicit=True
            )
            class_types = [triple[2] for triple in triples]
        if not class_types:
            raise OGMOntologyError(
                f"Class {class_iri} has no rdf:type defined.", class_iri=class_iri
            )
        if IRI("owl:Class") not in class_types and IRI("rdfs:Class") not in class_types:
            raise OGMOntologyError(
                f"IRI {class_iri} is not an OWL/RDFS Class.", class_iri=class_iri
            )

        class_spec = cls(
            iri=class_iri,
            types=class_types,
            hydration_level=hydration_level,
        )

        # Get one label of the class
        label_triples = db.triples_get(
            sub=class_iri, pred="rdfs:label", include_implicit=True
        )
        if label_triples:
            class_spec.label = str(label_triples.pop()[2])
            if label_triples:
                logger.warning(
                    f"Class {class_iri} has multiple rdfs:label values; using {class_spec.label!r}."
                )

        # Get one comment of the class
        comment_triples = db.triples_get(
            sub=class_iri, pred="rdfs:comment", include_implicit=True
        )
        if comment_triples:
            class_spec.comment = str(comment_triples.pop()[2])
            if comment_triples:
                logger.warning(
                    f"Class {class_iri} has multiple rdfs:comment values; using {class_spec.comment!r}."
                )

        # Get the superclasses of the class
        superclass_triples = db.triples_get(
            sub=class_iri, pred="rdfs:subClassOf", include_implicit=True
        )
        superclasses = [triple[2] for triple in superclass_triples]
        if class_iri in superclasses:
            superclasses.remove(class_iri)
        if superclasses:
            class_spec.superclasses = superclasses

        # skip property hydration if only reference is needed or desired class_scope is empty
        if hydration_level is ClassHydrationLevel.REFERENCE or (
            hydration_level is ClassHydrationLevel.SCOPE and not class_scope
        ):
            return class_spec

        ### Build property specs of the class
        if hydration_level is ClassHydrationLevel.SCOPE and class_scope is None:
            raise OGMUsageError(
                "ClassScope must be provided when hydration_level is 'SCOPE'."
            )

        query_result = db.query(
            _property_discovery_query(class_iri).to_string(validate=False),
            convert_bindings=True,
        )
        property_list = [
            b["property"] for b in query_result.get("results", {}).get("bindings", [])
        ]
        properties = set(property_list)
        if len(property_list) != len(properties):
            logger.warning(
                f"Properties of {class_iri} contain duplicates, likely due to multiple superclasses defining the same property: {properties}"
            )

        if hydration_level is ClassHydrationLevel.SCOPE:
            expected_properties = set(class_scope.keys())
            missing_properties = expected_properties - properties
            if missing_properties:
                raise OGMDataError(
                    f"Properties {missing_properties} specified in class scope, but not found as property of class {class_spec.iri}.",
                    property_iri=_one_or_none(missing_properties),
                    class_iri=class_iri,
                )
            properties = expected_properties

        maximum_counts = cls._maximum_counts(class_iri, ogm)
        path = _path + (class_iri,)
        for prop in sorted(properties):
            nested_scope = class_scope.get(prop, None) if class_scope else None
            class_spec.properties[prop] = PropertySpec.specify(
                prop_iri=prop,
                nested_scope=nested_scope,
                ogm=ogm,
                hydration_level=hydration_level,
                _path=path,
                _interfaces=_interfaces,
            )
            class_spec.properties[prop].max_count = _tighten_bound(
                class_spec.properties[prop].max_count, maximum_counts.get(prop), min
            )

        return class_spec

    def _with_interfaces(
        self,
        prop: IRI,
        interfaces: InterfaceResolution,
        ogm: "OGM",
    ) -> ClassSpec:
        """A copy of this ClassSpec in which the parameter property ``prop`` is specified
        again, with the interfaces and the merge depth of ``interfaces``.

        This ClassSpec is left as it is, so a ClassSpec that another instance or a caller holds
        never changes. The class's own maximum count of ``prop`` is applied again, as
        ``specify`` applies it."""
        prop_spec = PropertySpec.specify(
            prop_iri=prop,
            nested_scope=None,
            ogm=ogm,
            hydration_level=self.hydration_level,
            _interfaces=interfaces,
        )
        if self.iri is not None:
            prop_spec.max_count = _tighten_bound(
                prop_spec.max_count, self._maximum_counts(self.iri, ogm).get(prop), min
            )
        return replace(self, properties={**self.properties, prop: prop_spec})
