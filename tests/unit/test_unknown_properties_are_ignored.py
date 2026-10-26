"""Pin the contract: a model built for a class ignores undeclared keys, it does not refuse them.

Under the open world a graph carries more than any schema declares, so the model never rejects
unknown keys. The write paths of the OGM refuse an undeclared property before the model is
built; the model itself stays tolerant for the read path.
"""

import pytest

from kapps_triplestore_interface import IRI

from kapps_ogm.mapping.class_spec import ClassHydrationLevel, ClassSpec
from kapps_ogm.mapping.property_spec import PropertySpec, PropertyValueKind

ONTO = "http://www.sfb1574.kit.edu/ontologies/TransferUnit"
HAS_UNIT = IRI("hasUnit", ONTO)


@pytest.mark.parametrize("named", [True, False], ids=["named", "anonymous"])
def test_unknown_key_is_dropped_without_error(named: bool) -> None:
    spec = ClassSpec(
        iri=IRI("ConveyorBelt", ONTO) if named else None,
        types=[],
        properties={
            HAS_UNIT: PropertySpec(
                iri=HAS_UNIT,
                value_kind=PropertyValueKind.LITERAL,
                python_range_type=str,
            )
        },
        hydration_level=ClassHydrationLevel.FULL,
    )
    model = spec.to_pydantic_model()
    data: dict = {HAS_UNIT.lined: ["m"], "someComment": "not declared"}
    if named:
        data["id"] = IRI("belt1", ONTO)

    instance = model.model_validate(data)

    assert getattr(instance, HAS_UNIT.lined) == ["m"]
    assert "someComment" not in instance.model_dump()
    assert model.model_config.get("extra", "ignore") == "ignore"
