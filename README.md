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

Python 3.12 or later. You also need a reachable GraphDB triplestore — this library maps to and
from one, it does not embed one. The client is `kapps-triplestore-interface`, which installs as a
dependency; you do not need to install it yourself.

## A round-trip example

Write an instance, read it back, and compare. Nothing here names a host: the connection comes
from the environment.

```python
import os

from kapps_triplestore_interface import IRI, GraphDB, GraphDBCredentials

from kapps_ogm.ogm import OGM
from kapps_ogm.utils.class_scope import ClassScope

ONTOLOGY = "https://example.org/ontology#"

credentials = GraphDBCredentials(
    base_url=os.environ["GRAPHDB_URL"],
    username=os.environ["GRAPHDB_USERNAME"],
    password=os.environ["GRAPHDB_PASSWORD"],
    repository=os.environ["GRAPHDB_REPOSITORY"],
)
ogm = OGM(db=GraphDB(credentials=credentials))

# How deep to go. An RDF graph has no natural boundary, so the caller states which
# paths to follow out from the root class; everything else is left in the store.
#
# Each chain runs from the root class down to a property you want, so a nested node
# needs its own properties named. `[[hasComponent]]` alone would put an empty scope
# under hasComponent, and a nested class with an empty scope is not hydrated at all --
# hasName and isActive would be unknown properties, dropped with a warning.
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

`create` returns a `Node` — the mapped instance, carrying `.id` and `.data`. To get the pydantic
model itself, call `.materialize()`:

```python
model = fetched.materialize()   # a pydantic BaseModel, validated against the derived shape
print(model.model_dump())
```

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
ogm = OGM(db=db, skolem_namespace="https://example.org/.well-known/genid/")
```

The `/.well-known/genid/` path itself is not configurable. A namespace without it would mint
addresses this library could no longer recognise as Skolem IRIs, which would silently disable the
guard rather than fail, so it is rejected at construction.

## Contributing

This repository is a **publish target**. It receives one commit per release, cut from a private
development repository, so a pull request opened here has no shared history to merge into and
cannot be merged. Please open an issue instead.

# License

The package is licensed under the [MIT license](LICENSE).

# Acknowledgements

This package is developed as part of the INF subproject of the [CRC 1574: Circular Factory for the Perpetual Product](https://www.sfb1574.kit.edu/english/index.php). This work is therefore supported by the Deutsche Forschungsgemeinschaft (DFG, German Research Foundation) [grant-number: SFB-1574-471687386]
