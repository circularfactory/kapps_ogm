# API reference

Generated from the source by `sphinx.ext.autodoc`, one page per public module (17 modules).
Members without a docstring are listed too, with their signatures.

:::{note}
The package's top-level namespace re-exports the names most code imports: `OGM`, `Node`, `ClassSpec`, `PropertySpec`, `ClassScope`, …
Each is documented once, on the page of the module that defines it.
:::

```{toctree}
:caption: Core
:maxdepth: 1

ogm
```

```{toctree}
:caption: Mapping
:maxdepth: 1

mapping/anonymous_model
mapping/class_spec
mapping/property_spec
```

```{toctree}
:caption: Nodes
:maxdepth: 1

node/core
node/node_address
node/node_data_formatter
node/node_property_chains
node/node_serializer
node/node_validator
```

```{toctree}
:caption: Utilities
:maxdepth: 1

utils/class_scope
utils/constants
utils/errors
utils/json_ogm_encoder
utils/loader_strategy
utils/pretty_print
utils/skolem
```
