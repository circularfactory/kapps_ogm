from __future__ import annotations

from typing import Any, Optional, TYPE_CHECKING
import pydantic as pd

from kapps_triplestore_interface import IRI

from kapps_ogm.mapping.class_spec import ClassSpec, ClassHydrationLevel
from kapps_ogm.mapping.property_spec import PropertySpec, PropertyValueKind
from kapps_ogm.utils.class_scope import ClassScope

if TYPE_CHECKING:
    from kapps_ogm.ogm import OGM


def _blank_value_for_property(ogm: "OGM", prop: PropertySpec) -> Any:
    """
    Produce a blank value for a property based on its kind and hydration.
    - data/literal: None
    - object: nested blank instance if hydrated, else None (IRI placeholder)
    - complex: requires hydrated nested ClassSpec; returns nested blank instance
    """
    if prop.value_kind is PropertyValueKind.LITERAL:
        return None

    if prop.value_kind is PropertyValueKind.OBJECT:
        if (
            prop.nested
            and not prop.nested.hydration_level is ClassHydrationLevel.REFERENCE
        ):
            return _blank_instance_from_class_spec(ogm, class_spec=prop.nested)
        return None  # unresolved object → IRI placeholder

    if prop.value_kind is PropertyValueKind.COMPLEX:
        if not (
            prop.nested and prop.nested.hydration_level is ClassHydrationLevel.FULL
        ):
            raise RuntimeError(
                f"Complex property {prop.iri} requires fully hydrated nested ClassSpec"
            )
        return _blank_instance_from_class_spec(ogm, class_spec=prop.nested)

    raise ValueError(f"Unknown value_kind: {prop.value_kind}")


def _blank_instance_from_class_spec(
    ogm: "OGM",
    *,
    class_spec: ClassSpec,
    instance_iri: Optional[IRI] = None,
) -> pd.BaseModel:
    """Build a blank pydantic instance for a given ClassSpec."""
    model_cls = class_spec.to_pydantic_model()

    data: dict[str, Any] = {}

    # attach id only at root or when explicitly provided
    if instance_iri is not None:
        data["id"] = instance_iri

    for prop in class_spec.properties.values():
        field_name = prop.iri.lined

        # Always lists
        values: list[Any] = []

        # Always add one blank value, regardless of requiredness
        values.append(_blank_value_for_property(ogm, prop))

        data[field_name] = values

    return model_cls.model_construct(**data)


def _create_blank_instance(
    ogm: "OGM",
    *,
    instance_iri: IRI,
    class_iri: IRI,
    class_scope: Optional[ClassScope] = None,
    hydration_level: bool = False,
) -> pd.BaseModel:
    """Resolve schema (respecting class scope) and return a blank instance."""
    class_spec = ClassSpec.specify(
        class_iri=class_iri,
        ogm=ogm,
        class_scope=class_scope,
        hydration_level=hydration_level,
    )

    return _blank_instance_from_class_spec(
        ogm,
        class_spec=class_spec,
        instance_iri=instance_iri,
    )
