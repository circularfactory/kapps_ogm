# kapps-ogm

An object-graph mapper for RDF. It maps between instance data in a triple store and pydantic models,
in both directions, and it derives the shape of each model from the ontology in the store.

> This file defines the **vocabulary**. How to use the mapper is in `README.md`, and what changes in
> each version is in `CHANGELOG.md`.

## Language

**Single-valued property**:
A property of which an individual holds at most one value. The constraint is given by the ontology
as `owl:FunctionalProperty` or an OWL restriction whose maximum is one, or by an `sh:maxCount 1`
shape.
_Avoid_: functional property (one of the declarations), scalar (a shape in code)

## How the mapper treats the terms

**A single-valued property** gives a list of at most one, as every property gives a list: `[value]`,
or `[]` without a value. So the shape of the data never depends on a cardinality declaration. A
second value is refused before the write, and the error names the maximum.

The mapper reads three of the declarations: `owl:FunctionalProperty`; an `rdfs:range` restriction
with `owl:cardinality 1` or `owl:maxCardinality 1`; and a restriction under `rdfs:subClassOf` of the
class or of one of its superclasses, with `owl:cardinality`, `owl:maxCardinality`,
`owl:qualifiedCardinality` or `owl:maxQualifiedCardinality`. A class restriction bounds the
property on that class and its subclasses only. Where several declarations bound a property, the
tightest wins. The mapper takes no minimum from them.

The mapper does not check the qualifier of a qualified cardinality (`owl:onClass`,
`owl:onDataRange`). The bound counts every value of the property, whatever its class or datatype,
and two IRIs count as two values. So two qualified restrictions on one property with different
qualifiers, each `owl:qualifiedCardinality 1`, give the property a maximum of 1, not 2; Core 0.9.0
has no such case.

The mapper does not read a SHACL shape. So it does not refuse a second value of a property that
only an `sh:maxCount 1` shape makes single-valued. Only the store's SHACL validation refuses that
value.

> _Changed 2026-10-11, decided by Etienne._ Before, this section said: "The mapper reads two of the
> declarations: `owl:FunctionalProperty`, and an `rdfs:range` restriction with `owl:cardinality 1`
> or `owl:maxCardinality 1`. It does not read a restriction under `rdfs:subClassOf`, an
> `owl:qualifiedCardinality`, or a SHACL shape." The case: Core 0.9.0 declares all of its bounds
> under `rdfs:subClassOf`, 19 of them as `owl:qualifiedCardinality 1`, so none of them reached the
> data model. A workpiece with two `isActive` values passed the mapper.
