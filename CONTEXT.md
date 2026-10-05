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

The mapper reads two of the declarations: `owl:FunctionalProperty`, and an `rdfs:range` restriction
with `owl:cardinality 1` or `owl:maxCardinality 1`. It does not read a restriction under
`rdfs:subClassOf`, an `owl:qualifiedCardinality`, or a SHACL shape. So it does not refuse a second
value of a property that only an `sh:maxCount 1` shape makes single-valued. Only the store's SHACL
validation refuses that value.
