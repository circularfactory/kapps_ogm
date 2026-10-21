"""The validator's two literal type checks name the values that the check itself found.

A literal property is checked against the datatype of an `owl:allValuesFrom` and of an
`owl:someValuesFrom` restriction. An ontology gives both on one property when two restrictions
merge, so the tests build that specification by hand, as the tests of the OWL minimums do. The
store is not touched: a validation reads nothing.
"""

import logging

import pytest
from rdflib import BNode

from kapps_triplestore_interface import IRI

from kapps_ogm.mapping.class_spec import ClassHydrationLevel, ClassSpec
from kapps_ogm.mapping.property_spec import PropertySpec, PropertyValueKind
from kapps_ogm.node.core import Node
from kapps_ogm.node.node_validator import NodeValidator
from kapps_ogm.utils.errors import OGMDataError

NS = "https://example.org/validatormessages#"
THING = IRI(NS + "Thing")
CHILD = IRI(NS + "Child")
HAS_VALUE = IRI(NS + "hasValue")
HAS_PART = IRI(NS + "hasPart")
NODE_ID = IRI(NS + "thing1")

LITERAL = PropertyValueKind.LITERAL


def thing_spec(**restrictions) -> ClassSpec:
    return ClassSpec(
        iri=THING,
        properties={
            HAS_VALUE: PropertySpec(
                iri=HAS_VALUE,
                value_kind=LITERAL,
                python_range_type=float,
                **restrictions,
            )
        },
        hydration_level=ClassHydrationLevel.FULL,
    )


def thing(values: list, **restrictions) -> Node:
    return Node(
        id=NODE_ID, class_spec=thing_spec(**restrictions), data={HAS_VALUE: values}
    )


class TestAllValuesFrom:
    def test_the_message_names_every_offending_value_with_its_type(self):
        values = [1.5, "fast", 2.5, 7]
        node = thing(values, all_from=float)

        with pytest.raises(OGMDataError) as raised:
            NodeValidator.validate(node)

        message = str(raised.value)
        assert "expected owl:allValuesFrom" in message
        assert message.endswith("got 'fast' (str), 7 (int)")
        assert raised.value.value == values
        assert raised.value.instance_iri == NODE_ID
        assert raised.value.property_iri == HAS_VALUE
        assert raised.value.class_iri == THING

    def test_values_of_the_datatype_validate(self):
        NodeValidator.validate(thing([1.5, 2.5], all_from=float))


class TestSomeValuesFrom:
    def test_strict_refuses_and_names_the_types_found(self):
        values = ["fast", 7]
        node = thing(values, some_from=float)

        with pytest.raises(OGMDataError) as raised:
            NodeValidator.validate(node, strict=True)

        message = str(raised.value)
        assert "expected owl:someValuesFrom" in message
        assert message.endswith("got values of type str, int")
        assert raised.value.value == values
        assert raised.value.property_iri == HAS_VALUE

    def test_without_strict_it_validates_and_warns_once(self, caplog):
        node = thing(["fast", 7], some_from=float)

        with caplog.at_level(logging.WARNING, logger="kapps_node_validator"):
            NodeValidator.validate(node)

        warnings = [
            r.getMessage() for r in caplog.records if "someValuesFrom" in r.getMessage()
        ]
        assert len(warnings) == 1
        assert warnings[0].endswith("got values of type str, int")

    def test_one_value_of_the_datatype_is_enough(self):
        NodeValidator.validate(thing(["fast", 1.5], some_from=float), strict=True)


class TestBesideAnObjectProperty:
    """A nested node visited first never leaks into the literal property's message."""

    @staticmethod
    def parent(values: list, **restrictions) -> Node:
        child_spec = ClassSpec(
            iri=CHILD,
            properties={
                HAS_VALUE: PropertySpec(
                    iri=HAS_VALUE, value_kind=LITERAL, python_range_type=float
                )
            },
            hydration_level=ClassHydrationLevel.FULL,
        )
        child = Node(id=BNode(), data={HAS_VALUE: [1.0]})
        spec = thing_spec(**restrictions)
        spec.properties[HAS_PART] = PropertySpec(
            iri=HAS_PART, value_kind=PropertyValueKind.OBJECT
        )
        # The nested node sits beside the literal property, as a leaked loop variable would name it.
        data = {HAS_PART: [child], HAS_VALUE: values}
        parent = Node(id=NODE_ID, class_spec=spec, data=data)
        # Giving the parent its spec hands the child none, so the child gets its own after.
        child.class_spec = child_spec
        return parent

    def test_all_values_from_names_the_literal_values(self):
        with pytest.raises(OGMDataError) as raised:
            NodeValidator.validate(self.parent(["fast"], all_from=float))

        assert "'fast'" in str(raised.value)
        assert "Node" not in str(raised.value).split("got", 1)[1]

    def test_some_values_from_names_the_literal_types(self):
        with pytest.raises(OGMDataError) as raised:
            NodeValidator.validate(self.parent(["fast"], some_from=float), strict=True)

        assert "str" in str(raised.value)
        assert "Node" not in str(raised.value).split("got", 1)[1]
