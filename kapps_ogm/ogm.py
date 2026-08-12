from __future__ import annotations

from typing import Any, Callable, Optional
import logging
import pydantic as pd

from kapps_triplestore_interface import GraphDB, IRI
from kapps_triplestore_interface.utils.types import GraphNameLike

from kapps_ogm.node.core import Node
from kapps_ogm.node.node_address import reconcile_anonymous_addresses
from kapps_ogm.mapping.class_spec import ClassHydrationLevel, ClassSpec
from kapps_ogm.mapping.property_spec import PropertySpec, PropertyValueKind
from kapps_ogm.utils.blank_instance import _create_blank_instance
from kapps_ogm.utils.loader_strategy import LoaderStrategy
from kapps_ogm.utils.class_scope import ClassScope
from kapps_ogm.utils.errors import AnonymousNodeFetchError
from kapps_ogm.utils.pretty_print import format_triples_turtle
from kapps_ogm.utils.skolem import (
    DEFAULT_SKOLEM_NAMESPACE,
    is_skolem_iri,
    mint_skolem_iri,
    validate_skolem_namespace,
)


class OGM:
    """
    TODO: Docstring for OGM
    """

    def __init__(
        self,
        *,
        db: GraphDB,
        loader: Optional[LoaderStrategy] = None,
        logger: Optional[logging.Logger] = None,
        naming_schema: Optional[Callable[[], str]] = None,
        skolem_namespace: str = DEFAULT_SKOLEM_NAMESPACE,
    ):
        """
        Args:
            db: GraphDB interface

            loader: LoaderStrategy that takes an IRI and returns property chains for selective instance loading. if not specified, property chains need to be provided at fetch/creation time.

            skolem_namespace: Namespace under which Skolem IRIs are minted for anonymous nodes.
                The minting authority is an ontology-governance decision, so this is configurable;
                the default is a placeholder whose path starts with ``/.well-known/genid/``.
        """
        self.db = db
        self.loader = loader
        self.naming_schema = naming_schema
        self.skolem_namespace = validate_skolem_namespace(skolem_namespace)
        self.logger = logger or logging.getLogger("kapps_ogm")
        self.logger.setLevel(logging.INFO)

    # ------------------------------------------------------------------
    # Schema orchestration
    # ------------------------------------------------------------------

    def get_class_spec(
        self,
        *,
        class_iri: IRI,
        class_scope: Optional[ClassScope] = None,
        hydration_level: ClassHydrationLevel = ClassHydrationLevel.SCOPE,
    ) -> ClassSpec:
        """
        Resolve a ClassSpec for a given class IRI and ClassScope.

        ClassScope defines selective hydration of nested properties.
        """
        class_scope = class_scope or (
            self.loader.expand(class_iri) if self.loader else None
        )
        self.logger.debug(
            "Resolving ClassSpec for %s (chain=%s, hydration_level=%s)",
            class_iri,
            class_scope,
            hydration_level,
        )

        spec = ClassSpec.specify(
            class_iri=class_iri,
            ogm=self,
            class_scope=class_scope,
            hydration_level=hydration_level,
        )
        return spec

    # ------------------------------------------------------------------
    # Creating new instances
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        class_iri: IRI,
        data: dict,
        class_scope: Optional[ClassScope] = None,
        instance_iri: Optional[IRI] = None,
        persist: bool = True,
        named_graph: Optional[GraphNameLike] = None,
    ) -> Node:
        """
        Create a new Node instance with given data.
        Args:
            class_iri: IRI of the class to instantiate
            class_scope: ClassScope defining the class and property structure
            data: Data dictionary for the instance (must conform to class_spec)
            instance_iri: Optional IRI for the new instance (if not provided, a new one will be generated)
            persist: Whether to persist the new instance to the graph database
        Returns:
            Node representing the newly created instance

        This is the primary entry point for:
        - REST POST
        - API writes
        - JSON import
        """
        class_scope = class_scope or (
            self.loader.expand(class_iri) if self.loader else None
        )
        class_spec = self.get_class_spec(
            class_iri=class_iri,
            class_scope=class_scope,
        )

        node = Node(
            id=instance_iri,
            class_spec=class_spec,
            data=data,
            ogm=self,
        )

        node.materialize()

        if persist:
            triples = node.to_triples()
            try:
                self.db.triples_add(triples, named_graph=named_graph)
            except Exception as e:
                raise Exception("Failed to persist instance.") from e

        return node

    def create_blank_instance(
        self,
        *,
        class_iri: IRI,
        class_scope: Optional[ClassScope] = None,
        instance_iri: Optional[IRI] = None,
    ) -> pd.BaseModel:
        """
        Create a blank pydantic instance for a given ClassScope, able to serve as a template for data population.

        Args:
            class_iri: IRI of the class to instantiate
            class_scope: ClassScope defining the class and property structure
            instance_iri: Optional IRI for the new instance
        Returns:
            pydantic BaseModel representing the blank instance
        """
        class_scope = class_scope or (
            self.loader.expand(class_iri) if self.loader else None
        )
        return _create_blank_instance(
            ogm=self,
            instance_iri=instance_iri or IRI("urn:uuid:generated-blank-instance"),
            class_iri=class_iri,
            class_scope=class_scope,
            hydration_level=ClassHydrationLevel.SCOPE,
        )

    def _assign_id(self, node: Node) -> None:
        if node.class_spec is None:
            raise ValueError(f"Node {node} has no ClassSpec, cannot assign id.")
        model_iri = getattr(node.class_spec, "iri", None)
        if model_iri:
            instance_id = self.db.new_iri(base=model_iri, schema=self.naming_schema)
        else:
            # An anonymous node gets a Skolem IRI rather than a blank node. A blank node has no
            # extent and cannot be addressed, so it can only be re-found by matching a pattern
            # from a named subject — which is what makes the write path destructive. RDF 1.1
            # Concepts §3.5 sanctions the substitution; nothing is asserted about the IRI, so the
            # meaning of the graph is unchanged.
            instance_id = mint_skolem_iri(self.skolem_namespace)
        node.id = instance_id

    # ------------------------------------------------------------------
    # Fetching existing instances (Read)
    # ------------------------------------------------------------------

    def _fetch_literal_property(
        self,
        instance_iri: IRI,
        property_spec: PropertySpec,
    ) -> list[Any]:
        triples = self.db.triples_get(sub=instance_iri, pred=property_spec.iri)
        property_data = [r[2] for r in triples]
        return property_data

    def _fetch_complex_property(
        self,
        instance_iri: IRI,
        property_iri: IRI,
    ) -> list[dict[IRI, list[Any]]]:
        """
        Helper function to fetch complex property data for a given instance and property spec.
        All properties attached to the complex class are fetched.

        Each returned group carries an ``"id"`` entry holding the node's own identifier — an IRI
        once the node has been skolemised, a BNode while it is still in the store's blank-node
        form. Discarding it, as this used to, is where identity died: the write path then had no
        way to address the node it had just read, so it minted a replacement and orphaned every
        triple this query returned but the ClassSpec does not declare.

        Groups come back ordered by identifier so that two fetches of unchanged data align
        positionally, which is what ``reconcile_anonymous_addresses`` relies on.
        """
        # Re-query anonymous node, this time including properties
        query = f"""
                SELECT ?bnode ?property ?value
                FROM <http://www.ontotext.com/explicit>
                WHERE {{
                    <{instance_iri}> <{property_iri}> ?bnode .
                    ?bnode ?property ?value .
                }}
            """
        query_result = (
            self.db.query(query, convert_bindings=True)
            .get("results", {})
            .get("bindings", [])
        )
        if not query_result:
            return []

        property_data_dict: dict[str, dict] = {}
        for binding in query_result:
            node_ref = binding["bnode"]
            prop_iri = IRI(str(binding["property"]))
            value = binding["value"]

            # Key on the string form, but keep the identifier object itself: it is the node's
            # address, and an IRI must stay an IRI while a blank node must stay a BNode.
            group = property_data_dict.setdefault(str(node_ref), {"id": node_ref})
            group.setdefault(prop_iri, [])
            group[prop_iri].append(value)

        # [{"id": <IRI|BNode>, property_iri: [value1, value2, ...], ...}, ...]
        # Keys are heterogeneous: the literal "id" holds the node's address, every other key is
        # a property IRI holding a list of values.
        property_data: list[dict[Any, Any]] = [
            property_data_dict[key] for key in sorted(property_data_dict)
        ]
        return property_data

    def _fetch_object_property(
        self,
        instance_iri: IRI,
        property_spec: PropertySpec,
        nested_class_scope: Optional[ClassScope],
    ) -> list[Node]:
        # Query all instances of the property
        triples = self.db.triples_get(sub=instance_iri, pred=property_spec.iri)
        if not triples:
            return []
        nested_instance_iris = [r[2] for r in triples]

        # Check if this property has a child scope
        # If not, fetch as reference only
        as_reference = nested_class_scope is None

        property_data = []

        for nested_instance_iri in nested_instance_iris:
            nested_instance = self.fetch(
                instance_iri=nested_instance_iri,
                class_spec=property_spec.nested,
                class_scope=nested_class_scope,
                as_reference=as_reference,
            )
            property_data.append(nested_instance)

        return property_data

    def fetch(
        self,
        *,
        instance_iri: IRI,
        class_spec: Optional[ClassSpec] = None,
        class_scope: Optional[ClassScope] = None,
        as_reference: bool = False,
        materialize: bool = False,
    ) -> Node:
        """
        Fetch an existing RDF instance from the database and return a Node for that instance.

        Args:
            instance_iri: IRI of the instance to fetch
            class_spec: Optional ClassSpec to use for fetching. If not provided, it will be resolved automatically
            class_scope: Optional ClassScope for selective hydration
            as_reference: If True, fetch only the IRI without loading properties
            materialize: If True, materialize the Node's data according to the ClassSpec.
                Ignored if as_reference is True.

        Returns:
            Node representing the fetched instance

        Raises:
            AnonymousNodeFetchError: If instance_iri is a Skolem IRI. Such a node is anonymous:
                nothing is asserted about it, so it has no rdf:type to resolve a ClassSpec from,
                and it is only meaningful as part of the entity that carries it.
        """
        if is_skolem_iri(instance_iri):
            raise AnonymousNodeFetchError(
                f"{instance_iri} is an anonymous node — fetch its parent instead. "
                "Anonymous nodes carry no rdf:type and are only reachable through the "
                "property that points at them."
            )

        if class_scope is None and self.loader is not None:
            class_scope = self.loader.expand(instance_iri)

        if class_spec is None:
            class_iri_set = self.db.owl_get_classes_of_individual(instance_iri)
            if not class_iri_set:
                raise ValueError(
                    f"Could not determine class IRI for instance {instance_iri}"
                )
            if len(class_iri_set) > 1:
                self.logger.warning(
                    "Instance %s has multiple classes %s, using the first one.",
                    instance_iri,
                    class_iri_set,
                )
            class_iri = class_iri_set.pop()
            class_spec = self.get_class_spec(
                class_iri=class_iri,
                class_scope=class_scope,
                hydration_level=ClassHydrationLevel.SCOPE,
            )

        data = {}
        if not as_reference:
            # Full fetch according to class spec and class scope
            for prop, property_spec in class_spec.properties.items():
                match property_spec.value_kind:
                    case PropertyValueKind.LITERAL:
                        # Fetch literal values directly
                        property_data = self._fetch_literal_property(
                            instance_iri=instance_iri,
                            property_spec=property_spec,
                        )
                    case PropertyValueKind.COMPLEX:
                        property_data = self._fetch_complex_property(
                            instance_iri=instance_iri,
                            property_iri=property_spec.iri,
                        )
                    case PropertyValueKind.OBJECT:
                        nested_class_scope = (
                            class_scope.get(prop, None) if class_scope else None
                        )
                        property_data = self._fetch_object_property(
                            instance_iri=instance_iri,
                            property_spec=property_spec,
                            nested_class_scope=nested_class_scope,
                        )
                    case _:
                        raise ValueError(
                            f"Unknown value_kind {property_spec.value_kind} for property {property_spec.iri}"
                        )

                # Ignore if none found
                if property_data:
                    data[prop] = property_data

        node = Node(
            id=instance_iri,
            class_spec=class_spec,
            data=data,
            instance=None,  # instance can be materialized later if needed
            ogm=self,
        )

        if not as_reference and materialize:
            node.materialize()

        return node

    # ------------------------------------------------------------------
    # updating existing instances
    # ------------------------------------------------------------------

    def commit(
        self,
        *,
        instance_iri: IRI,
        data: dict,
        named_graph: Optional[GraphNameLike] = None,
    ) -> Node:
        """
        Update a Node instance with given data.
        Args:
            instance_iri: IRI of the instance to update
            data: Data dictionary for the instance (must conform to class_spec)
            named_graph: Optional named graph to persist the changes to
        Returns:
            Node representing the newly updated instance
        """
        new_node = Node(id=instance_iri, data=data, ogm=self)

        class_iri_set = self.db.owl_get_classes_of_individual(instance_iri)
        if not class_iri_set:
            raise ValueError(
                f"Could not determine class IRI for instance {instance_iri}"
            )
        if len(class_iri_set) > 1:
            self.logger.warning(
                "Instance %s has multiple classes %s, using the first one.",
                instance_iri,
                class_iri_set,
            )

        class_iri = class_iri_set.pop()
        class_scope = ClassScope.from_node_data(new_node)
        class_spec = self.get_class_spec(
            class_iri=class_iri,
            class_scope=class_scope,
            hydration_level=ClassHydrationLevel.SCOPE,
        )

        new_node.class_spec = class_spec

        # Fetch before materializing, so the addresses of the anonymous nodes already in the store
        # can be transferred onto the node about to be written. The caller's payload cannot carry
        # them: the address is deliberately absent from the pydantic projection, so a fetch,
        # model_dump(), edit, commit cycle arrives here with none. Without this the commit would
        # mint fresh addresses, delete the existing nodes and strand every triple the ClassSpec
        # does not declare — connection metadata included — on nodes nothing points at.
        old_node = self.fetch(
            instance_iri=instance_iri,
            class_spec=new_node.class_spec,
            class_scope=class_scope,
            materialize=True,
        )
        reconcile_anonymous_addresses(old=old_node, new=new_node)

        new_node.materialize()

        old_triples, new_triples = old_node.diff(other=new_node)

        # Merge note: origin/paper logged the full turtle at INFO. Kept the richer
        # content but moved to DEBUG and guarded, because a library that dumps every
        # triple on every commit is unusable for a consumer that commits per
        # operation-status change — and the f-string would render the turtle even
        # when the level suppresses it.
        if self.logger.isEnabledFor(logging.DEBUG):
            self.logger.debug(
                f"Updating instance {instance_iri}: removing {len(old_triples)} triples, adding {len(new_triples)} triples:\n\n--- Old triples to be deleted ---\n{format_triples_turtle(old_triples)}\n\n--- New triples to be added ---\n{format_triples_turtle(new_triples)}",
            )

        # triples_update issues a single atomic DELETE/INSERT SPARQL transaction, so
        # the removed and added triples are applied together. This is required for
        # SHACL correctness: a replacement of a cardinality-constrained property (e.g.
        # a possession handover under a "possessed by exactly one resource" shape)
        # must never expose the intermediate state where the property is absent.
        try:
            self.db.triples_update(
                old_triples=old_triples,
                new_triples=new_triples,
                named_graph=named_graph,
            )
        except Exception as e:
            raise Exception("Failed to update instance in database.") from e

        return new_node

    # ------------------------------------------------------------------
    # deletion of instances
    # ------------------------------------------------------------------

    def delete(
        self,
        *,
        node: Node,
    ) -> bool:
        """
        Delete an existing Node instance from the graph database.

        Args:
            node: Node instance to delete
        Returns:
            bool: True if deletion was successful, False otherwise
        """
        raise NotImplementedError("Delete method is not yet implemented.")
        return False
