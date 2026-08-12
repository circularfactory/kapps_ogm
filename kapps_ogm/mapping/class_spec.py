from __future__ import annotations

from typing import Optional, Type, Any, Dict, List, TYPE_CHECKING
from enum import Enum
from dataclasses import dataclass, field, asdict
import logging
import pydantic as pd

from pydantic import ConfigDict  # Pydantic v2


from kapps_triplestore_interface import IRI
from kapps_ogm.utils.pretty_print import format_class_spec
from kapps_ogm.mapping.anonymous_model import AnonymousNodeModel
from kapps_ogm.mapping.property_spec import PropertySpec, PropertyValueKind
from kapps_ogm.utils.class_scope import ClassScope

if TYPE_CHECKING:
    from kapps_ogm.ogm import OGM

logger = logging.getLogger("kapps_cspec")
logger.setLevel(logging.INFO)


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
            raise ValueError("Cannot hydrate ClassSpec without an IRI.")

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
        )  # TODO: use graphdbs blanknode generator?

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

        # Enforce no unknown properties at model level (replaces Node._validate_data unknown-check)
        if ConfigDict is not None:
            model_cls.model_config = ConfigDict(extra="forbid")
        else:

            class Config(getattr(self.pydantic_base_model, "Config", object)):
                extra = "forbid"

            model_cls.Config = Config

        # Keep mapping to IRIs for reference
        setattr(
            model_cls,
            "_iri_fields",
            {prop_iri.lined: prop_iri for prop_iri in self.properties.keys()},
        )
        setattr(model_cls, "_iri_model_name", self.iri if self.iri else None)

        return model_cls

    @classmethod
    def specify_from_instance(
        cls,
        model_cls: Type[pd.BaseModel],
        instance: pd.BaseModel,
        ogm: "OGM",
        hydration_level: ClassHydrationLevel,
    ) -> ClassSpec:
        """
        create a ClassSpec for the given Pydantic model by analyzing its RDF data in the GraphDB via the OGM instance.

            Args:
                model_cls: The Pydantic model class to specify
                ogm: The OGM instance with access to the GraphDB
            Returns:
                A ClassSpec instance representing the class specification"""
        iri = getattr(model_cls, "_iri_model_name", None)
        if iri is None:
            raise ValueError(f"Model {model_cls.__name__} has no associated IRI.")

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
    ) -> ClassSpec:
        """
        create a ClassSpec for the given IRI by analyzing the ClassScope, its RDF data in the GraphDB via the OGM instance.

            Args:
                iri: The IRI of the class to specify
                ogm: The OGM instance with access to the GraphDB
                class_scope: The ClassScope defining the class and property structure
                hydration_level: Whether to include all immediate properties or not
            Returns:
                A ClassSpec instance representing the class specification"""
        db = ogm.db

        ### Collect special properties
        # Get all types of the class
        triples = db.triples_get(sub=class_iri, pred="rdf:type", include_implicit=True)
        class_types = [triple[2] for triple in triples]
        if not class_types:
            raise ValueError(f"Class {class_iri} has no rdf:type defined.")
        if IRI("owl:Class") not in class_types and IRI("rdfs:Class") not in class_types:
            raise ValueError(f"IRI {class_iri} is not an OWL/RDFS Class.")

        class_spec = cls(
            iri=class_iri,
            types=class_types,
            hydration_level=hydration_level,
        )

        # Get the (first) label of the class
        label_triples = db.triples_get(
            sub=class_iri, pred="rdfs:label", include_implicit=True
        )
        if label_triples:
            if len(label_triples) > 1:
                logger.warning(
                    f"Class {class_iri} has multiple rdfs:label values; using the first one."
                )
            class_spec.label = str(label_triples.pop()[2])

        # Get the (first) comment of the class
        comment_triples = db.triples_get(
            sub=class_iri, pred="rdfs:comment", include_implicit=True
        )
        if comment_triples:
            if len(comment_triples) > 1:
                logger.warning(
                    f"Class {class_iri} has multiple rdfs:comment values; using the first one."
                )
            class_spec.comment = str(comment_triples.pop()[2])

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
            raise ValueError(
                "ClassScope must be provided when hydration_level is 'SCOPE'."
            )

        query = f"""
            PREFIX onto: <http://www.ontotext.com/>
            PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

            SELECT ?property
            FROM onto:explicit
            WHERE {{
                {{
                    ?property rdfs:domain ?class .
                }}
                UNION
                {{
                    ?property rdfs:domain ?union_class .
                    ?union_class owl:unionOf ?list .
                    ?list rdf:rest*/rdf:first ?class .
                }} .
                <{class_iri}> rdfs:subClassOf* ?class.
            }}
        """
        query_result = db.query(query, convert_bindings=True)
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
                raise ValueError(
                    f"Properties {missing_properties} specified in class scope, but not found as property of class {class_spec.iri}."
                )
            properties = expected_properties

        for prop in properties:
            nested_scope = class_scope.get(prop, None) if class_scope else None
            class_spec.properties[prop] = PropertySpec.specify(
                prop_iri=prop,
                nested_scope=nested_scope,
                ogm=ogm,
                hydration_level=hydration_level,
            )

        return class_spec
