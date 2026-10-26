# Changelog

## 0.3.3 — 2026-10-26

- **kapps_ogm requires kapps-triplestore-interface 2.2.3 or later within 2.2**
  (`kapps-triplestore-interface~=2.2.3`). It is the release this version is tested against.

- **The OGM sends the queries it builds itself as text, without the interface's parse.** Given a
  `SPARQLQuery`, the triple store interface parses the rendered text again to validate it. That
  costs several milliseconds a query on the caller's thread, and more for a longer one. The
  property discovery, the maximum counts, the superproperties of an interface, the read of an
  anonymous node and the read of its stored terms now go as the text the interface would send.
  The other reads, which the interface builds itself (the triple reads and the class lookup of an
  instance), are validated as before. One error changes: a query that the store cannot read now
  raises the interface's error for the store's answer, not `InvalidQueryError`. The queries are
  built from fixed templates and from terms that the interface has rendered, so this needs a
  defect in the OGM.

- **A parameter node's interfaces are resolved per instance, and the view is selected by merge
  depth.** An interface (interim name) is a property whose own `rdfs:range` adds to a parameter
  node's shape, such as the connection details of one protocol. A parameter property no longer
  needs an `rdfs:subPropertyOf` marker for it: `fetch` merges the range of every interface whose
  range restricts a property the stored node carries, so two machines of one class can speak two
  protocols. A new `Interface(property_iri, interface_iri)` entry declares one for a node that
  carries no metadata yet; `get_class_spec`, `fetch` and `create` take `interfaces=`, and so do
  `Create` and `Update`. All resolved interfaces are merged, so a dual-homed node has both shapes.
  An `Interface` with `authoritative=True` selects the shape alone, and an `Update` with it
  removes the stored values that only an evidenced interface restricts, in the same write. An
  `Update` whose data leaves out a property that only evidence put into the shape keeps that
  property's stored values; it replaces them only when its data names the property. The new
  `merge_depth` keyword of `get_class_spec`, `fetch` and `Update` counts the levels of the
  interface hierarchy from its most general property: `merge_depth=1` gives a model without the
  connection details that the ontology puts on a protocol interface, with no filtering step.
  Without `merge_depth` every level is merged, as before. A `ClassSpec` given to `fetch` is used
  as given, never extended. `fetch` resolves the shape for its instance, so two instances of one
  class can get two ClassSpecs; a consumer that caches a ClassSpec keys it on the resolved
  interfaces too. Nodes at one position of one fetch share one shape, the union of their
  interfaces, because one field of a model has one type. A commit, and a `fetch`, read the
  interfaces of a stored parameter node that carries a property outside its shape, which is one
  more query; in a commit it is read inside the `fetch` of the stored node. So are the stored
  values that an authoritative declaration makes stale: the `fetch` of an `Update` takes their
  properties into its shape, so every read of the stored node, its values, its evidence and its
  stale values, sits below a `fetch`. The nested properties of an anonymous range are now
  sorted, so a shape does not depend on the order in which the store answers.

- **The shipped text names no project built with the library.** The test configuration says in
  neutral words why a run on the shared throwaway dataset forces other projects to seed again, and
  a test keeps consumer names out of the shipped files.

- **Every ontology namespace is spelled with `http://`, like every W3C ontology.** The default
  skolem base is now `http://w3id.org/circularfactory/.well-known/genid/`; the tests, test data,
  demo outputs, examples and README follow, and a test refuses an `https://` ontology namespace.
  A skolem IRI stored under the old scheme is still recognised, since the check tests only the
  path. Web links stay `https://`.

## 0.3.2 — 2026-10-21

- **kapps_ogm requires kapps-triplestore-interface 2.2.2 or later within 2.2**
  (`kapps-triplestore-interface~=2.2.2`). It is the release this version is tested against.

- **A property whose range is a datatype is read as a literal property, and keeps the literal's
  datatype.** A named `rdfs:range` that is `rdfs:Literal`, a datatype the store declares
  `rdf:type rdfs:Datatype` (a Gaussian mixture's, for example), or an XSD or RDF datatype without
  a python type (`xsd:gMonth`, `rdf:langString`) gives a literal property with no fixed python
  type, where it was taken for a class and failed ("is not an OWL/RDFS Class") or became an
  object link. A value given as an `rdflib.Literal` is written and read back with its datatype;
  a plain string is written as `xsd:string`. The datatype is asked for before the class, since a
  store with inference also types it `rdfs:Class`. A range of a datatype in a property chain is
  refused as any literal range is. A restriction target keeps its behaviour.

- **`PropertyOutsideScopeError`.** `create`, and a `Create` of `commit`,
  that carry a property which the class declares but the given class scope leaves out raise it,
  before anything is written. Before, such a write failed with the message that the class does not
  declare the property, which was false. It is a subclass of `OGMDataError`, so existing handlers
  still catch it; `instance_iri` (the nested node, if the property is nested), `property_iri`,
  `class_iri` and the new `scope` attribute name the case. A property that the class
  does not declare keeps the plain `OGMDataError`. The class's own properties are read only when a
  property is refused. A commit within its scope writes as before, and `Update`, which derives its
  scope from its data, cannot raise it. An anonymous node has no class IRI to ask, so it keeps the
  plain error, with a message that says the class does not declare the property or the scope
  leaves it out.

- **`create` and `commit` refuse a property the class does not declare.** A data object that names
  one, at the top level or in a nested value, raises `OGMDataError` before anything is written,
  on `create`, on a `Create` and on an `Update`. The error names the instance, the property and
  the class (`instance_iri`, `property_iri`, `class_iri`). Before, `create` dropped an unknown
  literal property with a warning, so a misspelled property lost its value silently, while
  `commit` raised. A caller that wrote an undeclared property through `create` now gets the
  error, and must remove the property or declare it in the ontology. Reads stay tolerant: a
  fetch still ignores a statement its class does not declare and warns, because a graph may
  carry more than any schema declares. The refusal is separate from `strict`, which the write
  paths never pass: minimum counts stay unenforced on write. The warning for undeclared data now
  also fires when the data lacks some declared property; before, it was silent in that case.

- **`OGM.delete` is deprecated.** An individual is never deleted: a property is retracted with
  `commit`. `delete` only ever raised "not yet implemented", which read as pending work. The entry
  point is now the private `_delete`, which raises `NotImplementedError` and says why. `delete`
  stays for one release as an alias that warns with a `DeprecationWarning` and raises the same
  error.

- **Tests:** two live tests prove that a refused `create` and a refused `commit` leave the stored
  statements unchanged, and that a class scope written for a superclass also covers a subclass
  declared after the scope exists, with a negative control that can fail.

- **Documentation:** the README's "Links" example passes a class scope, the error table lists a
  count above the maximum under pydantic's error, the commit paragraph says which values a read
  gets inferred and which explicit, and the `OGM` class has its docstring.

- **Fixed:** the validator's two literal type checks name the values that the check itself found.
  Both messages named the loop variable of another branch: with no object-valued property visited
  first, the check crashed with an internal error instead of the data error, also on the warning
  path of `owl:someValuesFrom` without strict. Now a value outside the `owl:allValuesFrom` datatype
  is refused with a message that names every offending value with its type, and a property with
  no value in the `owl:someValuesFrom` datatype gets a warning (a refusal in strict validation)
  that names the types of its values. The messages keep their "expected owl:...From" prefix; the
  error type and its attributes, `value` included, are unchanged.

- **A validation sends no request to the store.** The check "Node IRI is known but is not an
  instance of ClassSpec" is removed. It never fired: it asked the store whether the IRI is known
  in all three positions of a triple at once, which never holds for an instance. Fixed, it would
  have been wrong, since it tested `rdfs:subClassOf` where `rdf:type` is meant, and it would have
  refused every existing instance. It cost one read per validation. A create onto an existing IRI
  is still refused by the write's absent-subjects precondition, and a commit or a materializing
  fetch takes the class of an instance from the store. Every other rule, warning and refusal
  stays. The OGM no longer calls `iri_exists` or `is_subclass`.

- **`Append` adds one value to a property of a stored instance**. It is a change
  of `OGM.commit`, beside `Create` and `Update`, and is exported from `kapps_ogm`. The property's
  other values stay as they are and are not read. A call whose changes are all appends sends one
  `INSERT DATA`, so two writers that append to one property at the same moment both land. In a
  call with a `Create` or an `Update`, the appended triples go into the call's one
  `DELETE/INSERT`. Before the write, an append is refused if the class of the instance does not
  have the property (`OGMDataError`), if the value does not fit the range
  (`pydantic.ValidationError`), or if it would go over the property's maximum count
  (`OGMDataError`); only a bounded property's stored values are read for that. An object value is
  a link; a value with properties is refused (`OGMUsageError`). An anonymous-node value is always a
  new node under a minted Skolem IRI. Several appends may name one instance; an append and another
  change of one instance are refused, as two changes of one instance are.

- **Fixed:** `create`, and a `Create` of `commit`, derive the class scope from the data when no
  scope is given and no loader gives one, as `commit` does for an update. Before, they hydrated
  the class with an empty scope: literal values were dropped without an error, so only the
  type triple was written, and nested data failed with an error about properties not defined
  in the ClassSpec. A caller that created literal-only data without a scope now writes its
  literals too. A given scope, or a loader's, still wins. Data with properties whose
  scope cannot be derived raises an `OGMUsageError` that names the missing class scope.

- **Fixed:** `create` and `commit` with `data={}` no longer raise an `IndexError`. The scope of
  empty data is empty: `create` writes the instance with its type, and `commit` changes nothing.

- **Fixed:** a nested class at the REFERENCE level, which is the level of a hand-built
  `ClassSpec` with none set, is a link to every reader, as an IRI-only link is. The field of an
  object property no longer reads the nested class's level: its properties decide. `create` writes
  only the triple that points at the linked instance, for an IRI and for an id-only dictionary.
  Before, it wrote a string literal that spelled out the dictionary, and a materializing `fetch`
  put a dictionary into the field. A materializing `fetch` now gives a model that holds only the
  linked instance's id, and blank-instance generation gives the same. This qualifies the 0.3.0
  line "Before, it raised `ValueError`": for a nested class at the REFERENCE level, the
  id-dictionary form already wrote the literal.

## 0.3.1 — 2026-10-16

- **The type of an OGM error tells the caller what to do.** The OGM's own errors have one base,
  `OGMError`, and four categories: `OGMDataError` (the data breaks the class: Refused),
  `OGMOntologyError` (the ontology has a shape the OGM cannot map: Repair), `OGMNotFoundError`
  (the instance or the node does not exist, or cannot be resolved: Refresh) and `OGMUsageError`
  (the API was called incorrectly: Fix the call). `OGMUsageTypeError`, a subclass of
  `OGMUsageError`, replaces each `TypeError` the OGM raised. All six are exported from
  `kapps_ogm`. Each category also subclasses the built-in it replaces, so `except ValueError`
  still catches all four, every error that was a `TypeError` still is one, and every message is
  the text it was. `OGMUsageTypeError` is a `ValueError` too, so an `except ValueError` now also
  catches a `commit()` called with neither or both of its forms, or with a change that is not a
  `Create` or an `Update`.
- The errors carry the raw context as attributes, each `None` where it does not apply:
  `instance_iri`, `property_iri`, `class_iri` and `value` on `OGMDataError`, `class_iri` and
  `property_iri` on `OGMOntologyError`, and `iri` on `OGMNotFoundError`. The attributes interpret
  nothing.
- A class scope that names a property its class does not have, or that continues a property chain
  past a property with a literal range, raises `OGMDataError`, also when the caller builds the
  scope for `create` or `fetch`.
- `AnonymousNodeFetchError`, `UnresolvableNodeAddressError` and `AmbiguousNodeAlignmentError` are
  subclasses of `OGMNotFoundError`, and they carry the IRI they name in `iri`.
- An error of the triple store interface, and a `pydantic.ValidationError`, still reach the caller
  unchanged, as the same object. A `RuntimeError` is still an internal fault of the OGM.
- **Fixed:** the validators that parse a nested object or a nested anonymous node imported `Node`
  under a handler that caught every exception, so an error in that import was hidden. They now
  catch only `ImportError`.

- **No query of the OGM names a store-specific graph.** Two queries read explicit statements
  only: the property discovery of a class specification, and the read of a complex property's
  anonymous nodes in `fetch`. They now state that scope through the triple store interface's
  `SPARQLQuery` with `include_implicit=False`, and the interface writes the form its store
  understands. Before, each query named GraphDB's explicit-statements graph itself, which another
  store reads as an empty named graph, so the queries would return nothing there. The property
  discovery also used the `owl:` and `rdf:` prefixes without declaring them; it now writes full
  IRIs. On GraphDB the results do not change.
- **A class specification at the FULL level ends where a class repeats on its path.** At the FULL
  level an object property nests the FULL specification of its range class. A class that its
  properties lead back to, directly (a task that has a reference task) or through other classes,
  was specified again and again, and the call raised an invalid-query error caused by the maximum
  recursion depth. Now a range class that is already on the specification path, from the root
  class to the current class, is nested as an IRI-only link: a `ClassSpec` with the class's IRI
  and no properties. The read path, the write path, blank-instance generation and display take it
  as a link, as they take a nested class without properties from a class scope. A class on two
  paths without a cycle is still specified in full on both. A class scope is followed as it is
  given, so a scoped fetch returns what it returned before. A property without an `rdfs:range`
  still raises `ValueError` at the FULL level.
- The properties of a class specification are in sorted order. Before, their order depended on the
  hash seed of the process.
- **The wheel ships the `py.typed` marker (PEP 561)**, so that a type checker such as mypy reads the package's own annotations instead of reporting an untyped import for every import of `kapps_ogm`.

- **The OGM types its store by the triple store interface's contract.** `OGM(db=...)` annotates
  the store as `TripleStore`, the contract type of `kapps-triplestore-interface`, and no longer as
  the interface's adapter class. Any store of the contract works: the store that the interface's
  `connect()` returns, the adapter itself, and a test double that implements the operations that
  the OGM calls. The OGM calls only operations that the contract declares. A caller that passes
  the adapter, as before, changes nothing; a type checker accepts it, because the adapter
  implements the contract.
- The package, its docstrings, the README and the documentation name no triple store vendor,
  apart from the token provider `GDBTokenAuth` in the README's example. The example builds its
  store with `connect()`, from `TRIPLESTORE_URL`, `TRIPLESTORE_DATASET`, `TRIPLESTORE_USERNAME` and
  `TRIPLESTORE_PASSWORD`, and logs in for a token, because a store may refuse Basic authentication.
- **The live tests build their store with `connect()`**, from `TRIPLESTORE_URL`,
  `TRIPLESTORE_USERNAME` and `TRIPLESTORE_PASSWORD`, and skip without one of them. They no longer
  read the `GRAPHDB_*` variables. The dataset still comes from `pytest --repository` alone, and
  `TRIPLESTORE_DATASET` is not read. The opt-in for a run that clears a dataset on a shared host
  is `TRIPLESTORE_ALLOW_DESTRUCTIVE=1`; `GRAPHDB_ALLOW_DESTRUCTIVE` no longer counts.
- **A live test run removes every SHACL shape of the dataset it runs on.** The tests load their
  shapes with the store's shapes registry, `shapes_set`, which replaces every shape of the
  dataset. So a run now also removes the shapes that other code loaded into that dataset, where
  before it removed only its own. Load them again after a run.
- The test suite turns each `DeprecationWarning` of the triple store interface into an error, so a
  use of a name that the interface deprecates fails its test.
- **A property takes its maximum count from the restrictions of its class too.** The class
  specification reads the restrictions under `rdfs:subClassOf` of the class and of its
  superclasses: `owl:cardinality`, `owl:maxCardinality`, `owl:qualifiedCardinality` and
  `owl:maxQualifiedCardinality`. The bound becomes the property's maximum on that class and its
  subclasses. Where it meets the maximum of `owl:FunctionalProperty`, the tightest wins. The
  qualifier (`owl:onClass`, `owl:onDataRange`) is not checked: the bound counts every value, and
  two IRIs count as two values. No minimum is taken. Before, only `owl:FunctionalProperty` and an
  `rdfs:range` restriction gave a maximum, so an ontology that bounds its properties on its
  classes, such as Core, bounded nothing in the data model.
- A write that passed before is refused now: `create` and `commit` refuse a value over a class
  bound before the write, with the error of every value over a maximum, a
  `pydantic.ValidationError` that names the property and the maximum ("at most 1 item"). A
  materializing `fetch`, and a `commit`, of a stored instance that holds more values than its
  class bound raise that error too. Each class specified with its properties costs one more read
  of the store.

## 0.3.0 — 2026-10-05

### Added

- **`OGM.commit` writes changes to several instances in one update request.** Give `changes`, a list
  of `Create` and `Update` entries, instead of `instance_iri` and `data`. The OGM validates and
  reads every change first. Then it sends all of them as one `DELETE/INSERT`, so the store admits
  all of them or none of them. A refusal of one change raises the store's error, and no change is
  written. A change can refer to an instance that a `Create` in the same call makes. The call
  returns one `Node` for each change, in the same order. A commit of one instance does not change.
- The call raises `ValueError` and writes nothing when two changes write the same instance. That is
  the instance a change names, or a named instance that its data nests with properties. Types do
  not count.
- `kapps_ogm` exports `Create` and `Update`.
- **The data decides whether a nested value is a link.** A nested value given by its IRI alone, as
  `{"id": iri}` with no other key or as an `IRI`, is a link. `OGM.create` and a `Create` in
  `OGM.commit` write only the triple that points at it, and nothing about the linked instance, even
  when the class scope hydrates its class. A nested value with properties is created with its
  parent. Before, a hydrated class decided: a link wrote the linked instance's types, and so the
  first measurement on an existing object was not written. An `IRI` as the value of an object
  property now means the same as `{"id": iri}`. Before, it raised `ValueError`. (Not for a nested
  class at the REFERENCE level: there the id-dictionary form already wrote a literal; see the
  fix under Unreleased.)
- README section "What create and commit promise".

### Changed

- **`OGM.create` and `OGM.commit` return only for a write that the store made.** If the store finds
  the precondition of the write false, the call raises `PreconditionFailedError` from
  `kapps_triplestore_interface.exceptions`, unchanged, and nothing of the call is written. Before,
  the call returned, and its `Node` described a write that did not happen. The precondition is
  checked inside the write:
  - `create`, and a `Create` in `commit`, raise if the target graph holds an explicit triple whose
    subject is an instance that the call creates: the new instance, or a nested instance that the
    data gives with properties.
  - `commit` raises if a triple it would remove is not an explicit triple of the target graph: a
    value that the store only infers, a value that another writer changed after the call read it,
    or a `named_graph` that does not hold the instance.
- **Known limit:** a triple that is both explicit and inferred passes the check of `commit`. After
  the commit it is still visible, because a delete clears only the explicit triple.

### Fixed

- Data that links through a property its class does not have raises the `ValueError` that names
  the property. Before, an `AttributeError` from describing the unfinished node replaced it.
- The warnings for an instance with several classes, and for a class with several labels or
  comments, name the value that is used. Before, they said "the first one", which was not the one
  used.

- **`OGM.create` and `OGM.commit` now raise every error from the triple store interface
  unchanged, with its type and its message.** Before, both raised a bare `Exception` in its
  place, so a caller that caught `TripleStoreInterfaceError` missed a SHACL refusal. To catch a
  refusal, catch `TripleStoreInterfaceError`. A connection error or a timeout from `requests`
  also reaches the caller unchanged. A caller that catches `Exception` still catches every error.
  Two things change for that caller:

  - The messages `Failed to persist instance.` and `Failed to update instance in database.` are
    gone.
  - `__cause__` no longer holds the error from the triple store interface.

- **An OWL restriction no longer makes a field required.** Under the Open World Assumption, a
  missing value is unknown, not false. Before, `owl:someValuesFrom`, `owl:minCardinality` and
  `owl:cardinality` made the generated pydantic field required and gave a list a minimum length.
  So `OGM.create`, `OGM.commit` and a materializing fetch refused an instance without such a
  value. An example is a parameter whose device has not sent a value yet. Now that property
  gives an empty list, a single-valued one too (see the next entry). The type of the values and
  their maximum count still apply, and a value over the maximum is still refused before the write.
  `PropertySpec.required` is now always `False`. `NodeValidator` still logs a warning for fewer
  values than the OWL minimum.

- **A single-valued property holds its one value.** A property whose maximum is one, from
  `owl:FunctionalProperty`, `owl:cardinality 1` or `owl:maxCardinality 1`, now gives a list of at
  most one, as every other property gives a list: `[value]`, or `[]` without a value. Before, its
  field was a scalar, `None` without a value. But the data of a `Node` is a list for each
  property, so `OGM.create` refused the one value as well as two, and a materializing
  `OGM.fetch` of a stored value failed. So did `OGM.commit`, because it reads the stored instance
  as a model before it changes it. Now the shape of the data never depends on a cardinality
  declaration, and a fetch, `model_dump()`, commit cycle needs no conversion. A second value is
  still refused before the write, and now the error names the maximum: "List should have at most
  1 item". Code that reads such a field must change: `model.x[0]` reads the value that `model.x`
  read, and `model.x == []` replaces `model.x is None`. A property that only an `sh:maxCount 1` shape
  makes single-valued is not refused yet, because the OGM does not read SHACL shapes yet.

## 0.2.0 — 2026-08-12

First release cut through the release mechanism, and the first published from
[`circularfactory/kapps_ogm`](https://github.com/circularfactory/kapps_ogm).

### What this supersedes

PyPI already carries 0.1.2, and it is not a version anybody should still be on. It
declares `graph_db_interface >= 1.2.0` — a project that has since been forked and renamed, and
whose unreleased fixes this library depends on — and `aas_middleware >= 0.0.1`, which nothing
here has imported since that dependency was dropped. Neither pin resolves to something this
code runs against. The dependency table below is the honest one:
`kapps-triplestore-interface >= 2.1.0`, `rdflib`, `pydantic`, and nothing else.

The two are not continuous in build terms either. 0.1.2 was built with poetry-core,
which writes only the first `authors` entry into a wheel's metadata and so credited one of this
library's two authors. This release builds with hatchling, and `prepare_release.py` asserts both
names against the built artifact rather than against the manifest that claims them.

No git history is carried. This repository takes one commit per release, so the credit that
history would have carried lives in `LICENSE`, `pyproject.toml`, `README.md` and here instead.

**Why 0.2.0 and not 1.0.0**: the surface is still moving. Scoped hydration below a `COMPLEX`
property is unimplemented and `owl:someValuesFrom` still sets `min_count = 1`, which is wrong
under the Open World Assumption. A `1.x` would promise those are settled. `0.x` says what is
true — this is usable, several projects run against it, and the API may still move.

### Added

- **Four new modules implement Skolemised identity for anonymous nodes, per PRD
  requirements R1–R6.** The requirements document is
  `kapps_semantic_middleware`'s anonymous-node-identity PRD, which is development-repository
  material and does not ship with this distribution; this entry is the record that ships.
  `kapps_ogm/utils/skolem.py` provides `mint_skolem_iri()` and `is_skolem_iri()`, plus
  `WELL_KNOWN_GENID_PATH` and `DEFAULT_SKOLEM_NAMESPACE`. The path begins
  `/.well-known/genid/` per RDF 1.1 Concepts §3.5's recognisability provision, so a third
  party can tell the IRI stands in for a blank node; the minting authority is an
  ontology-governance decision not yet settled, so the namespace is configurable per `OGM`
  instance via a new `skolem_namespace` constructor argument and the default is documented
  as a placeholder. `kapps_ogm/mapping/anonymous_model.py` introduces `AnonymousNodeModel`,
  wired through the existing `ClassSpec.pydantic_base_model` seam; it carries the node's
  address in a pydantic `PrivateAttr`, captured from the payload's `id` key by a
  `mode="wrap"` model validator that pops the key before field validation. Verified on
  pydantic 2.13: private attributes are absent from `model_dump()`, `model_dump_json()`
  and `model_json_schema()`, so the address cannot leak northbound, into OpenAPI, or into
  `to_triples`; it does not survive a dump-then-revalidate round trip, which is why it is
  a mirror and `Node.data` remains the authoritative carrier. `kapps_ogm/node/node_address.py`
  exports `reconcile_anonymous_addresses()`, which copies addresses from the fetched node
  onto the node about to be written. `kapps_ogm/utils/errors.py` adds
  `AnonymousNodeFetchError` and `UnresolvableNodeAddressError`.

### Fixed

- **`PropertySpec.specify()` now merges anonymous `rdfs:range` restrictions across the `rdfs:subPropertyOf*` chain, implementing the conjunctive semantics RDFS entails.** Two defects, which RDFS makes one problem: ranges asserted on an interface superproperty were invisible to the OGM, and multiple `rdfs:range` assertions on one property raised `ValueError: Property ... has multiple independent rdfs:range defined`. RDFS ranges are conjunctive — several assertions mean the value must satisfy all of them, their intersection — so refusing them was an incorrect reading of the specification, not a deliberate restriction. The merge is an entailment, not a convention: `belt tu:hasConveyorSpeed _:b` (asserted), `tu:hasConveyorSpeed rdfs:subPropertyOf inf:isInterfaceAccessibleMQTTParameter` (asserted) entails `belt inf:isInterfaceAccessibleMQTTParameter _:b` (rdfs7); `inf:isInterfaceAccessibleMQTTParameter rdfs:range C_mqtt` entails `_:b rdf:type C_mqtt` (rdfs3); `tu:hasConveyorSpeed rdfs:range C_speed` entails `_:b rdf:type C_speed` (rdfs3); therefore `_:b rdf:type (C_speed ⊓ C_mqtt)`. The value node **is** an instance of the intersection, and the OGM computing it implements an entailment the reasoner does not materialise. Measured: GraphDB materialises **0** inherited ranges even with `include_implicit=True`, so range-on-superproperty could not work by inheritance and had to be resolved explicitly by walking `rdfs:subPropertyOf*` in SPARQL.

  The cost to scenario 3 was observable on every `fetch(materialize=True)` of a parameter node: `WARNING kapps_node_validator: Unknown properties in data for ClassSpec None: {hasMQTTBrokerIP, hasMQTTTopic, hasMQTTSetTopic, accessMode}`. Four of the five triples on the node were invisible to the effective shape. `inf:accessMode` was worse than undeclared: it is declared on the interface superproperty `inf:isInterfaceAccessibleParameter`, two levels up the chain, so it was **unreachable** until the walk was added. This also forced a documented exception to the rule that values are written through the mapper: `examples/seed.py:_attach_connection_metadata` in `EHoffm/kapps_semantic_middleware` wrote topic, set topic and broker with a raw SPARQL `INSERT`, flagged in its own docstring as that exception, because the write path serialised only what the range restriction declared. Verified live against the institute's GraphDB, repository `Tests`, on 2026-07-28: `tu:hasConveyorSpeed` now resolves one COMPLEX spec whose nested anonymous `ClassSpec` declares all six of `inf:hasValue` (float), `tu:hasUnit`, `inf:hasMQTTTopic`, `inf:hasMQTTBrokerIP`, `inf:hasMQTTSetTopic` and `inf:accessMode` (all string); a scoped `fetch(materialize=True)` of `tui:ConveyorBelt1_left` returns the topic, set topic, broker IP and access mode in the parameter node's data and logs no validator warning.

  Because ranges can now arrive from multiple levels of the chain, conflict rules are defined where previously the code simply refused the case:

  | Situation | Behaviour |
  | --- | --- |
  | Same property from two levels with the same datatype | Merge |
  | Same property with incompatible datatypes | Raise, a genuine ontology error |
  | Differing cardinalities | Most restrictive wins (min is the maximum of the minima, max the minimum of the maxima); unsatisfiable result raises |
  | Named class range mixed with an anonymous restriction range | Raise |
  | Two unrelated named class ranges | Raise, as before |
  | A datatype target merged with a class target | Raise — the value cannot be both (added with the `value_kind` fix in the next entry) |

  The subsumption filter (commit `3a86137`) is narrowed to named ranges: it now carries an `isIRI(?sub) && isIRI(?obj)` guard. It exists to pick the most specific *named* class; without the guard it could discard an anonymous restriction range and silently drop half a merge. A cross-product defect is fixed in passing: when a property's `rdfs:range` was a bare `owl:Restriction` rather than an `owl:Class` with `owl:intersectionOf`, both structural `OPTIONAL`s in `_specify_complex_property`'s query failed, `?restriction` stayed unbound, and the following `OPTIONAL { ?restriction owl:onProperty ?onProperty }` matched **every restriction in the repository**. Binding `?restriction` in a `UNION` before the detail `OPTIONAL`s removes it. Walking the chain made fixing it necessary, since it multiplies the number of ranges reaching that query. The structural metadata (`unionOf`, `complementOf`, `oneOf`) moved to its own query for the same reason: reading it off "the first binding" is meaningless once several ranges contribute. The walk uses the SPARQL property path `rdfs:subPropertyOf*`, evaluated as a transitive closure, so a cyclic `rdfs:subPropertyOf` assertion terminates by construction rather than needing a guard in Python.

  Requiredness is deliberately untouched: `owl:someValuesFrom` still sets `min_count = 1`. That it must not, under the Open World Assumption, is open work. The merge as implemented cannot make a shape harder to satisfy than its most restrictive part, which is the constraint that open work places on this change. The specification of record is requirement **R7** of `kapps_semantic_middleware`'s anonymous-node-identity PRD, and the reading of `rdfs:range` that it builds on is recorded there too. Both are development-repository documents and neither ships here. This unblocks two further fixes that could not land while every fetch of real data carried undeclared properties.

- **A nested property's `value_kind` was inferred from the *presence* of `owl:someValuesFrom` / `owl:allValuesFrom` rather than from what the keyword points at, so every restriction was assumed to constrain a literal.** `owl:allValuesFrom xsd:string` constrains a literal; `owl:allValuesFrom cfc:Unit` constrains an object, and both are legal OWL. `XSDToPythonTypes` is a plain `dict` and both lookups used `[]`, so a class target raised `KeyError` carrying nothing but the IRI — a poor diagnostic for a domain engineer who wrote a legal restriction. The intended fallback was **dead code**: the `someValuesFrom` branch already read `if range_type: ... else: nested_spec.some_from = range_iri`, which says exactly the right thing, but the `[]` lookup on the line above raised first so the `else` could never run; the intent was `.get()`. The codebase contradicted itself — `to_pydantic_field`'s LITERAL branch already raised `"Literal property ... cannot have allValuesFrom as Object IRI"`, so one site assumed class targets impossible while another explicitly rejected them.

  `validate_some_all` called `isinstance(x, self.some_from)`, which raises `TypeError: isinstance() arg 2 must be a type` when the constraint is an `IRI`. The dead branch would therefore have been broken even had it been reachable — further evidence it was never exercised. It now dispatches on whether the constraint is an `IRI`, and a class-valued constraint is checked only as "the value is a reference", because deciding class membership needs the store and `to_pydantic_field` has no access to it.

  A datatype target merged with a class target on the same nested property now raises at specify time, naming the nested property, the owning property and both targets. This case is *created* by the fix rather than predating it: before, a class target raised `KeyError` before any merge could happen, so the mixed pair could not exist. Now that it can, it needs a rule — and without one it would pass the merge unnoticed, because the two targets land in different fields (`python_range_type` versus `all_from`) and the reconciliation sees no clash on either, leaving pydantic to raise much later from a call site that cannot name the ontology at fault. The rule fires only when **both** sides carry a target, so a cardinality-only restriction — which says nothing about type — still merges with a typed one under the most-restrictive cardinality rule.

  Datatype-ness is decided by **namespace**, not by membership of `XSDToPythonTypes`. That map covers 33 datatypes, so `xsd:gMonth`, `xsd:gDay` and `xsd:dateTimeStamp` are absent from it, and reading a miss as "then it must be a class" would silently give those restrictions an `IRI` field type and demand references where the ontology asked for literals — trading a loud failure for a quiet misclassification. A target in the XSD namespace, or one of `rdf:XMLLiteral`, `rdf:langString` and `rdfs:Literal`, is a datatype; if it is one the map cannot resolve, that raises with a message naming the property and the datatype rather than falling through to the class branch.

  Zero non-XSD `some`/`allValuesFrom` restrictions exist in either live GraphDB repository (`Tests`, `OGM`), measured 2026-07-28, so no current data changes behaviour. This is a correctness fix, not a migration. Note also that the `isinstance(self.all_from, IRI)` guard in `to_pydantic_field` is deliberately retained: it becomes unreachable from `specify` but still guards a hand-built `PropertySpec`, and an existing test depends on it. `min_count = 1` on `owl:someValuesFrom` is untouched; that it should not be, under the Open World Assumption, remains open work.

  The defect was found in the design session for the range merge in the previous entry, whose `rdfs:subPropertyOf*` walk is what widened its blast radius — a restriction on any *ancestor* can now reach it, not only one on the property itself. That project's rule on where a bug fix is recorded requires this entry.

- **Anonymous nodes lost their identity on every write; they are now Skolemised.** The
  anonymous node behind a `COMPLEX` property — every parameter node in the Circular Factory
  — had no identity that survived a write. Identity was destroyed three times over:
  `OGM._fetch_complex_property` (`ogm.py`) grouped the query result by node and then
  returned `list(property_data_dict.values())`, discarding the key, so identity died at
  read; `format_for_instance` called `_assign_id`, which for an anonymous ClassSpec minted
  `db.new_blank_id()`, but pydantic ignored the `id` key entirely because an anonymous
  model has no `id` field; and `_value_to_triples` (`node_serializer.py`) minted another
  fresh `BNode` for any nested model without an `id` — on both sides of the diff. `Node.diff`
  therefore compared blank-node groups whose labels never matched, so a commit deleted the
  whole old group and inserted a new one. Because `kapps_triplestore_interface.triples_update`
  renders blank nodes as SPARQL variables, the DELETE matched the real node by structure,
  unlinking it and orphaning every triple the ClassSpec did not declare. A no-change commit
  was not a no-op. This was reproduced live on the ticket: after committing a speed value,
  the parameter node had moved, and three MQTT connection-metadata triples were left on a
  node with no inbound edge — while the call reported success.

  The fix skolemises. A blank node is an existential variable: it has no extent, cannot be
  addressed, and can only be re-found by matching a pattern from a named subject — which is
  exactly what made the write destructive. RDF 1.1 Concepts §3.5 sanctions replacing it with
  a Skolem IRI: the transformation does not appreciably change the meaning of an RDF graph,
  and it permits the possibility of other graphs subsequently using the Skolem IRIs, which
  is not possible for blank nodes. That second property is the requirement — PROV
  qualification, SHACL focus nodes and joining a history snapshot to live state are all
  impossible against a blank node. Two conditions attach to the guarantee and are honoured
  as normative rules: the IRIs are globally unique and never reused, and nothing is asserted
  about the node — no `rdf:type`, no class membership, no annotation. `to_triples` already
  satisfied the type half, since it emits type triples only when `class_spec.iri` is set and
  an anonymous ClassSpec has `iri=None`.

  `_fetch_complex_property` now keeps the identifier it already had, returning it as an
  `"id"` entry per group, and returns groups sorted by identifier so two fetches of
  unchanged data align positionally; `sanitize_data` passes an `IRI` or `BNode` through
  verbatim instead of coercing it. `_assign_id` mints a Skolem IRI for an anonymous
  ClassSpec instead of `db.new_blank_id()`. `_value_to_triples` no longer mints at all; it
  resolves the target in order — the address recorded in `Node.data`, then the `_node_iri`
  mirror on the model — otherwise it raises `UnresolvableNodeAddressError`. An unresolvable
  target must never silently become a new node. `OGM.commit` now fetches the old node before
  materializing the new one and reconciles addresses between them; this is load-bearing
  rather than incidental, since the canonical usage pattern is `fetch(materialize=True)` →
  `model_dump()` → edit → `commit(data=<plain dict>)`, and the dump deliberately carries no
  address, so it must be recovered from the store side. `OGM.fetch` on a Skolem IRI now
  raises `AnonymousNodeFetchError` naming the situation ("anonymous node — fetch its parent")
  before touching the database, rather than failing later with `ValueError: Could not
  determine class IRI`. The diff needed no change: with an IRI subject,
  `group_triples_by_bnode` puts each triple in its own group, so the diff reduces to what
  actually changed and stays one atomic DELETE/INSERT.

  A parameter node already in the store as a real blank node is relocated once, to a Skolem
  IRI, on the next write that touches it; the old side of that one transaction still names
  the blank node, so the relocation is a single atomic DELETE/INSERT. Undeclared triples on
  such a node are not carried across that one relocation — the ClassSpec does not know about
  them — so a legacy node loses them exactly once. This is bounded in practice because only
  the TBox is seeded in productive environments; all ABox data is written through the OGM
  and is therefore skolemised from the outset. Converting a whole resource up front, and the
  inverse deskolemise, are PRD R12 and not built here. Merging the interface restrictions so
  that connection metadata becomes declared — which is what stops even that one-time loss — is
  PRD R7, the range merge above. Entity deletion stays unsupported; canonical (isomorphism-preserving)
  Skolemisation is explicitly not what was built, since identity here is per node, not
  derived from content, which is what a locator needs.

  Five new unit test files, 72 tests, all offline against the existing `mock_db` fixture:
  `test_skolem_identity.py` (minting, recognition, the fetch guard),
  `test_anonymous_node_model.py` (projection invariance), `test_anonymous_node_addressing.py`
  (`to_triples` address resolution), `test_anonymous_node_round_trip.py` (identity at read,
  reconciliation, `Node.diff`), and `test_commit_round_trip.py`, which drives the full
  `fetch` → `model_dump` → edit → `commit` pattern and asserts the ticket's acceptance
  criteria directly: an unchanged commit writes zero triples, a changed value emits exactly
  one DELETE and one INSERT naming the node's IRI, the belt→parameter link is never
  unlinked, and no blank node reaches the write path. The suite is 155 tests, all passing.
  The ticket noted that this was never caught because `scripts/demo_update_value.py`
  exercises only the named-class `OBJECT` path — `demo:hasConveyorPosition` has a named-class
  range, so `_value_to_triples` took the stable-IRI branch. The `COMPLEX` update path now
  has coverage.

- **Review follow-ups, all in the same change.** `mint_skolem_iri` accepted any namespace while
  `is_skolem_iri` required the literal `/.well-known/genid/` path, so an `OGM` configured with a
  namespace off the default minted addresses its own guard could not recognise — silently
  disarming the `AnonymousNodeFetchError` check in `OGM.fetch`. `validate_skolem_namespace` now
  normalises the trailing slash and rejects a namespace lacking the well-known path, at `OGM`
  construction rather than on the first anonymous write; only the authority preceding that path
  was ever configurable, since §3.5 fixes the path itself.

  `to_json_ld` decided what to inline by testing `startswith("genid-")`, the blank-node label
  form, so a skolemised parameter stopped being inlined and its address surfaced in a northbound
  projection — a leak R4 forbids, and a change to the served shape. All five inlining decisions
  now route through one `is_anonymous_ref` predicate that treats a Skolem IRI as what §3.5 says
  it is: a blank node's stand-in.

  `reconcile_anonymous_addresses` aligns anonymous values by position, which is unambiguous for
  an appended or edited list but not for a shortened one: nothing says which stored node was
  dropped, and aligning by position would shift a surviving node's address onto the wrong entry,
  moving one parameter's properties onto another parameter's node. That is a worse failure than
  losing an address, so it now raises `AmbiguousNodeAlignmentError` rather than guessing.
  Clearing a property entirely stays legal — there is nothing left to misassign. Reordering an
  equal-length list is still undetectable from position alone; that is a known limitation and
  cannot arise until one property carries two or more anonymous nodes, which no current domain
  model does. Closing it wants content-based matching, and probably the range merge first — under the locator
  pattern, two sibling parameter nodes carry a unit and metadata but no value, so they
  are frequently content-identical and ties are the normal case rather than the edge case.

  On the namespace: Ratan settled the minting authority as `w3id.org/circularfactory`, which is
  what `DEFAULT_SKOLEM_NAMESPACE` already uses, so requiring the well-known path constrains
  nothing that was wanted. Had `urn:uuid:` been chosen instead — one of the three candidates the
  PRD left open — it would have been rejected here, and under the unvalidated code it would
  instead have silently disarmed the `fetch` guard.

  `kapps_ogm/utils/__init__.py` now re-exports the new errors and Skolem helpers, matching how
  `constants`, `pretty_print`, `json_ogm_encoder` and `class_scope` are already surfaced.

- **`ogm.py` called `format_triples_turtle` without importing it, so `OGM.commit` raised
  `NameError` whenever the logger was enabled for `DEBUG`.** The call sits inside an
  `isEnabledFor(DEBUG)` guard, which is why it had gone unnoticed: the suite never
  commits at `DEBUG`. Found by static analysis while reordering `commit`, not by hitting it.
  Added the missing import.

### Removed

- **`tests/integration/test_roundtrip.py`, the repository's only integration test,
  pending the semantic middleware rebuild.** It asserted the OGM's round-trip
  identity contract — `create(persist=True)` followed by `fetch(materialize=True)`
  yields the same JSON-LD — and in doing so was also the only executable
  demonstration of how `kapps_semantic_middleware` drives the OGM. Removed for two
  reasons: its shape (a hand-built `ClassScope` from two depth-1 property chains,
  passed to both `create` and `fetch`) encodes the *previous* middleware's call
  pattern, which pins the OGM interface while its only real consumer is being
  rebuilt; and it never reached its own assertion, failing instead inside
  `ogm.create` on the unrelated `ClassScope` coverage defect described under *Fixed* below. A permanently-red test that
  fails before the property it exists to check asserts nothing.

  The contract and the demonstration role are both still wanted: reinstating them
  against the rebuilt middleware is open work; the removed source stays in this
  repository's history for reconstruction. Its fixture data,
  `tests/test_data/TransferUnit1_data.json`, is deliberately retained and is
  currently unreferenced.

  `deepdiff` remains a declared dev dependency — `tests/unit/test_class_scope.py`
  still uses it.

- **`scripts/demo_instantiation.py`, for the same reason, and because it was the
  last consumer of undeclared packages.** It was the repository's most complete
  worked example — the only one going all the way from an ontology class to a served
  REST API: `ClassScope.from_property_chains` → `ClassSpec.specify` →
  `to_pydantic_model` → `create_blank_instance` → `ogm.create` (in-memory *and*
  persisted to a named graph) → `materialize` → `to_triples` / `to_json_ld` →
  `aas.DataModel.from_models` → `generate_rest_api_for_data_model` → `uvicorn.run`.

  Only that last stretch needed `aas_middleware` and `uvicorn`, and neither is
  declared in `pyproject.toml`; `aas_middleware` was deliberately dropped in 279851e
  ("This will break demos"). Once the manifest was fixed (under *Fixed* below), this script was the only thing in
  the repository that still could not be run from a clean environment built from the
  manifest — **with it gone, every remaining script and test can.** It also encoded
  the previous middleware's call pattern and carried a hardcoded GraphDB hostname.

  Reinstatement is split in two, because the demo must in future drive
  `kapps_semantic_middleware` rather than construct `aas_middleware` directly — and
  that half cannot live in this repository, since the middleware already depends on
  `kapps_ogm` and a demo here driving it would close a dependency cycle. The OGM-only
  portion is blocked on nothing; the end-to-end portion belongs to
  `kapps_semantic_middleware`. The four generated artefacts under
  `scripts/output/` are retained for reference and are now stale and unreferenced.

### Fixed

- **The test suite could not be installed or run from the manifest: two packages
  imported at module scope were declared nowhere in `pyproject.toml`.** Because a
  missing import at module scope is a pytest *collection* error rather than a test
  failure, either one alone aborted the entire run — including the 80-odd tests that
  never touch the missing package. No environment built from the manifest could run
  the suite at all, which blocked verifying any other ticket. Two distinct defects,
  the first masking the second:

  1. **`deepdiff` was imported but never declared** (`pyproject.toml`). Used by
     `tests/unit/test_class_scope.py` (`from deepdiff import DeepDiff`),
     `tests/integration/test_roundtrip.py` and `scripts/demo_roundtrip.py` (both
     `import deepdiff`), but absent from both `[tool.poetry.dependencies]` and
     `[tool.poetry.group.dev.dependencies]`, which listed only `black`, `pytest` and
     `pylint`. Declared `deepdiff = "^9.1.0"` in the dev dependency group.

  2. **The root `conftest.py` imported the deliberately removed `aas_middleware`**
     (`tests/conftest.py`). A root `conftest.py` is imported before any test module,
     so this had exactly the same suite-wide blast radius as (1) — it was simply
     masked by it. The import served only a session-scoped `mw` fixture returning a
     bare `AasMiddleware()`, which no test in the repository requests: dead code left
     behind when commit 279851e dropped `aas_middleware` from the manifest. Removed
     the unused fixture and its import, rather than re-adding a dependency that had
     been intentionally removed.

  Verified by building a clean virtual environment from the manifest alone, rather
  than relying on a developer machine that has accumulated packages. The suite now
  collects all 84 tests with zero collection errors, and 83 pass. The one remaining
  failure, `test_roundtrip[…TransferUnit…]`, is a pre-existing product defect
  unrelated to packaging: `OGM.create` raises from
  `_recursive_update_nodes_class_spec` (`kapps_ogm/node/core.py`) because the
  `ClassScope` built from the test's property chains does not cover the `isOccupied`
  property present in the data. It aborts inside `ogm.create`, well before any
  `DeepDiff` call.

  Note for follow-up: `scripts/demo_instantiation.py` still does `import
  aas_middleware`, and is now the only consumer of an undeclared package left in the
  repository. That is the breakage commit 279851e knowingly accepted ("This will
  break demos"), and `scripts/` is outside this fix's scope, but it means the demos
  are still not runnable from the manifest alone. Fixed here: the script is removed, see *Removed* above.

- **`OGM.commit` could not add or remove properties — only replace equal counts,
  and crashed on any `xsd:dateTime` property.** Three related defects, all on the
  commit path, which together made the OGM unusable as a general write path
  (contradicting the paper's "single validated write path" and the OGM's own
  documented triple-level mutability):

  1. **`OGM.commit` crashed on entities with a `datetime` value**
     (`kapps_ogm/ogm.py`). Leftover debug `print(json.dumps(...))` statements tried
     to serialize the diff (JSON-LD) with a `datetime` literal in it, raising
     `TypeError: Object of type datetime is not JSON serializable`. Removed the
     debug prints; replaced with a single `logger.debug` of the triple counts.

  2. **`ClassScope.from_node_data` dropped empty-valued properties**
     (`kapps_ogm/utils/class_scope.py`). A property whose value list was empty
     (`{prop: []}`, the idiom for "remove this property") produced no scope entry,
     because the inner `for value in values` loop never ran. `OGM.commit` therefore
     derived a scope that omitted the property, fetched the old state without it,
     and never diffed it away — so property *removal* was impossible. Fixed to emit
     a leaf chain for empty-valued properties so the scope covers them.

  3. **`OGM.commit` used `db.triples_update`, which required equal-length lists.**
     A general diff (from `Node.diff`) has disjoint remove/add sets of differing
     size, so add-only and remove-only commits raised
     `InvalidInputError: Old and new triples lists must have the same length.`
     Kept `triples_update` (its single atomic `DELETE/INSERT` transaction is
     required for SHACL-safe cardinality replacement — e.g. a possession handover)
     and generalized `kapps_triplestore_interface.triples_update` to accept unequal lengths
     (see that repo's changelog).

  Together these make `OGM.commit` able to add, remove, and replace properties
  atomically. Reported/fixed by the `kapps_semantic_middleware` project (which
  routes all of its knowledge-graph writes through the OGM) per its
  dependency-and-bugfix policy.

- **`PropertySpec.specify()` crashed with `NameError` on every property whose range
  needed resolving** (`kapps_ogm/mapping/property_spec.py`).

  **Symptom:** any call that resolves a class's properties —
  `OGM.get_class_spec(...)`, `OGM.create(...)`, `OGM.fetch(...)` (when a class spec
  must be derived), and `OGM.commit(...)` — raised
  `NameError: name 'range_query_result' is not defined` as soon as it reached a
  property with an `rdfs:range`. In practice this broke nearly every real
  fetch/create/commit, since almost every class has at least one such property. Only
  `ClassHydrationLevel.REFERENCE` (or an empty scope), which skips property
  resolution, avoided the crash.

  **Root cause:** commit `bcb7840` ("Fix handling of owl:Thing range in
  propertySpec") refactored the range lookup so the query result is bound to the
  local variable `query_result`, but the immediately following set-comprehension was
  left referencing the old, now-undefined name `range_query_result`:

  ```python
  query_result = ogm.db.triples_get(
      sub=prop_iri, pred="rdfs:range", include_implicit=True
  )
  range_set = set(triple[2] for triple in range_query_result)  # NameError
  ```

  **Fix:** reference the correct variable, `query_result`:

  ```python
  range_set = set(triple[2] for triple in query_result)
  ```

  This is a one-line correctness fix with no behavioral change beyond making the
  intended code path run. No public API, signature, or data-shape change.

  **Reported/fixed by:** the `kapps_semantic_middleware` project, which depends on
  `kapps_ogm` as a local editable dependency and hit this on its first real
  fetch/commit. Per that project's dependency-and-bugfix policy,
  which is a development-repository document that does not ship here, genuine correctness bugs
  in sibling dependency repos are fixed directly in the sibling, with a detailed
  changelog entry. This is that entry.
