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

**Append**:
A change that adds one value to a property of a stored instance, and leaves the property's other
values as they are, without reading them. In code, the `Append` entry of `OGM.commit`.
_Avoid_: add (`create` adds instances), update (an `Update` replaces the values it names)

**Refusal on write**:
`create`, and a `Create` or an `Update` of `commit`, raise `OGMDataError` for a property the class
does not declare, before anything is written. A read stays tolerant: it ignores the statement and
warns. In code, the `refuse_unknown` keyword of `Node.materialize` and `NodeValidator.validate`,
separate from `strict`.
_Avoid_: strict (that is the minimum counts, which a write never enforces)

**Property outside the scope**:
A property that the class declares and the class scope of a write leaves out. `create`, and a
`Create` of `commit`, raise `PropertyOutsideScopeError` for it, a subclass of `OGMDataError`, before
anything is written; a write stays confined to its scope, and the error makes that loud. A property
that the class does not declare keeps the plain `OGMDataError` of the refusal on write. In code, the
`scope` attribute of the error lists the properties that the scope names for the class.
`Update` derives its scope from its data, so it cannot raise this.
_Avoid_: unknown property (that is one the class does not declare)

**Deletion**:
The mapper never deletes an individual. A property is retracted with `commit`, which removes the
triples that the new data no longer holds. The private `_delete` raises `NotImplementedError` and
is kept only as an entry point for a later extension. The public `delete` is a deprecated alias
of it for one release.
_Avoid_: delete (as a public method)

**Interface**:
A property whose own `rdfs:range` adds to the shape of a parameter node, for example the
connection details of one protocol. It contributes to one node, resolved per instance, from three
sources merged as a union: **evidence** (the stored node carries a property that the interface's
range restricts and the shape does not declare), a **declaration** (an `Interface` entry, which
resolves the parameter property as if it were `rdfs:subPropertyOf` the interface), and an
`rdfs:subPropertyOf` assertion in the ontology. In code, the `Interface` entry of `get_class_spec`,
`fetch`, `create`, `Create` and `Update`. An `Update` whose data leaves out a property that only
evidence put into the shape keeps that property's stored values. Only an exclusive declaration
removes them.
_Avoid_: protocol (one kind of interface), connector (what the middleware wires to it)

**Exclusive declaration**:
An `Interface` declared with `exclusive=True`. The declared interfaces are then the only ones of
its parameter property, and the other interfaces of the stored node are ignored. An `Update`
removes the stored values of the properties that only one of those other interfaces restricts, in
the same write; a property that no interface restricts stays. It is how a machine moves from one
protocol to another.
_Avoid_: override, precedence (two interfaces of a node are both merged, never ranked)

**Merge depth**:
How many levels of each interface hierarchy are merged into a parameter node's shape, counted from
the most general property down. A property without a superproperty is at level 1, and each other
property is one level below its deepest superproperty. The parameter property's own range is always
merged. Depth 1 is the user-facing view, which has no connection details when the ontology puts
them on the protocol interfaces; no depth (the default) is the wiring view. In code, the
`merge_depth` keyword of `get_class_spec`, `fetch` and `Update`.
_Avoid_: scope (that selects properties, not levels), level (one step of the hierarchy, not the count)

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
