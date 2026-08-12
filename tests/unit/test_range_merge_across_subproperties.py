"""
Unit tests for merging anonymous rdfs:range restrictions across rdfs:subPropertyOf chains.

Tests for SAWeindel/kapps_ogm#7: merge anonymous rdfs:range restrictions across rdfs:subPropertyOf*
"""

import pytest
from pydantic import create_model, ValidationError
from rdflib import BNode, Literal, XSD
from kapps_triplestore_interface import IRI

from kapps_ogm.mapping.property_spec import PropertySpec, PropertyValueKind
from kapps_ogm.mapping.class_spec import ClassHydrationLevel, ClassSpec
from kapps_ogm.utils.class_scope import ClassScope
from kapps_ogm.ogm import OGM

NS = "https://example.org/rangemerge#"


def restriction(bnode: BNode, on_property: IRI, **facets) -> list[tuple]:
    """Build triples for a single owl:Restriction.

    Facets accept all_values_from, some_values_from, min_cardinality, max_cardinality.
    Cardinality literals are typed as xsd:nonNegativeInteger.
    """
    triples = [
        (bnode, IRI("rdf:type"), IRI("owl:Restriction")),
        (bnode, IRI("owl:onProperty"), on_property),
    ]

    if "all_values_from" in facets:
        triples.append((bnode, IRI("owl:allValuesFrom"), facets["all_values_from"]))

    if "some_values_from" in facets:
        triples.append((bnode, IRI("owl:someValuesFrom"), facets["some_values_from"]))

    if "min_cardinality" in facets:
        triples.append(
            (
                bnode,
                IRI("owl:minCardinality"),
                Literal(facets["min_cardinality"], datatype=XSD.nonNegativeInteger),
            )
        )

    if "max_cardinality" in facets:
        triples.append(
            (
                bnode,
                IRI("owl:maxCardinality"),
                Literal(facets["max_cardinality"], datatype=XSD.nonNegativeInteger),
            )
        )

    return triples


def intersection_range(
    subject: IRI, restrictions: list[tuple[BNode, list[tuple]]]
) -> list[tuple]:
    """Triples asserting `subject rdfs:range [ owl:intersectionOf ( <restrictions> ) ]`.

    `restrictions` is a list of (bnode, [(predicate, object), ...]) pairs. Returns a flat
    list of triples; the caller must add them in one `triples_add` call so the blank-node
    labels resolve to one node each.
    """
    if not restrictions:
        return []

    range_bnode = BNode()
    triples = [(subject, IRI("rdfs:range"), range_bnode)]

    # Build the RDF list for owl:intersectionOf
    list_nodes = []
    for _ in restrictions:
        list_nodes.append(BNode())
    list_nodes.append(IRI("rdf:nil"))

    triples.append((range_bnode, IRI("owl:intersectionOf"), list_nodes[0]))

    for i, (restr_bnode, restr_triples) in enumerate(restrictions):
        triples.extend(restr_triples)
        triples.append((list_nodes[i], IRI("rdf:first"), restr_bnode))
        triples.append((list_nodes[i], IRI("rdf:rest"), list_nodes[i + 1]))

    return triples


class TestMergeAcrossSubPropertyChain:
    """Tests that rdfs:range restrictions are merged across rdfs:subPropertyOf* ancestors."""

    def test_superproperty_restriction_is_merged_into_effective_shape(self, ogm: OGM):
        """The driving case: a property with an intersection range declaring hasUnit, whose
        superproperty has an intersection range declaring hasTopic, and whose grandsuperproperty
        declares accessMode. Assert the resulting spec is COMPLEX with all six nested properties.
        """
        NS_test = NS + "chain_"
        prop_iri = IRI(NS_test + "hasConveyorSpeed")
        superprop_iri = IRI(NS_test + "isInterfaceAccessibleMQTTParameter")
        grandsuperprop_iri = IRI(NS_test + "isInterfaceAccessibleParameter")

        hasValue = IRI(NS_test + "hasValue")
        hasUnit = IRI(NS_test + "hasUnit")
        hasMQTTTopic = IRI(NS_test + "hasMQTTTopic")
        hasMQTTBrokerIP = IRI(NS_test + "hasMQTTBrokerIP")
        hasMQTTSetTopic = IRI(NS_test + "hasMQTTSetTopic")
        accessMode = IRI(NS_test + "accessMode")

        # Grandsuperproperty range: accessMode
        b1 = BNode()
        triples_grand = intersection_range(
            grandsuperprop_iri,
            [(b1, restriction(b1, accessMode, all_values_from=IRI("xsd:string")))],
        )

        # Superproperty range: hasMQTTTopic, hasMQTTBrokerIP, hasMQTTSetTopic
        b2 = BNode()
        b3 = BNode()
        b4 = BNode()
        triples_super = intersection_range(
            superprop_iri,
            [
                (b2, restriction(b2, hasMQTTTopic, all_values_from=IRI("xsd:string"))),
                (
                    b3,
                    restriction(b3, hasMQTTBrokerIP, all_values_from=IRI("xsd:string")),
                ),
                (
                    b4,
                    restriction(b4, hasMQTTSetTopic, all_values_from=IRI("xsd:string")),
                ),
            ],
        )

        # Property range: hasValue, hasUnit
        b5 = BNode()
        b6 = BNode()
        triples_prop = intersection_range(
            prop_iri,
            [
                (b5, restriction(b5, hasValue, all_values_from=IRI("xsd:float"))),
                (b6, restriction(b6, hasUnit, all_values_from=IRI("xsd:string"))),
            ],
        )

        # Subproperty chain
        chain_triples = [
            (prop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty")),
            (prop_iri, IRI("rdfs:subPropertyOf"), superprop_iri),
            (superprop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty")),
            (superprop_iri, IRI("rdfs:subPropertyOf"), grandsuperprop_iri),
            (grandsuperprop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty")),
        ]

        ogm.db.triples_add(triples_grand, check_exist=False)
        ogm.db.triples_add(triples_super, check_exist=False)
        ogm.db.triples_add(triples_prop, check_exist=False)
        ogm.db.triples_add(chain_triples, check_exist=False)

        prop_spec = PropertySpec.specify(
            prop_iri=prop_iri,
            ogm=ogm,
            nested_scope=ClassScope(),
            hydration_level=True,
        )

        assert prop_spec.value_kind is PropertyValueKind.COMPLEX
        assert prop_spec.nested.iri is None
        assert set(prop_spec.nested.properties.keys()) == {
            hasValue,
            hasUnit,
            hasMQTTTopic,
            hasMQTTBrokerIP,
            hasMQTTSetTopic,
            accessMode,
        }

    def test_two_ranges_on_the_same_property_merge_instead_of_raising(self, ogm: OGM):
        """One property, two separate rdfs:range assertions, each an intersection with a different
        restriction. Assert both nested properties are present and no exception is raised.
        """
        NS_test = NS + "twoRanges_"
        prop_iri = IRI(NS_test + "hasDualRange")
        propA = IRI(NS_test + "propA")
        propB = IRI(NS_test + "propB")

        b1 = BNode()
        triples1 = intersection_range(
            prop_iri, [(b1, restriction(b1, propA, all_values_from=IRI("xsd:string")))]
        )

        b2 = BNode()
        triples2 = intersection_range(
            prop_iri, [(b2, restriction(b2, propB, all_values_from=IRI("xsd:int")))]
        )

        type_triples = [(prop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty"))]

        ogm.db.triples_add(triples1, check_exist=False)
        ogm.db.triples_add(triples2, check_exist=False)
        ogm.db.triples_add(type_triples, check_exist=False)

        prop_spec = PropertySpec.specify(
            prop_iri=prop_iri,
            ogm=ogm,
            nested_scope=ClassScope(),
            hydration_level=True,
        )

        assert prop_spec.value_kind is PropertyValueKind.COMPLEX
        assert propA in prop_spec.nested.properties
        assert propB in prop_spec.nested.properties

    def test_superproperty_without_a_range_contributes_nothing(self, ogm: OGM):
        """Property with one intersection range, superproperty with no rdfs:range at all.
        Assert it resolves to exactly the property's own nested properties and does not raise.
        """
        NS_test = NS + "noRangeSuper_"
        prop_iri = IRI(NS_test + "hasSpeed")
        superprop_iri = IRI(NS_test + "baseParam")
        hasSpeed = IRI(NS_test + "hasSpeedVal")

        b1 = BNode()
        triples_prop = intersection_range(
            prop_iri,
            [(b1, restriction(b1, hasSpeed, all_values_from=IRI("xsd:float")))],
        )

        chain_triples = [
            (prop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty")),
            (prop_iri, IRI("rdfs:subPropertyOf"), superprop_iri),
            (superprop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty")),
        ]

        ogm.db.triples_add(triples_prop, check_exist=False)
        ogm.db.triples_add(chain_triples, check_exist=False)

        prop_spec = PropertySpec.specify(
            prop_iri=prop_iri,
            ogm=ogm,
            nested_scope=ClassScope(),
            hydration_level=True,
        )

        assert prop_spec.value_kind is PropertyValueKind.COMPLEX
        assert set(prop_spec.nested.properties.keys()) == {hasSpeed}

    def test_cyclic_subproperty_assertions_terminate(self, ogm: OGM):
        """a rdfs:subPropertyOf b, b rdfs:subPropertyOf a, each with its own intersection range.
        Assert specify returns and the merged shape contains both restrictions' properties.
        """
        NS_test = NS + "cycle_"
        prop_a = IRI(NS_test + "propA")
        prop_b = IRI(NS_test + "propB")
        valA = IRI(NS_test + "valA")
        valB = IRI(NS_test + "valB")

        b1 = BNode()
        triples_a = intersection_range(
            prop_a, [(b1, restriction(b1, valA, all_values_from=IRI("xsd:string")))]
        )

        b2 = BNode()
        triples_b = intersection_range(
            prop_b, [(b2, restriction(b2, valB, all_values_from=IRI("xsd:int")))]
        )

        cycle_triples = [
            (prop_a, IRI("rdf:type"), IRI("owl:ObjectProperty")),
            (prop_b, IRI("rdf:type"), IRI("owl:ObjectProperty")),
            (prop_a, IRI("rdfs:subPropertyOf"), prop_b),
            (prop_b, IRI("rdfs:subPropertyOf"), prop_a),
        ]

        ogm.db.triples_add(triples_a, check_exist=False)
        ogm.db.triples_add(triples_b, check_exist=False)
        ogm.db.triples_add(cycle_triples, check_exist=False)

        prop_spec = PropertySpec.specify(
            prop_iri=prop_a,
            ogm=ogm,
            nested_scope=ClassScope(),
            hydration_level=True,
        )

        assert prop_spec.value_kind is PropertyValueKind.COMPLEX
        assert valA in prop_spec.nested.properties
        assert valB in prop_spec.nested.properties

    def test_bare_restriction_range_does_not_absorb_unrelated_restrictions(
        self, ogm: OGM
    ):
        """A property whose rdfs:range is a bare owl:Restriction (no owl:intersectionOf).
        Assert nested.properties contains exactly that one restriction's property.
        """
        NS_test = NS + "bare_"
        prop_iri = IRI(NS_test + "hasBareRange")
        target_prop = IRI(NS_test + "targetProp")

        range_bnode = BNode()
        triples = [
            (prop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty")),
            (prop_iri, IRI("rdfs:range"), range_bnode),
            (range_bnode, IRI("rdf:type"), IRI("owl:Restriction")),
            (range_bnode, IRI("owl:onProperty"), target_prop),
            (range_bnode, IRI("owl:allValuesFrom"), IRI("xsd:string")),
        ]

        ogm.db.triples_add(triples, check_exist=False)

        prop_spec = PropertySpec.specify(
            prop_iri=prop_iri,
            ogm=ogm,
            nested_scope=ClassScope(),
            hydration_level=True,
        )

        assert prop_spec.value_kind is PropertyValueKind.COMPLEX
        assert set(prop_spec.nested.properties.keys()) == {target_prop}


class TestMergeConflictRules:
    """Tests for conflict detection and resolution during range merging."""

    def test_same_property_same_datatype_merges(self, ogm: OGM):
        """The same onProperty with allValuesFrom xsd:string on both the property and its
        superproperty. Assert one entry, python_range_type is str, no raise.
        """
        NS_test = NS + "sameType_"
        prop_iri = IRI(NS_test + "hasVal")
        superprop_iri = IRI(NS_test + "baseVal")
        shared_prop = IRI(NS_test + "sharedProp")

        b1 = BNode()
        triples_prop = intersection_range(
            prop_iri,
            [(b1, restriction(b1, shared_prop, all_values_from=IRI("xsd:string")))],
        )

        b2 = BNode()
        triples_super = intersection_range(
            superprop_iri,
            [(b2, restriction(b2, shared_prop, all_values_from=IRI("xsd:string")))],
        )

        chain_triples = [
            (prop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty")),
            (prop_iri, IRI("rdfs:subPropertyOf"), superprop_iri),
            (superprop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty")),
        ]

        ogm.db.triples_add(triples_prop, check_exist=False)
        ogm.db.triples_add(triples_super, check_exist=False)
        ogm.db.triples_add(chain_triples, check_exist=False)

        prop_spec = PropertySpec.specify(
            prop_iri=prop_iri,
            ogm=ogm,
            nested_scope=ClassScope(),
            hydration_level=True,
        )

        assert prop_spec.value_kind is PropertyValueKind.COMPLEX
        assert shared_prop in prop_spec.nested.properties
        assert prop_spec.nested.properties[shared_prop].python_range_type is str

    def test_same_property_incompatible_datatypes_raises(self, ogm: OGM):
        """allValuesFrom xsd:string versus allValuesFrom xsd:int. Assert ValueError whose message
        names both the offending nested property and the owning property.
        """
        NS_test = NS + "incompat_"
        prop_iri = IRI(NS_test + "hasVal")
        superprop_iri = IRI(NS_test + "baseVal")
        shared_prop = IRI(NS_test + "sharedProp")

        b1 = BNode()
        triples_prop = intersection_range(
            prop_iri,
            [(b1, restriction(b1, shared_prop, all_values_from=IRI("xsd:string")))],
        )

        b2 = BNode()
        triples_super = intersection_range(
            superprop_iri,
            [(b2, restriction(b2, shared_prop, all_values_from=IRI("xsd:int")))],
        )

        chain_triples = [
            (prop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty")),
            (prop_iri, IRI("rdfs:subPropertyOf"), superprop_iri),
            (superprop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty")),
        ]

        ogm.db.triples_add(triples_prop, check_exist=False)
        ogm.db.triples_add(triples_super, check_exist=False)
        ogm.db.triples_add(chain_triples, check_exist=False)

        with pytest.raises(ValueError, match=str(shared_prop)):
            PropertySpec.specify(
                prop_iri=prop_iri,
                ogm=ogm,
                nested_scope=ClassScope(),
                hydration_level=True,
            )

    def test_differing_cardinalities_take_the_most_restrictive(self, ogm: OGM):
        """minCardinality 1 on one side, maxCardinality 3 on one side and maxCardinality 2 on the
        other. Assert merged min_count == 1 and max_count == 2.

        Both sides carry a maximum so the merge has to choose between them, rather than
        inheriting the only bound that was set.
        """
        NS_test = NS + "card_"
        prop_iri = IRI(NS_test + "hasVal")
        superprop_iri = IRI(NS_test + "baseVal")
        shared_prop = IRI(NS_test + "sharedProp")

        b1 = BNode()
        triples_prop = intersection_range(
            prop_iri,
            [
                (
                    b1,
                    restriction(
                        b1,
                        shared_prop,
                        all_values_from=IRI("xsd:string"),
                        min_cardinality=1,
                        max_cardinality=3,
                    ),
                )
            ],
        )

        b2 = BNode()
        triples_super = intersection_range(
            superprop_iri,
            [
                (
                    b2,
                    restriction(
                        b2,
                        shared_prop,
                        all_values_from=IRI("xsd:string"),
                        max_cardinality=2,
                    ),
                )
            ],
        )

        chain_triples = [
            (prop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty")),
            (prop_iri, IRI("rdfs:subPropertyOf"), superprop_iri),
            (superprop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty")),
        ]

        ogm.db.triples_add(triples_prop, check_exist=False)
        ogm.db.triples_add(triples_super, check_exist=False)
        ogm.db.triples_add(chain_triples, check_exist=False)

        prop_spec = PropertySpec.specify(
            prop_iri=prop_iri,
            ogm=ogm,
            nested_scope=ClassScope(),
            hydration_level=True,
        )

        assert prop_spec.nested.properties[shared_prop].min_count == 1
        assert prop_spec.nested.properties[shared_prop].max_count == 2

    def test_unsatisfiable_cardinalities_raise(self, ogm: OGM):
        """minCardinality 3 versus maxCardinality 1. Assert ValueError with 'unsatisfiable'."""
        NS_test = NS + "unsat_"
        prop_iri = IRI(NS_test + "hasVal")
        superprop_iri = IRI(NS_test + "baseVal")
        shared_prop = IRI(NS_test + "sharedProp")

        b1 = BNode()
        triples_prop = intersection_range(
            prop_iri,
            [
                (
                    b1,
                    restriction(
                        b1,
                        shared_prop,
                        all_values_from=IRI("xsd:string"),
                        min_cardinality=3,
                    ),
                )
            ],
        )

        b2 = BNode()
        triples_super = intersection_range(
            superprop_iri,
            [
                (
                    b2,
                    restriction(
                        b2,
                        shared_prop,
                        all_values_from=IRI("xsd:string"),
                        max_cardinality=1,
                    ),
                )
            ],
        )

        chain_triples = [
            (prop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty")),
            (prop_iri, IRI("rdfs:subPropertyOf"), superprop_iri),
            (superprop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty")),
        ]

        ogm.db.triples_add(triples_prop, check_exist=False)
        ogm.db.triples_add(triples_super, check_exist=False)
        ogm.db.triples_add(chain_triples, check_exist=False)

        with pytest.raises(ValueError, match="unsatisfiable"):
            PropertySpec.specify(
                prop_iri=prop_iri,
                ogm=ogm,
                nested_scope=ClassScope(),
                hydration_level=True,
            )

    def test_named_range_mixed_with_anonymous_restriction_raises(self, ogm: OGM):
        """Property with a named class range, superproperty with an intersection restriction range.
        Assert ValueError naming the property, matching on the word 'anonymous'.
        """
        NS_test = NS + "mixed_"
        prop_iri = IRI(NS_test + "hasVal")
        superprop_iri = IRI(NS_test + "baseVal")
        named_class = IRI(NS_test + "NamedClass")

        triples_prop = [
            (prop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty")),
            (prop_iri, IRI("rdfs:range"), named_class),
        ]

        b1 = BNode()
        triples_super = intersection_range(
            superprop_iri,
            [
                (
                    b1,
                    restriction(
                        b1, IRI(NS_test + "prop"), all_values_from=IRI("xsd:string")
                    ),
                )
            ],
        )

        chain_triples = [
            (prop_iri, IRI("rdfs:subPropertyOf"), superprop_iri),
            (superprop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty")),
        ]

        ogm.db.triples_add(triples_prop, check_exist=False)
        ogm.db.triples_add(triples_super, check_exist=False)
        ogm.db.triples_add(chain_triples, check_exist=False)

        with pytest.raises(ValueError, match="anonymous"):
            PropertySpec.specify(
                prop_iri=prop_iri,
                ogm=ogm,
                nested_scope=ClassScope(),
                hydration_level=True,
            )

    def test_two_unrelated_named_ranges_across_the_chain_raise(self, ogm: OGM):
        """Property with named range ClassA, superproperty with unrelated named range ClassB.
        Assert ValueError matching 'has multiple.*rdfs:range defined'.
        """
        NS_test = NS + "multiNamed_"
        prop_iri = IRI(NS_test + "hasVal")
        superprop_iri = IRI(NS_test + "baseVal")
        class_a = IRI(NS_test + "ClassA")
        class_b = IRI(NS_test + "ClassB")

        triples_prop = [
            (prop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty")),
            (prop_iri, IRI("rdfs:range"), class_a),
        ]

        triples_super = [
            (superprop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty")),
            (superprop_iri, IRI("rdfs:range"), class_b),
        ]

        chain_triples = [(prop_iri, IRI("rdfs:subPropertyOf"), superprop_iri)]

        ogm.db.triples_add(triples_prop, check_exist=False)
        ogm.db.triples_add(triples_super, check_exist=False)
        ogm.db.triples_add(chain_triples, check_exist=False)

        with pytest.raises(ValueError, match="has multiple.*rdfs:range defined"):
            PropertySpec.specify(
                prop_iri=prop_iri,
                ogm=ogm,
                nested_scope=ClassScope(),
                hydration_level=True,
            )

    def test_most_specific_named_range_still_wins_across_the_chain(self, ogm: OGM):
        """Property with named range SubClass, superproperty with named range SuperClass, and
        SubClass rdfs:subClassOf SuperClass asserted. Assert resolved nested.iri == SubClass.
        """
        NS_test = NS + "specific_"
        prop_iri = IRI(NS_test + "hasVal")
        superprop_iri = IRI(NS_test + "baseVal")
        sub_class = IRI(NS_test + "SubClass")
        super_class = IRI(NS_test + "SuperClass")

        triples_prop = [
            (prop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty")),
            (prop_iri, IRI("rdfs:range"), sub_class),
        ]

        triples_super = [
            (superprop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty")),
            (superprop_iri, IRI("rdfs:range"), super_class),
        ]

        chain_triples = [
            (prop_iri, IRI("rdfs:subPropertyOf"), superprop_iri),
            (sub_class, IRI("rdf:type"), IRI("owl:Class")),
            (super_class, IRI("rdf:type"), IRI("owl:Class")),
            (sub_class, IRI("rdfs:subClassOf"), super_class),
        ]

        ogm.db.triples_add(triples_prop, check_exist=False)
        ogm.db.triples_add(triples_super, check_exist=False)
        ogm.db.triples_add(chain_triples, check_exist=False)

        prop_spec = PropertySpec.specify(
            prop_iri=prop_iri,
            ogm=ogm,
            nested_scope=ClassScope(),
            hydration_level=True,
        )

        assert prop_spec.value_kind is PropertyValueKind.OBJECT
        assert prop_spec.nested.iri == sub_class


class TestSingleRangeIsUnchanged:
    """Tests that single-range properties resolve exactly as before the chain-walk change."""

    def test_single_named_range_resolves_as_before(self, ogm: OGM):
        """An object property with one named class range and no superproperty resolves to
        PropertyValueKind.OBJECT with nested.iri == target.
        """
        NS_test = NS + "singleNamed_"
        prop_iri = IRI(NS_test + "hasTarget")
        target_class = IRI(NS_test + "TargetClass")

        ogm.db.triples_add(
            [
                (prop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty")),
                (prop_iri, IRI("rdfs:range"), target_class),
            ],
            check_exist=False,
        )

        prop_spec = PropertySpec.specify(
            prop_iri=prop_iri,
            ogm=ogm,
            nested_scope=ClassScope(),
            hydration_level=True,
        )

        assert prop_spec.value_kind is PropertyValueKind.OBJECT
        assert prop_spec.nested.iri == target_class

    def test_single_literal_range_resolves_as_before(self, ogm: OGM):
        """A datatype property with rdfs:range xsd:string and no superproperty resolves to
        PropertyValueKind.LITERAL with python_range_type is str.
        """
        NS_test = NS + "singleLiteral_"
        prop_iri = IRI(NS_test + "hasName")

        ogm.db.triples_add(
            [
                (prop_iri, IRI("rdf:type"), IRI("owl:DatatypeProperty")),
                (prop_iri, IRI("rdfs:range"), IRI("xsd:string")),
            ],
            check_exist=False,
        )

        prop_spec = PropertySpec.specify(
            prop_iri=prop_iri,
            ogm=ogm,
            nested_scope=ClassScope(),
            hydration_level=True,
        )

        assert prop_spec.value_kind is PropertyValueKind.LITERAL
        assert prop_spec.python_range_type is str


class TestMergeConjunctive:
    """Direct unit tests for PropertySpec.merge_conjunctive with hand-built PropertySpec objects,
    no database. These are the fast tests; the GraphDB-backed ones above pin the SPARQL.
    """

    def test_same_datatype_merges(self):
        """Two restrictions on the same property with identical allValuesFrom xsd:string merge
        successfully.
        """
        prop_iri = IRI("https://example.org/mergeTest#ownerProp")
        nested_prop = IRI("https://example.org/mergeTest#nestedProp")

        spec1 = PropertySpec(
            iri=nested_prop,
            value_kind=PropertyValueKind.LITERAL,
            python_range_type=str,
            min_count=None,
            max_count=None,
            some_from=None,
            all_from=str,
            nested=None,
        )

        spec2 = PropertySpec(
            iri=nested_prop,
            value_kind=PropertyValueKind.LITERAL,
            python_range_type=str,
            min_count=None,
            max_count=None,
            some_from=None,
            all_from=str,
            nested=None,
        )

        merged = spec1.merge_conjunctive(spec2, owner_iri=prop_iri)

        assert merged.python_range_type is str
        assert merged.all_from is str

    def test_differing_datatypes_raises(self):
        """Two restrictions with incompatible allValuesFrom (xsd:string vs xsd:int) raise
        ValueError naming both properties.
        """
        prop_iri = IRI("https://example.org/mergeTest#ownerProp2")
        nested_prop = IRI("https://example.org/mergeTest#nestedProp2")

        spec1 = PropertySpec(
            iri=nested_prop,
            value_kind=PropertyValueKind.LITERAL,
            python_range_type=str,
            min_count=None,
            max_count=None,
            some_from=None,
            all_from=str,
            nested=None,
        )

        spec2 = PropertySpec(
            iri=nested_prop,
            value_kind=PropertyValueKind.LITERAL,
            python_range_type=int,
            min_count=None,
            max_count=None,
            some_from=None,
            all_from=int,
            nested=None,
        )

        with pytest.raises(ValueError, match="incompatible"):
            spec1.merge_conjunctive(spec2, owner_iri=prop_iri)

    def test_cardinality_most_restrictive(self):
        """Merged cardinalities take the most restrictive: min is max of inputs, max is min of
        inputs.
        """
        prop_iri = IRI("https://example.org/mergeTest#ownerProp3")
        nested_prop = IRI("https://example.org/mergeTest#nestedProp3")

        spec1 = PropertySpec(
            iri=nested_prop,
            value_kind=PropertyValueKind.OBJECT,
            python_range_type=None,
            min_count=1,
            max_count=5,
            some_from=None,
            all_from=None,
            nested=None,
        )

        spec2 = PropertySpec(
            iri=nested_prop,
            value_kind=PropertyValueKind.OBJECT,
            python_range_type=None,
            min_count=2,
            max_count=3,
            some_from=None,
            all_from=None,
            nested=None,
        )

        merged = spec1.merge_conjunctive(spec2, owner_iri=prop_iri)

        assert merged.min_count == 2
        assert merged.max_count == 3

    def test_unsatisfiable_cardinalities_raise(self):
        """When merged min_count exceeds max_count, raises ValueError with 'unsatisfiable' in
        message.
        """
        prop_iri = IRI("https://example.org/mergeTest#ownerProp4")
        nested_prop = IRI("https://example.org/mergeTest#nestedProp4")

        spec1 = PropertySpec(
            iri=nested_prop,
            value_kind=PropertyValueKind.OBJECT,
            python_range_type=None,
            min_count=3,
            max_count=None,
            some_from=None,
            all_from=None,
            nested=None,
        )

        spec2 = PropertySpec(
            iri=nested_prop,
            value_kind=PropertyValueKind.OBJECT,
            python_range_type=None,
            min_count=None,
            max_count=1,
            some_from=None,
            all_from=None,
            nested=None,
        )

        with pytest.raises(ValueError, match="unsatisfiable"):
            spec1.merge_conjunctive(spec2, owner_iri=prop_iri)

    def test_literal_beats_object_value_kind(self):
        """If either side is LITERAL, merged value_kind is LITERAL."""
        prop_iri = IRI("https://example.org/mergeTest#ownerProp5")
        nested_prop = IRI("https://example.org/mergeTest#nestedProp5")

        spec1 = PropertySpec(
            iri=nested_prop,
            value_kind=PropertyValueKind.LITERAL,
            python_range_type=str,
            min_count=None,
            max_count=None,
            some_from=None,
            all_from=str,
            nested=None,
        )

        spec2 = PropertySpec(
            iri=nested_prop,
            value_kind=PropertyValueKind.OBJECT,
            python_range_type=None,
            min_count=None,
            max_count=None,
            some_from=None,
            all_from=None,
            nested=None,
        )

        merged = spec1.merge_conjunctive(spec2, owner_iri=prop_iri)

        assert merged.value_kind is PropertyValueKind.LITERAL

    def test_mismatched_iri_raises(self):
        """Different iri on the two specs raises ValueError (programming error)."""
        prop_iri = IRI("https://example.org/mergeTest#ownerProp6")
        nested_prop1 = IRI("https://example.org/mergeTest#nestedProp6a")
        nested_prop2 = IRI("https://example.org/mergeTest#nestedProp6b")

        spec1 = PropertySpec(
            iri=nested_prop1,
            value_kind=PropertyValueKind.LITERAL,
            python_range_type=str,
            min_count=None,
            max_count=None,
            some_from=None,
            all_from=str,
            nested=None,
        )

        spec2 = PropertySpec(
            iri=nested_prop2,
            value_kind=PropertyValueKind.LITERAL,
            python_range_type=str,
            min_count=None,
            max_count=None,
            some_from=None,
            all_from=str,
            nested=None,
        )

        with pytest.raises(ValueError):
            spec1.merge_conjunctive(spec2, owner_iri=prop_iri)


    def test_datatype_and_class_target_raise(self):
        """Hand-built specs, one with python_range_type=str, one with all_from=IRI, raise
        ValueError matching 'both a datatype and a class'.
        """
        prop_iri = IRI("https://example.org/mergeTest#ownerPropDC")
        nested_prop = IRI("https://example.org/mergeTest#nestedPropDC")

        spec1 = PropertySpec(
            iri=nested_prop,
            value_kind=PropertyValueKind.LITERAL,
            python_range_type=str,
            min_count=None,
            max_count=None,
            some_from=None,
            all_from=str,
            nested=None,
        )

        spec2 = PropertySpec(
            iri=nested_prop,
            value_kind=PropertyValueKind.OBJECT,
            python_range_type=None,
            min_count=None,
            max_count=None,
            some_from=None,
            all_from=IRI("https://example.org/vk#SomeClass"),
            nested=None,
        )

        with pytest.raises(ValueError, match="both a datatype and a class"):
            spec1.merge_conjunctive(spec2, owner_iri=prop_iri)

    def test_cardinality_only_spec_does_not_trigger_the_target_conflict(self):
        """One spec with python_range_type=str, one with only max_count=2 and no target.
        Assert the merge succeeds and python_range_type is str.
        """
        prop_iri = IRI("https://example.org/mergeTest#ownerPropCO")
        nested_prop = IRI("https://example.org/mergeTest#nestedPropCO")

        spec1 = PropertySpec(
            iri=nested_prop,
            value_kind=PropertyValueKind.LITERAL,
            python_range_type=str,
            min_count=None,
            max_count=None,
            some_from=None,
            all_from=str,
            nested=None,
        )

        spec2 = PropertySpec(
            iri=nested_prop,
            value_kind=PropertyValueKind.OBJECT,
            python_range_type=None,
            min_count=None,
            max_count=2,
            some_from=None,
            all_from=None,
            nested=None,
        )

        merged = spec1.merge_conjunctive(spec2, owner_iri=prop_iri)

        assert merged.python_range_type is str
        assert merged.max_count == 2


class TestRestrictionTargetDecidesValueKind:
    """Tests that value_kind follows the restriction's target (datatype vs class), not the OWL keyword."""

    def test_class_valued_all_values_from_becomes_an_object_constraint(self, ogm: OGM):
        """An intersection range whose restriction has owl:allValuesFrom pointing at a class IRI
        yields value_kind OBJECT, all_from set to the class IRI, and python_range_type None.
        """
        NS_test = NS + "classAll_"
        prop_iri = IRI(NS_test + "hasClassTarget")
        nested_prop = IRI(NS_test + "nestedProp")
        class_target = IRI(NS_test + "SomeClass")

        b1 = BNode()
        triples = intersection_range(
            prop_iri,
            [(b1, restriction(b1, nested_prop, all_values_from=class_target))],
        )

        type_triples = [(prop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty"))]

        ogm.db.triples_add(triples, check_exist=False)
        ogm.db.triples_add(type_triples, check_exist=False)

        prop_spec = PropertySpec.specify(
            prop_iri=prop_iri,
            ogm=ogm,
            nested_scope=ClassScope(),
            hydration_level=True,
        )

        assert prop_spec.value_kind is PropertyValueKind.COMPLEX
        nested = prop_spec.nested.properties[nested_prop]
        assert nested.value_kind is PropertyValueKind.OBJECT
        assert nested.all_from == class_target
        assert nested.python_range_type is None

    def test_class_valued_some_values_from_becomes_an_object_constraint(self, ogm: OGM):
        """An intersection range whose restriction has owl:someValuesFrom pointing at a class IRI
        yields value_kind OBJECT, some_from set to the class IRI, and min_count 1 (ticket #11).
        """
        NS_test = NS + "classSome_"
        prop_iri = IRI(NS_test + "hasClassSomeTarget")
        nested_prop = IRI(NS_test + "nestedProp")
        class_target = IRI(NS_test + "AnotherClass")

        b1 = BNode()
        triples = intersection_range(
            prop_iri,
            [(b1, restriction(b1, nested_prop, some_values_from=class_target))],
        )

        type_triples = [(prop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty"))]

        ogm.db.triples_add(triples, check_exist=False)
        ogm.db.triples_add(type_triples, check_exist=False)

        prop_spec = PropertySpec.specify(
            prop_iri=prop_iri,
            ogm=ogm,
            nested_scope=ClassScope(),
            hydration_level=True,
        )

        assert prop_spec.value_kind is PropertyValueKind.COMPLEX
        nested = prop_spec.nested.properties[nested_prop]
        assert nested.value_kind is PropertyValueKind.OBJECT
        assert nested.some_from == class_target
        assert nested.min_count == 1

    def test_xsd_all_values_from_is_still_literal(self, ogm: OGM):
        """Regression guard: owl:allValuesFrom xsd:string still yields value_kind LITERAL and
        python_range_type str.
        """
        NS_test = NS + "xsdAll_"
        prop_iri = IRI(NS_test + "hasXSDTarget")
        nested_prop = IRI(NS_test + "nestedProp")

        b1 = BNode()
        triples = intersection_range(
            prop_iri,
            [(b1, restriction(b1, nested_prop, all_values_from=IRI("xsd:string")))],
        )

        type_triples = [(prop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty"))]

        ogm.db.triples_add(triples, check_exist=False)
        ogm.db.triples_add(type_triples, check_exist=False)

        prop_spec = PropertySpec.specify(
            prop_iri=prop_iri,
            ogm=ogm,
            nested_scope=ClassScope(),
            hydration_level=True,
        )

        assert prop_spec.value_kind is PropertyValueKind.COMPLEX
        nested = prop_spec.nested.properties[nested_prop]
        assert nested.value_kind is PropertyValueKind.LITERAL
        assert nested.python_range_type is str

    def test_cardinality_only_restriction_is_object_with_no_target(self, ogm: OGM):
        """A restriction carrying only owl:maxCardinality yields value_kind OBJECT with
        python_range_type, some_from and all_from all None.
        """
        NS_test = NS + "cardOnly_"
        prop_iri = IRI(NS_test + "hasCardOnly")
        nested_prop = IRI(NS_test + "nestedProp")

        b1 = BNode()
        triples = intersection_range(
            prop_iri,
            [(b1, restriction(b1, nested_prop, max_cardinality=2))],
        )

        type_triples = [(prop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty"))]

        ogm.db.triples_add(triples, check_exist=False)
        ogm.db.triples_add(type_triples, check_exist=False)

        prop_spec = PropertySpec.specify(
            prop_iri=prop_iri,
            ogm=ogm,
            nested_scope=ClassScope(),
            hydration_level=True,
        )

        assert prop_spec.value_kind is PropertyValueKind.COMPLEX
        nested = prop_spec.nested.properties[nested_prop]
        assert nested.value_kind is PropertyValueKind.OBJECT
        assert nested.python_range_type is None
        assert nested.some_from is None
        assert nested.all_from is None
        assert nested.max_count == 2


class TestDatatypeAndClassTargetConflict:
    """Tests that a datatype target and a class target on the same nested property raise at specify time."""

    def test_datatype_and_class_targets_on_the_same_property_raise(self, ogm: OGM):
        """A property whose intersection range restricts sharedProp to xsd:string, and a
        superproperty whose intersection range restricts the same sharedProp to a class IRI,
        raises ValueError whose message contains the nested property IRI, the owning property IRI,
        and both targets.
        """
        NS_test = NS + "datatypeClassConflict_"
        prop_iri = IRI(NS_test + "hasConflictingRange")
        superprop_iri = IRI(NS_test + "baseConflictingRange")
        shared_prop = IRI(NS_test + "sharedProp")
        class_target = IRI(NS_test + "ConflictingClass")

        b1 = BNode()
        triples_prop = intersection_range(
            prop_iri,
            [(b1, restriction(b1, shared_prop, all_values_from=IRI("xsd:string")))],
        )

        b2 = BNode()
        triples_super = intersection_range(
            superprop_iri,
            [(b2, restriction(b2, shared_prop, all_values_from=class_target))],
        )

        chain_triples = [
            (prop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty")),
            (prop_iri, IRI("rdfs:subPropertyOf"), superprop_iri),
            (superprop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty")),
        ]

        ogm.db.triples_add(triples_prop, check_exist=False)
        ogm.db.triples_add(triples_super, check_exist=False)
        ogm.db.triples_add(chain_triples, check_exist=False)

        with pytest.raises(ValueError) as excinfo:
            PropertySpec.specify(
                prop_iri=prop_iri,
                ogm=ogm,
                nested_scope=ClassScope(),
                hydration_level=True,
            )

        message = str(excinfo.value)
        assert str(shared_prop) in message
        assert str(prop_iri) in message
        assert str(class_target) in message
        # The datatype target is carried as the resolved python type, so it is rendered by
        # name rather than as a repr: "str", not "<class 'str'>".
        assert "str" in message
        assert "<class" not in message

    def test_two_different_class_targets_raise(self, ogm: OGM):
        """Both sides class-valued with different classes raises ValueError matching
        'incompatible allValuesFrom'.
        """
        NS_test = NS + "classClassConflict_"
        prop_iri = IRI(NS_test + "hasTwoClassRanges")
        superprop_iri = IRI(NS_test + "baseTwoClassRanges")
        shared_prop = IRI(NS_test + "sharedProp")
        class_a = IRI(NS_test + "ClassA")
        class_b = IRI(NS_test + "ClassB")

        b1 = BNode()
        triples_prop = intersection_range(
            prop_iri,
            [(b1, restriction(b1, shared_prop, all_values_from=class_a))],
        )

        b2 = BNode()
        triples_super = intersection_range(
            superprop_iri,
            [(b2, restriction(b2, shared_prop, all_values_from=class_b))],
        )

        chain_triples = [
            (prop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty")),
            (prop_iri, IRI("rdfs:subPropertyOf"), superprop_iri),
            (superprop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty")),
        ]

        ogm.db.triples_add(triples_prop, check_exist=False)
        ogm.db.triples_add(triples_super, check_exist=False)
        ogm.db.triples_add(chain_triples, check_exist=False)

        with pytest.raises(ValueError, match="incompatible allValuesFrom"):
            PropertySpec.specify(
                prop_iri=prop_iri,
                ogm=ogm,
                nested_scope=ClassScope(),
                hydration_level=True,
            )

    def test_cardinality_only_restriction_merges_with_a_typed_one(self, ogm: OGM):
        """Property restricts sharedProp to xsd:string; superproperty restricts the same property
        with owl:maxCardinality 2 only. Assert no raise, value_kind LITERAL, python_range_type str,
        max_count 2.
        """
        NS_test = NS + "cardMergesTyped_"
        prop_iri = IRI(NS_test + "hasCardPlusType")
        superprop_iri = IRI(NS_test + "baseCardPlusType")
        shared_prop = IRI(NS_test + "sharedProp")

        b1 = BNode()
        triples_prop = intersection_range(
            prop_iri,
            [(b1, restriction(b1, shared_prop, all_values_from=IRI("xsd:string")))],
        )

        b2 = BNode()
        triples_super = intersection_range(
            superprop_iri,
            [(b2, restriction(b2, shared_prop, max_cardinality=2))],
        )

        chain_triples = [
            (prop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty")),
            (prop_iri, IRI("rdfs:subPropertyOf"), superprop_iri),
            (superprop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty")),
        ]

        ogm.db.triples_add(triples_prop, check_exist=False)
        ogm.db.triples_add(triples_super, check_exist=False)
        ogm.db.triples_add(chain_triples, check_exist=False)

        prop_spec = PropertySpec.specify(
            prop_iri=prop_iri,
            ogm=ogm,
            nested_scope=ClassScope(),
            hydration_level=True,
        )

        nested = prop_spec.nested.properties[shared_prop]
        assert nested.value_kind is PropertyValueKind.LITERAL
        assert nested.python_range_type is str
        assert nested.max_count == 2


class TestObjectValuedConstraintValidation:
    """Tests that an IRI constraint in validate_some_all accepts IRI values and rejects non-references."""

    def test_iri_constraint_accepts_an_iri_value(self):
        """Build a PropertySpec with value_kind OBJECT, all_from=IRI, call to_pydantic_field(),
        and validate that a list containing an IRI passes without raising TypeError.
        """
        nested_prop = IRI("https://example.org/vk#nestedProp")
        class_iri = IRI("https://example.org/vk#SomeClass")

        spec = PropertySpec(
            iri=nested_prop,
            value_kind=PropertyValueKind.OBJECT,
            python_range_type=None,
            min_count=None,
            max_count=None,
            some_from=None,
            all_from=class_iri,
            nested=None,
        )

        field_type, field = spec.to_pydantic_field()

        TestModel = create_model("TestModel", test_field=(field_type, field))

        iri_value = IRI("https://example.org/instance#someInstance")
        instance = TestModel(test_field=[iri_value])
        assert instance.test_field == [iri_value]

    def test_iri_constraint_rejects_a_non_reference_value(self):
        """The same field rejects a value that is not a reference at all.

        A plain string is *not* rejected — `IRI` subclasses `str` and the field type would
        coerce it, so rejecting it here would contradict the type the guard is protecting.
        The check is "is this a reference", and an int is not one.
        """
        nested_prop = IRI("https://example.org/vk#nestedProp2")
        class_iri = IRI("https://example.org/vk#AnotherClass")

        spec = PropertySpec(
            iri=nested_prop,
            value_kind=PropertyValueKind.OBJECT,
            python_range_type=None,
            min_count=None,
            max_count=None,
            some_from=None,
            all_from=class_iri,
            nested=None,
        )

        field_type, field = spec.to_pydantic_field()

        TestModel = create_model("TestModel2", test_field=(field_type, field))

        with pytest.raises(ValidationError):
            TestModel(test_field=[42])


class TestUnmappableDatatypeTargets:
    """Tests that a datatype target is never mistaken for a class just because the map lacks it.

    XSDToPythonTypes covers 33 datatypes, so xsd:gMonth, xsd:gDay and xsd:dateTimeStamp are
    absent from it. Deciding datatype-ness by dict membership would turn those restrictions
    into object ones, giving the property an IRI field type and demanding references where
    the ontology asked for literals.
    """

    def test_unmapped_xsd_datatype_raises_rather_than_becoming_a_class(self, ogm: OGM):
        """owl:allValuesFrom xsd:gMonth is a datatype the map does not cover; it must raise,
        naming the nested property, the owning property and the datatype.
        """
        NS_test = NS + "unmappedXSD_"
        prop_iri = IRI(NS_test + "hasUnmappedTarget")
        nested_prop = IRI(NS_test + "nestedProp")
        g_month = IRI("http://www.w3.org/2001/XMLSchema#gMonth")

        b1 = BNode()
        triples = intersection_range(
            prop_iri, [(b1, restriction(b1, nested_prop, all_values_from=g_month))]
        )

        ogm.db.triples_add(triples, check_exist=False)
        ogm.db.triples_add(
            [(prop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty"))], check_exist=False
        )

        with pytest.raises(ValueError) as excinfo:
            PropertySpec.specify(
                prop_iri=prop_iri,
                ogm=ogm,
                nested_scope=ClassScope(),
                hydration_level=True,
            )

        message = str(excinfo.value)
        assert "cannot map to a python type" in message
        assert str(nested_prop) in message
        assert str(prop_iri) in message
        assert str(g_month) in message

    def test_rdfs_literal_target_is_treated_as_a_datatype(self, ogm: OGM):
        """rdfs:Literal is the top datatype, not a class, even though it is outside the XSD
        namespace. It must not silently become a class target.
        """
        NS_test = NS + "rdfsLiteral_"
        prop_iri = IRI(NS_test + "hasLiteralTarget")
        nested_prop = IRI(NS_test + "nestedProp")
        rdfs_literal = IRI("http://www.w3.org/2000/01/rdf-schema#Literal")

        b1 = BNode()
        triples = intersection_range(
            prop_iri, [(b1, restriction(b1, nested_prop, all_values_from=rdfs_literal))]
        )

        ogm.db.triples_add(triples, check_exist=False)
        ogm.db.triples_add(
            [(prop_iri, IRI("rdf:type"), IRI("owl:ObjectProperty"))], check_exist=False
        )

        with pytest.raises(ValueError, match="cannot map to a python type"):
            PropertySpec.specify(
                prop_iri=prop_iri,
                ogm=ogm,
                nested_scope=ClassScope(),
                hydration_level=True,
            )


class TestReferenceConstraintAcceptsPlainStrings:
    """Pins that the reference check agrees with the field type it guards.

    validate_some_all runs as a BeforeValidator, ahead of pydantic's coercion, so a plain
    string that the IRI field type would go on to accept has to pass the check too.
    """

    def test_plain_string_reference_is_accepted(self):
        """A str that the IRI field type would coerce must not be rejected by the guard."""
        spec = PropertySpec(
            iri=IRI("https://example.org/refcheck#nestedProp"),
            value_kind=PropertyValueKind.OBJECT,
            python_range_type=None,
            min_count=None,
            max_count=None,
            some_from=None,
            all_from=IRI("https://example.org/refcheck#SomeClass"),
            nested=None,
        )

        field_type, field = spec.to_pydantic_field()
        Model = create_model("RefCheckModel", test_field=(field_type, field))

        instance = Model(test_field=["https://example.org/instance#i1"])
        assert len(instance.test_field) == 1
