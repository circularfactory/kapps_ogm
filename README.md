# kapps-ogm

An object-graph mapper for RDF. It maps between instance data in a triplestore and pydantic
models, in both directions.

## What it does

The pydantic model's shape is **derived from the ontology**, not written by hand. You give it a
class IRI; it reads that class's declared properties out of the store — the `rdfs:range`
declarations and OWL restrictions attached to them — and builds the model from what it finds.

That is the whole point. The alternative is a pydantic model written in Python that mirrors an
ontology, and mirrors it correctly only until somebody edits one of the two. Nothing detects the
drift: the model keeps validating, against a shape the ontology no longer describes. Here there
is one source of truth, and it is the store.

## Installation

```bash
pip install kapps-ogm
```

Python 3.12 or later. You also need a reachable triple store — this library maps to and from
one, it does not embed one. The client is `kapps-triplestore-interface`, which installs as a
dependency; you do not need to install it yourself.

The mapper reads its classes from the ontology in the store and never loads an ontology itself. In
a productive circular factory the ontologies and shapes are already in the knowledge graph; the
ones shipped with the libraries seed demonstrations and tests.

## A round-trip example

Write an instance, read it back, and compare. Nothing here names a host: the connection comes
from the environment.

The example assumes the store already holds the ontology that declares its classes.

The OGM takes any store of the triple store interface's contract, `TripleStore`, and `connect()`
gives you one. It checks that the store answers, accepts the identity and has the dataset before
it returns. `TripleStoreEndpoint.from_env()` reads the store's URL and its dataset from
`TRIPLESTORE_URL` and `TRIPLESTORE_DATASET`, and `GDBTokenAuth.from_env()` the identity from
`TRIPLESTORE_USERNAME` and `TRIPLESTORE_PASSWORD`. It logs in for a token, because a store may
refuse Basic authentication; the triple store interface's documentation lists the other auth
providers.

```python
from kapps_triplestore_interface import IRI, GDBTokenAuth, TripleStoreEndpoint, connect

from kapps_ogm.ogm import OGM
from kapps_ogm.utils.class_scope import ClassScope

ONTOLOGY = "http://example.org/ontology#"

store = connect(TripleStoreEndpoint.from_env(), GDBTokenAuth.from_env())
ogm = OGM(db=store)

# How deep to go. An RDF graph has no natural boundary, so the caller states which
# paths to follow out from the root class; everything else is left in the store.
#
# Each chain runs from the root class down to a property you want, so a nested node
# needs its own properties named. `[[hasComponent]]` alone would put an empty scope
# under hasComponent, and a nested class with an empty scope is not hydrated at all --
# hasName and isActive would be properties the class does not declare, and create
# would refuse the data with an OGMDataError before writing anything.
class_scope = ClassScope.from_property_chains(
    [
        [IRI(f"{ONTOLOGY}hasComponent"), IRI(f"{ONTOLOGY}hasName")],
        [IRI(f"{ONTOLOGY}hasComponent"), IRI(f"{ONTOLOGY}isActive")],
    ]
)

data = {
    f"{ONTOLOGY}hasComponent": [
        {
            f"{ONTOLOGY}hasName": ["Component A"],
            f"{ONTOLOGY}isActive": [True],
        },
    ],
}

node = ogm.create(
    class_iri=IRI(f"{ONTOLOGY}Assembly"),
    class_scope=class_scope,
    data=data,
    persist=True,
)

fetched = ogm.fetch(
    instance_iri=node.id,
    class_scope=class_scope,
    materialize=True,
)

assert fetched.to_json_ld() == node.to_json_ld()
```

`create` can leave out `class_scope`: it then derives the scope from `data`, as `commit` does.
`fetch` has no data to derive one from, so it takes the scope.

`create` returns a `Node` — the mapped instance, carrying `.id` and `.data`. To get the pydantic
model itself, call `.materialize()`:

```python
model = fetched.materialize()   # a pydantic BaseModel, validated against the derived shape
print(model.model_dump())
```

## Changing several instances in one write

`commit` updates one instance. To change several instances together, give it `changes`: a list of
`Create` and `Update` entries, each for a different instance. They are written as one
`DELETE/INSERT`, so the store admits all of them or none of them. If the store refuses one
change, for example on a SHACL shape, the call raises the store's error and writes nothing.

Here a new inspection record is created, and an existing workpiece points at it, in one write:

```python
from kapps_ogm import Create, Update

inspection = IRI(f"{ONTOLOGY}inspection_7")

ogm.commit(
    changes=[
        Create(class_iri=IRI(f"{ONTOLOGY}Inspection"), instance_iri=inspection, data={}),
        Update(
            instance_iri=IRI(f"{ONTOLOGY}workpiece_1"),
            data={f"{ONTOLOGY}hasLastInspection": [{"id": str(inspection)}]},
        ),
    ]
)
```

An `Update` means what a commit of one instance means: each property in `data` replaces the stored
values of that property, and the other properties keep theirs. So an `Update` lists every value
that a property keeps, and a value it does not list is removed. To add a value to a property that
already has values, read the stored values and list them too. A value that another writer adds
between that read and the commit is then removed, and nothing reports it.

Each instance gets at most one change in a call. This includes a named instance that a change
nests with properties in its data, because the change writes about it too. Two changes that write
one instance would each apply their own diff to it, so the call raises `OGMUsageError` before it
writes. Types do not count, so a `Create` can link another new instance of the same call.

A `Create` refers to a new instance only, as `create` does: see below. Give a `Create` its
`instance_iri` when another change refers to it.

## Adding one value to a property

An `Append` adds one value to a property of a stored instance, and leaves the other values of
the property as they are. It does not read them and does not write them again. So two writers
that add a value to one property at the same moment both land, which an `Update` cannot promise.

```python
from kapps_ogm import Append

ogm.commit(
    changes=[
        Append(
            instance_iri=IRI(f"{ONTOLOGY}resource_1"),
            property_iri=IRI(f"{ONTOLOGY}hasCapability"),
            value=IRI(f"{ONTOLOGY}capability_7"),
        )
    ]
)
```

The value is given as in the data of a commit. For an object property it is an IRI or
`{"id": iri}`: a link, and the call writes nothing about the linked instance. To make that
instance in the same write, put a `Create` of it into the same call. For an anonymous node it is
a dictionary of the node's properties, without an `id`. Each such append makes a new node, so two
appends of the same content give two nodes.

An append is checked before the write as the value of a commit is: the class of the instance must
have the property, and the value must fit its range. If the property has a maximum count, the call
reads the stored values first and refuses an append that would go over the maximum, with
`OGMDataError`. Another writer can add a value between that read and the write; if the maximum
must hold under concurrent writers, state it as a SHACL shape. A property without a maximum is not
read.

A call whose changes are all appends sends one request: an `INSERT DATA` without a precondition.
A value the store holds already is not added again, and the call returns normally. In a call with
a `Create` or an `Update`, the appended triples go into the call's one `DELETE/INSERT`. Several
appends may name one instance, but an append and another change may not.

## What create and commit promise

A normal return of `create` or `commit` means that the store admitted the write: it found the
precondition of the write true, and it committed the change. If the precondition is false, the call
raises `PreconditionFailedError` from `kapps_triplestore_interface.exceptions`, and nothing of the
call is written. The error comes unchanged from the triple store interface. The store checks the
precondition in the same update request that makes the write.

**A new instance only.** `create`, and a `Create` in `commit`, write only new instances. The store
refuses the write if the target graph holds an explicit triple whose subject is an instance that the
call creates. The target graph is `named_graph` if you give one. An IRI whose triples are only in
another graph counts as new there.

**Links.** The data decides what a call creates. A nested value with properties is an instance of
its own, and the call creates it with its parent. A nested value given by its IRI alone, as
`{"id": iri}` with no other key or as an `IRI`, is a link. The call writes only the triple that
points at it, and nothing about the linked instance, even when the class scope hydrates its class.
So a link can point at an instance that exists. A SHACL shape decides whether the store admits it.

```python
# m2 is new. obj2 exists, and the call writes nothing about it.
ogm.create(
    class_iri=IRI(f"{ONTOLOGY}Measurement"),
    instance_iri=IRI(f"{ONTOLOGY}m2"),
    data={
        f"{ONTOLOGY}value": [1.5],
        f"{ONTOLOGY}measuredOn": [{"id": f"{ONTOLOGY}obj2"}],
    },
    class_scope=ClassScope.from_property_chains(
        [[IRI(f"{ONTOLOGY}value")], [IRI(f"{ONTOLOGY}measuredOn")]]
    ),
)
```

**What `commit` removes.** `commit` reads the stored instance first, inferred triples included
for its literal and object properties; an anonymous node is read from the explicit triples only.
The triples that the new data no longer holds are the triples to remove. A delete removes only
explicit triples, so the store checks each triple to remove against the explicit triples of the
target graph. The call raises if one is not there. Examples are a value that the store only
infers, a value that another writer changed after the call read it, and a `named_graph` that does
not hold the instance. If the data changes nothing, the call sends nothing, because there is
nothing to admit.

**Concurrent writers.** The store isolates commits at the level "read committed". Another write
can commit between the check of a write and its own commit. If an invariant must hold while
several writers change the store, state it as a SHACL shape. The store checks the shapes when it
commits each write.

**Appends.** A call whose changes are all appends has no precondition. A normal return means that
the store committed its `INSERT DATA`. A value that the store holds already stays as it was.

**Known limit.** A triple that is both explicit and inferred passes the check. After the commit it
is still visible, because a delete clears only the explicit triple, and the inferred one stays.

## When a call fails

The type of an error tells you what to do. The OGM's own errors have one base, `OGMError`, and
four categories. Each category is also the built-in that the OGM raised before, so an
`except ValueError` or `except TypeError` keeps working, and each message is the text it was.

| Error | What happened | What to do |
|---|---|---|
| `OGMDataError` | The data breaks the class: a required property is missing, a value has the wrong type. On `create` and `commit`: a property the class does not declare, or, as the subclass `PropertyOutsideScopeError`, one that the class declares and the class scope leaves out (its `scope` attribute lists what the scope names). On an `Append`: also a nested value with properties, an anonymous node, or a property that holds at most one value. | Refused: change the data. |
| `OGMOntologyError` | The ontology has a shape that the OGM cannot map: no type, no range, ranges that do not merge, a range that the OGM does not support. | Repair the ontology. |
| `OGMNotFoundError` | The instance or the node does not exist, or the OGM cannot resolve it. | Refresh: read the state again. |
| `OGMUsageError` | The API was called incorrectly. `OGMUsageTypeError` is the one that is also a `TypeError`. | Fix the call. |

A class scope that names a property the class does not have, or that continues a property chain
past a property with a literal range, raises `OGMDataError` too, also when you build the scope
yourself for `create` or `fetch`.

`create` and `commit` refuse data that names a property the class does not declare, at the top
level or in a nested value, before any triple is written. The `OGMDataError` names the instance
that carries the property in `instance_iri`, the property in `property_iri` and the class in
`class_iri`. `instance_iri` is the nested value's IRI when the property is in a nested value, and
`None` for an instance whose IRI is not minted yet, or when several nested values carry the
property. `property_iri` is `None` when several properties are unknown; the message lists them
all. Minimum counts stay unenforced on write. A read tolerates undeclared statements: `fetch` reads
the statements of an anonymous node in full, and materializing a node whose data holds more than
its class declares logs a warning and leaves the rest out, because a graph may carry more than any
schema declares.

All six types are importable from `kapps_ogm`. The attributes give the context: `instance_iri`,
`property_iri`, `class_iri` and `value` on `OGMDataError`, `class_iri` and `property_iri` on
`OGMOntologyError`, and `iri` on `OGMNotFoundError`. An attribute is `None` where it does not
apply.

The OGM does not change an error that it did not detect. An error of the triple store interface,
such as `PreconditionFailedError` or a SHACL refusal, reaches you as the same object, and so does a
`pydantic.ValidationError` from the generated model. That includes a count above the property's maximum, and a value that breaks an
`owl:someValuesFrom` or `owl:allValuesFrom` restriction: pydantic runs that check, so it reaches
you as a `pydantic.ValidationError`, not as an `OGMDataError`. A `RuntimeError` is an internal fault
of the OGM.

## What the ontology has to declare

The mapper reads shapes out of the store, so a class whose properties are undeclared maps to an
empty shape — it does not guess. Each property a model should carry needs an `rdfs:range`, an OWL
restriction, or both.

Two rules are worth knowing before you write the TBox:

- **Several `rdfs:range` assertions on one property are read conjunctively** — as their
  intersection, which is what RDFS entails. A value must satisfy all of them. Declaring two is
  not an error.
- **Ranges are inherited through `rdfs:subPropertyOf`.** A range declared on a superproperty
  reaches every subproperty, and the chain is walked transitively. This is not something a
  reasoner materialises for you; the mapper resolves it explicitly.

Where two declarations genuinely conflict — incompatible datatypes, or a datatype target against
a class target — that raises at specify time and names the property, rather than failing later
inside pydantic where the ontology cannot be named.

## Anonymous nodes

A nested node with no IRI of its own still needs an identity, or a write followed by a read
returns a different node than the one written. Such nodes are given a **Skolem IRI**, minted
under a `/.well-known/genid/` path as RDF 1.1 Concepts §3.5 provides for, so that a third party
reading the graph can tell the IRI stands in for a blank node.

The minting authority is an ontology-governance decision rather than this library's, so the
namespace is configurable per instance and the default is a documented placeholder:

```python
ogm = OGM(db=store, skolem_namespace="http://example.org/.well-known/genid/")
```

The `/.well-known/genid/` path itself is not configurable. A namespace without it would mint
addresses this library could no longer recognise as Skolem IRIs, which would silently disable the
guard rather than fail, so it is rejected at construction.

## Interfaces of a parameter node

A parameter property, such as a belt's speed, gives its anonymous node the domain shape: a value
and its unit. How a machine is reached, over MQTT or OPC UA, is not a fact of its class. So the
connection details come from an **interface**: a property whose own `rdfs:range` restricts them.
The mapper resolves the interfaces of each node per instance, and merges all of them:

- **Evidence.** A stored node that carries a property an interface's range restricts gets that
  interface's shape, with no declaration in the ontology.
- **A declaration.** `Interface(property_iri, interface_iri)` resolves the property as if it were
  `rdfs:subPropertyOf` the interface. A first write needs it, since the node carries nothing yet.
- **An `rdfs:subPropertyOf` assertion** in the ontology, for hardware whose protocol is fixed.

```python
from kapps_ogm import Interface, Update

# The first write: the declaration gives the node the MQTT shape, so its topic is accepted.
ogm.create(class_iri=BELT, data=data, interfaces=[Interface(HAS_SPEED, MQTT_PARAMETER)])

# A user-facing read: only the most general interface level, so no broker and no topic.
node = ogm.fetch(instance_iri=belt, class_scope=scope, merge_depth=1, materialize=True)

# The same depth on the way back leaves the connection details as they are.
ogm.commit(changes=[Update(instance_iri=belt, data=edited, merge_depth=1)])

# A protocol migration: the OPC UA shape alone, and the stale MQTT values are removed.
ogm.commit(changes=[Update(instance_iri=belt, data=migrated,
                           interfaces=[Interface(HAS_SPEED, OPCUA_PARAMETER, exclusive=True)])])
```

`merge_depth` counts the levels of the interface hierarchy from its most general property, which
is at level 1. This works as a projection when the ontology puts the content a peer may see on
the general interface and the connection details on the protocol interfaces below it. The depth
applies to every superproperty of the parameter property, so a parameter property that is itself
a subproperty of another domain property loses that property's range at a depth below its level.
A `ClassSpec` given to `fetch` is used as it is.

Evidence is matched to every property whose own range restricts a property the node carries and
its shape does not declare. An ontology in which a domain property restricts such a property too
gives that domain property's range to the node as well.

## Contributing

This repository is a **publish target**. It receives one commit per release, cut from a private
development repository, so a change proposed here has no shared history to merge into and
cannot be merged. Please open an issue instead.

# License

The package is licensed under the [MIT license](LICENSE).

# Acknowledgements

This package is developed as part of the INF subproject of the [CRC 1574: Circular Factory for the Perpetual Product](https://www.sfb1574.kit.edu/english/index.php). This work is therefore supported by the Deutsche Forschungsgemeinschaft (DFG, German Research Foundation) [grant-number: SFB-1574-471687386]
