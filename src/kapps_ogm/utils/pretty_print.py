from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from kapps_ogm.mapping.property_spec import PropertySpec
    from kapps_ogm.mapping.class_spec import ClassSpec


def format_triples_turtle(triples) -> str:
    """Format a set of RDF triples in a Turtle-like structure.

    Args:
        triples: Set or list of (subject, predicate, object) tuples.

    Returns:
        str: Formatted triples in Turtle-like syntax.
    """
    from collections import defaultdict

    # Group triples by subject
    grouped = defaultdict(list)
    for s, p, o in triples:
        grouped[s].append((p, o))

    output = []
    for subject in sorted(grouped.keys(), key=str):
        # Format subject
        subj_str = str(subject)
        output.append(f"\n{subj_str}")

        # Format predicates and objects
        predicates = grouped[subject]
        for i, (pred, obj) in enumerate(predicates):
            pred_str = str(pred)
            obj_str = str(obj)
            if i == len(predicates) - 1:
                output.append(f"    {pred_str} {obj_str} .")
            else:
                output.append(f"    {pred_str} {obj_str} ;")

    return "\n".join(output)


def format_node_data(node_data: Dict[Any, List[Any]]) -> Dict[str, Any]:
    """Convert Node.data with IRI keys and Node values to JSON-serializable format.

    Recursively converts IRI and BNode objects to strings, enabling JSON serialization.

    Args:
        node_data: Node.data dictionary with IRI keys and mixed values.

    Returns:
        Dict with all IRI/BNode objects converted to strings.
    """
    from kapps_triplestore_interface import IRI
    from rdflib import BNode

    def convert_to_serializable(obj):
        """Convert IRI and other non-serializable objects to strings."""
        if isinstance(obj, (IRI, BNode)):
            return str(obj)
        elif isinstance(obj, dict):
            return {
                convert_to_serializable(k): convert_to_serializable(v)
                for k, v in obj.items()
            }
        elif isinstance(obj, list):
            return [convert_to_serializable(item) for item in obj]
        return obj

    return convert_to_serializable(node_data)


def format_property_spec(prop_spec: "PropertySpec", indent: int = 0) -> str:
    """
    Recursively converts a PropertySpec (with nested ClassSpec) into a formatted string.

    Args:
        prop_spec: PropertySpec instance to format.
        indent: Indentation level for nested structures.

    Returns:
        str: Formatted PropertySpec representation.
    """
    ind = "  " * indent
    lines = [
        f"{ind}PropertySpec(",
        f"{ind}  iri={prop_spec.iri}",
        f"{ind}  value_kind={prop_spec.value_kind}",
        f"{ind}  python_range_type={prop_spec.python_range_type}",
        f"{ind}  required={prop_spec.required}",
        f"{ind}  min_count={prop_spec.min_count}",
        f"{ind}  max_count={prop_spec.max_count}",
    ]

    if prop_spec.nested:
        nested = prop_spec.nested
        lines.append(f"{ind}  nested=ClassSpec(")
        lines.append(f"{ind}    iri={nested.iri}")
        if hasattr(nested, "label") and nested.label:
            lines.append(f"{ind}    label={nested.label}")
        lines.append(f"{ind}    properties={{")
        for k, v in nested.properties.items():
            lines.append(f"{ind}      {k}:")
            lines.append(format_property_spec(v, indent=indent + 3))
        lines.append(f"{ind}    }}")
        if hasattr(nested, "metadata") and nested.metadata:
            lines.append(f"{ind}    metadata={nested.metadata}")
        lines.append(f"{ind}  )")
    else:
        lines.append(f"{ind}  nested=None")

    lines.append(f"{ind})")
    return "\n".join(lines)


def format_class_spec(class_spec: "ClassSpec", indent: int = 0) -> str:
    """
    Recursively converts a ClassSpec (with nested PropertySpec objects) into a formatted string.

    Args:
        class_spec: ClassSpec instance to format.
        indent: Indentation level for nested structures.

    Returns:
        str: Formatted ClassSpec representation.
    """
    ind = "  " * indent
    lines = [
        f"{ind}ClassSpec(",
        f"{ind}  iri={class_spec.iri}",
    ]

    if hasattr(class_spec, "label") and class_spec.label:
        lines.append(f"{ind}  label={class_spec.label}")

    if hasattr(class_spec, "types") and class_spec.types:
        lines.append(f"{ind}  types={class_spec.types}")

    if hasattr(class_spec, "superclasses") and class_spec.superclasses:
        lines.append(f"{ind}  superclasses={class_spec.superclasses}")

    lines.append(f"{ind}  properties={{")
    for prop_iri, prop_spec in class_spec.properties.items():
        lines.append(f"{ind}    {prop_iri}:")
        # Use the PropertySpec printer for nested properties
        lines.append(format_property_spec(prop_spec, indent=indent + 3))

    lines.append(f"{ind}  }}")

    if hasattr(class_spec, "metadata") and class_spec.metadata:
        lines.append(f"{ind}  metadata={class_spec.metadata}")

    lines.append(f"{ind})")
    return "\n".join(lines)
