from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence, Union, cast, overload
import logging
import warnings
import pydantic as pd
from rdflib import BNode, Literal

from kapps_triplestore_interface import IRI, SPARQLQuery, TripleStore
from kapps_triplestore_interface.utils.types import (
    GraphNameLike,
    IRILike,
    SubjectLike,
)

from kapps_ogm.node.core import Node
from kapps_ogm.node.node_address import reconcile_anonymous_addresses
from kapps_ogm.mapping.class_spec import ClassHydrationLevel, ClassSpec
from kapps_ogm.mapping.property_spec import PropertySpec, PropertyValueKind
from kapps_ogm.utils.blank_instance import _create_blank_instance
from kapps_ogm.utils.loader_strategy import LoaderStrategy
from kapps_ogm.utils.class_scope import ClassScope
from kapps_ogm.utils.errors import (
    AnonymousNodeFetchError,
    OGMDataError,
    OGMNotFoundError,
    OGMUsageError,
    OGMUsageTypeError,
)
from kapps_ogm.utils.pretty_print import format_triples_turtle
from kapps_ogm.utils.skolem import (
    DEFAULT_SKOLEM_NAMESPACE,
    is_skolem_iri,
    mint_skolem_iri,
    validate_skolem_namespace,
)

_DELETION_MESSAGE = (
    "An individual is never deleted. Retract its properties with commit(); "
    "the entry point is kept for a later extension."
)


@dataclass(frozen=True)
class Create:
    """A new instance, written by OGM.commit together with the other changes of one call.

    Args:
        class_iri: IRI of the class to instantiate
        data: Data dictionary for the new instance (must conform to the class)
        instance_iri: Optional IRI for the new instance. Give it when another change in the
            same call refers to the new instance. Otherwise the IRI is minted during the call.
        class_scope: Optional ClassScope, as for OGM.create
    """

    class_iri: IRI
    data: dict
    instance_iri: Optional[IRI] = None
    class_scope: Optional[ClassScope] = None


@dataclass(frozen=True)
class Update:
    """A change to an existing instance, written by OGM.commit together with the other changes
    of one call.

    Args:
        instance_iri: IRI of the instance to update
        data: Data dictionary, as for a commit of one instance. Each property it names replaces
            the stored values of that property, and [] removes the property. A property it
            does not name keeps its stored values.
    """

    instance_iri: IRI
    data: dict


@dataclass(frozen=True)
class Append:
    """One value added to a property of a stored instance, written by OGM.commit
    together with the other changes of one call.

    The other values of the property stay as they are: an append neither reads nor rewrites
    them. The value is validated as a commit's value is. The instance must be one that the store
    holds already; a value of an instance that a ``Create`` of the same call makes goes into that
    ``Create``.

    Args:
        instance_iri: IRI of the stored instance
        property_iri: IRI of the property, which the class of the instance must have
        value: The value, as in the data of a commit: a literal for a datatype property; an IRI
            or ``{"id": iri}`` for an object property, which writes the link and nothing about
            the linked instance; a dictionary of the node's properties, without an ``id``, for
            an anonymous node, which is always a new node under a minted Skolem IRI.
    """

    instance_iri: IRI
    property_iri: IRI
    value: Any


Change = Union[Create, Update, Append]

RDF_TYPE = IRI("rdf:type")


def _created_instances(triples: set) -> list[SubjectLike]:
    """The instances that a create writes about: the new instance and each nested instance its
    data gives with properties. A link writes only a triple whose subject is one of them.
    """
    return sorted(
        {subject for subject, _, _ in triples if not isinstance(subject, BNode)}
    )


def _described_instances(triples: set) -> set[IRI]:
    """The named instances that ``triples`` state something about, apart from their types."""
    return {
        subject
        for subject, predicate, _ in triples
        if not isinstance(subject, BNode) and IRI(predicate) != RDF_TYPE
    }


def _python_form(term: Any) -> Any:
    """A triple's object in the form the store's reads give it: a literal as its python value."""
    return term.toPython() if isinstance(term, Literal) else term


class _BoundedValues:
    """The maximum count of the properties that the appends of one commit write.

    An unbounded property is never read. For a bounded one, the stored values are read once per
    instance and property with ``triples_get``, as a fetch reads them (inferred values included),
    and each appended value that is not among them counts as one more. The read and the write are
    two requests: another writer can add a value between them, so two appends at one moment can
    go over the bound together. An invariant under concurrent writers belongs in a SHACL shape.
    """

    def __init__(self, db: TripleStore):
        self.db = db
        self.values: dict[tuple[IRI, IRI], set] = {}

    def add(self, node: Node, property_spec: PropertySpec, added: set) -> None:
        """Count the values ``added`` gives to the property of ``node``; raise over the bound."""
        maximum = property_spec.max_count
        if maximum is None:
            return
        instance_iri = cast(IRI, node.id)
        property_iri = IRI(property_spec.iri)
        values = self.values.get((instance_iri, property_iri))
        if values is None:
            values = {
                _python_form(triple[2])
                for triple in self.db.triples_get(sub=instance_iri, pred=property_iri)
            }
            self.values[(instance_iri, property_iri)] = values
        stored = len(values)
        values.update(
            _python_form(obj)
            for subject, predicate, obj in added
            if subject == instance_iri and IRI(predicate) == property_iri
        )
        if len(values) > maximum:
            raise OGMDataError(
                f"{property_iri} of {instance_iri} takes at most {maximum} values. The store "
                f"holds {stored}, and the appends would make it {len(values)}.",
                instance_iri=instance_iri,
                property_iri=property_iri,
                class_iri=node.class_spec.iri if node.class_spec else None,
            )


def _carrier_iri(
    root: Node, chain: Sequence[IRI], property_iri: Optional[IRI]
) -> Optional[IRI]:
    """The IRI of the node in ``root``'s data that carries ``property_iri``.

    ``chain`` is the property chain from ``root`` to the class whose scope refused the property.
    The nodes at its end whose data holds ``property_iri`` carry it; with no ``property_iri``
    (several properties refused at once), each node at its end that has data counts. The IRI is
    given only when exactly one node carries the property and it has an IRI, and otherwise None.
    """
    nodes = [root]
    for prop in chain:
        nodes = [
            child
            for node in nodes
            for child in (node.data or {}).get(IRI(prop), [])
            if isinstance(child, Node)
        ]
    carriers = [
        node
        for node in nodes
        if node.data and (property_iri is None or IRI(property_iri) in node.data)
    ]
    if len(carriers) == 1 and isinstance(carriers[0].id, IRI):
        return carriers[0].id
    return None


class OGM:
    """The object-graph mapper: instance data of a triple store as pydantic models.

    The shape of each model is derived from the ontology in the store at the time of the call:
    the declared properties of the class, their ranges, and the maximum number of values from
    the class's restrictions and the functional properties. Nothing is generated ahead of time,
    and the mapper keeps no state between calls beyond its store, its optional loader, its
    naming schema and its Skolem namespace.

    ``create`` writes a new instance, ``fetch`` reads one under a class scope, and ``commit``
    sends the difference between a data object and the stored instance as one atomic update.
    A write refuses a data object that names a property its class does not declare; a read
    ignores such a statement of the graph and warns. An instance is never deleted; a property
    is retracted with ``commit``.
    """

    def __init__(
        self,
        *,
        db: TripleStore,
        loader: Optional[LoaderStrategy] = None,
        logger: Optional[logging.Logger] = None,
        naming_schema: Optional[Callable[[IRI], IRI]] = None,
        skolem_namespace: str = DEFAULT_SKOLEM_NAMESPACE,
    ):
        """
        Args:
            db: The triple store, as the triple store interface's `connect()` returns it.

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

    def _given_or_loader_scope(
        self, class_iri: IRI, class_scope: Optional[ClassScope]
    ) -> Optional[ClassScope]:
        """The given class scope, else the loader's. An empty scope counts as none."""
        if class_scope:
            return class_scope
        return self.loader.expand(class_iri) if self.loader else None

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
        class_scope = self._given_or_loader_scope(class_iri, class_scope)
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

    def _data_class_spec(
        self,
        node: Node,
        class_iri: IRI,
        class_scope: Optional[ClassScope],
    ) -> ClassSpec:
        """The ClassSpec of ``class_iri`` in ``class_scope``, for the data of ``node``.

        A scope built from data, or given with it, can name a property that the class does not
        declare. The class specification refuses that while it is built, with an error that does
        not know the instance. The error that leaves here names the instance whose data carries
        the property, which may be a nested one."""
        try:
            return self.get_class_spec(
                class_iri=class_iri,
                class_scope=class_scope,
                hydration_level=ClassHydrationLevel.SCOPE,
            )
        except OGMDataError as error:
            if error.instance_iri is None:
                error.instance_iri = _carrier_iri(
                    node,
                    getattr(error, "_scope_chain", ()),
                    error.property_iri,
                )
            raise

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

        ``create`` writes only what it creates. The data decides what that is. A nested value
        given by its IRI alone, as ``{"id": iri}`` with no other key or as an IRI, is a link:
        ``create`` writes the triple that points at it and nothing about the linked instance,
        even when ``class_scope`` hydrates its class. The linked instance may exist. A nested
        value with properties is created with the new instance, under its given ``id`` or under
        a minted IRI.

        A normal return means that the store admitted the write: it found that none of the
        instances to create exists yet, and it committed the triples. See "What create and
        commit promise" in the README.

        Args:
            class_iri: IRI of the class to instantiate
            class_scope: ClassScope defining the class and property structure. Without it,
                the loader's scope is used, and without a loader the scope is derived from
                ``data``, as ``commit`` derives it.
            data: Data dictionary for the instance (must conform to the class)
            instance_iri: Optional IRI for the new instance (if not provided, a new one will be generated)
            persist: Whether to persist the new instance to the graph database
            named_graph: Optional named graph to persist the new instance to
        Returns:
            Node representing the newly created instance
        Raises:
            OGMDataError: If the data does not fit the class, for example if it names a
                property that the class does not declare, here or in a nested value. Its
                ``instance_iri``, ``property_iri`` and ``class_iri`` name the instance, the
                property and the class. Nothing is written. Refused.
                A property that the class declares and the class scope leaves out raises the
                subclass ``PropertyOutsideScopeError``, which also has ``scope``.
            OGMOntologyError: If the ontology has a shape that the OGM cannot map. Repair.
            OGMUsageError: If the call is malformed, for example data that is not a dictionary
                of lists, or data with properties and no class scope to give or derive. Fix
                the call.
            pydantic.ValidationError: If the data does not fit the generated model. It
                propagates unchanged.
            kapps_triplestore_interface.exceptions.PreconditionFailedError: If an instance it
                would create already exists: the target graph holds an explicit triple whose
                subject is the new instance or a nested instance that the data gives with
                properties. The target graph is ``named_graph`` if it is given, and otherwise
                the one the triple store interface writes to without it. Nothing is written. It
                propagates unchanged.
            kapps_triplestore_interface.exceptions.TripleStoreInterfaceError: If the triple
                store interface rejects the triples, or the store answers the write with an
                error, for example a SHACL refusal. It propagates unchanged.
            requests.exceptions.RequestException: If the triple store cannot be reached, for
                example on a connection error or a timeout. It propagates unchanged.

        This is the primary entry point for:

        - REST POST
        - API writes
        - JSON import

        To create an instance in the same write as other changes, pass a ``Create`` to
        ``OGM.commit``.
        """
        node = self._new_node(
            class_iri=class_iri,
            data=data,
            class_scope=class_scope,
            instance_iri=instance_iri,
        )

        if persist:
            triples = node.to_triples(links_from_data=True)
            self.db.triples_add(
                triples,
                named_graph=named_graph,
                absent_subjects=_created_instances(triples),
            )

        return node

    def _new_node(
        self,
        *,
        class_iri: IRI,
        data: dict,
        class_scope: Optional[ClassScope],
        instance_iri: Optional[IRI],
    ) -> Node:
        """A materialized Node for a new instance. Nothing is written.

        The class scope is the given one, else the loader's, else the one derived from the data,
        as ``commit`` derives it. An empty scope counts as none."""
        class_scope = self._given_or_loader_scope(class_iri, class_scope)
        if not class_scope:
            class_scope = ClassScope.from_data_dict(data)
            # Invariant check: every property key yields a chain, so a derived scope is empty
            # only for data without properties. This guards a later change of the derivation.
            if not class_scope and any(key != "id" for key in data):
                raise OGMUsageError(
                    f"No class scope for the new instance of {class_iri}: none was given, "
                    "no loader gives one, and none could be derived from the data. "
                    "Pass class_scope."
                )
        node = Node(
            id=instance_iri,
            data=data,
            ogm=self,
        )
        node.class_spec = self._data_class_spec(node, class_iri, class_scope)

        # A write refuses a property that the class does not declare, before anything is sent.
        node.materialize(refuse_unknown=True)
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
        class_scope = self._given_or_loader_scope(class_iri, class_scope)
        return _create_blank_instance(
            ogm=self,
            instance_iri=instance_iri or IRI("urn:uuid:generated-blank-instance"),
            class_iri=class_iri,
            class_scope=class_scope,
            hydration_level=ClassHydrationLevel.SCOPE,
        )

    def _assign_id(self, node: Node) -> None:
        if node.class_spec is None:
            raise OGMUsageError(f"Node {node} has no ClassSpec, cannot assign id.")
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
        # Re-query anonymous node, this time including properties. Explicit statements only: the
        # store infers more about the node (the inverse of the property back to the instance, the
        # types its class implies), and those are not the node's own data. A write could not
        # remove them either.
        query = SPARQLQuery.select(
            variables=["?bnode", "?property", "?value"],
            where_clauses=[
                f"<{instance_iri}> <{property_iri}> ?bnode .",
                "?bnode ?property ?value .",
            ],
            include_implicit=False,
        )
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
                and it is only meaningful as part of the entity that carries it. It is an
                OGMNotFoundError.
            OGMNotFoundError: If the store holds no class for the instance. Refresh.
            OGMDataError: If ``class_scope`` names a property that the class does not have, or
                continues a property chain past a property with a literal range. Refused.
            OGMOntologyError: If the ontology has a shape that the OGM cannot map. Repair.
            kapps_triplestore_interface.exceptions.TripleStoreInterfaceError: If the triple
                store interface or the store refuses a read. It propagates unchanged.
        """
        if is_skolem_iri(instance_iri):
            raise AnonymousNodeFetchError(
                f"{instance_iri} is an anonymous node — fetch its parent instead. "
                "Anonymous nodes carry no rdf:type and are only reachable through the "
                "property that points at them.",
                iri=instance_iri,
            )

        if class_scope is None and self.loader is not None:
            class_scope = self.loader.expand(instance_iri)

        if class_spec is None:
            class_iri_set = self.db.owl_get_classes_of_individual(instance_iri)
            if not class_iri_set:
                raise OGMNotFoundError(
                    f"Could not determine class IRI for instance {instance_iri}",
                    iri=instance_iri,
                )
            # The store gives IRIs: only a call with `local_name=True` gives strings.
            class_iri = cast(IRI, class_iri_set.pop())
            if class_iri_set:
                self.logger.warning(
                    "Instance %s has multiple classes, using %s and not %s.",
                    instance_iri,
                    class_iri,
                    class_iri_set,
                )
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

    @overload
    def commit(
        self,
        *,
        instance_iri: IRI,
        data: dict,
        named_graph: Optional[GraphNameLike] = None,
    ) -> Node: ...

    @overload
    def commit(
        self,
        *,
        changes: Sequence[Change],
        named_graph: Optional[GraphNameLike] = None,
    ) -> list[Node]: ...

    def commit(
        self,
        *,
        instance_iri: Optional[IRI] = None,
        data: Optional[dict] = None,
        changes: Optional[Sequence[Change]] = None,
        named_graph: Optional[GraphNameLike] = None,
    ) -> Union[Node, list[Node]]:
        """
        Update an instance with given data, or write the changes to several instances as one.

        Give either ``instance_iri`` and ``data``, which update one instance, or ``changes``.

        ``changes`` holds ``Create`` and ``Update`` entries, each for a different instance.
        All of them are validated and read first, and then written as one ``DELETE/INSERT``,
        so the store admits all of them or none of them. A change can refer to an instance
        that a ``Create`` in the same call makes. A ``Create`` writes only what it creates, as
        ``OGM.create`` does: a nested value given by its IRI alone is a link. An empty
        ``changes`` writes nothing.

        ``changes`` may also hold ``Append`` entries. An append adds one value to a property of a
        stored instance and leaves the property's other values as they are: it neither reads
        nor rewrites them. Several appends may name one instance, but an append and a
        ``Create`` or an ``Update`` may not. A call whose changes are all appends sends one
        request, a single ``INSERT DATA`` without a precondition, so two writers that append to
        one property at the same moment both land, and an append of a value the store holds
        already changes nothing. In a call with other changes, the appended triples go into the
        one ``DELETE/INSERT`` and add nothing to its precondition. Before the write, an append
        is refused if the class of the instance does not have the property, or if the value
        does not fit its range. For a property with a maximum count, the stored values are read
        first, and an append that would go over the maximum is refused; another writer can add
        a value between that read and the write.

        The triples that an ``Update`` removes come from a full read of the stored instance:
        inferred triples are included for literal and object properties, and only explicit
        triples for an anonymous node. The store removes only explicit triples of the target
        graph, so it checks the triples to remove against those. A normal return means that the store admitted the
        write: each triple to remove was an explicit triple of the target graph, no instance
        to create existed, and the store committed the change. When the data changes nothing,
        nothing is sent, because there is nothing to admit. A triple that is both explicit and
        inferred passes the check, and stays visible after the commit, because a delete clears
        only the explicit triple. See "What create and commit promise" in the README.

        The OGM's own errors below are raised before anything is written, and each of them is a
        ``ValueError``.

        Args:
            instance_iri: IRI of the instance to update
            data: Data dictionary for the instance (must conform to the class)
            changes: The changes to write in one update request, instead of ``instance_iri``
                and ``data``
            named_graph: Optional named graph to persist the changes to
        Returns:
            Node representing the newly updated instance. With ``changes``, a list with one
            Node for each change, in the same order. The Node of an append holds the appended
            value only, not the property's other values.
        Raises:
            OGMUsageTypeError: If both forms or neither are given, or ``changes`` holds an
                entry that is not a ``Create``, an ``Update`` or an ``Append``. It is a
                ``TypeError``. Fix the call.
            OGMUsageError: With ``changes``, if two changes write the same instance: the
                instance a change names, or a named instance that its data nests with
                properties. Types do not count, and appends to one instance do not conflict.
                Also for an append that would write about another instance: an object value
                with properties, or an anonymous node given with an ``id``. Fix the call.
            OGMNotFoundError: If the store holds no class for an instance to update or to
                append to. Refresh. AmbiguousNodeAlignmentError and
                UnresolvableNodeAddressError are subclasses.
            OGMDataError: If the data does not fit the class, for example if it names a
                property that the class does not declare, here or in a nested value. Its
                ``instance_iri``, ``property_iri`` and ``class_iri`` name the instance, the
                property and the class. Nothing is written. A property that the class declares
                and a ``Create``'s class scope leaves out raises the subclass
                ``PropertyOutsideScopeError``. Also if the class of an instance
                has no property an append names, or an append would go over the property's
                maximum count. Refused.
            OGMOntologyError: If the ontology has a shape that the OGM cannot map. Repair.
            pydantic.ValidationError: If the data does not fit the generated model. It
                propagates unchanged.
            kapps_triplestore_interface.exceptions.PreconditionFailedError: If a triple it
                would remove is not an explicit triple of the target graph. That is the case
                for a value that the store only infers, for a value that another writer changed
                after this call read it, and for a ``named_graph`` that does not hold the
                instance. With ``changes``, also if an instance that a ``Create`` would create
                already exists, as for ``OGM.create``. The target graph is ``named_graph`` if it
                is given, and otherwise the one the triple store interface writes to without it.
                Nothing of the call is written. It propagates unchanged.
            kapps_triplestore_interface.exceptions.TripleStoreInterfaceError: If the triple
                store interface rejects the triples, or the store answers the write with an
                error, for example a SHACL refusal. It propagates unchanged. A refusal of one
                change refuses all changes of the call.
            requests.exceptions.RequestException: If the triple store cannot be reached, for
                example on a connection error or a timeout. It propagates unchanged.
        """
        if changes is None:
            if instance_iri is None or data is None:
                raise OGMUsageTypeError(
                    "commit() takes instance_iri and data, or changes"
                )
            return self._commit(
                [Update(instance_iri=instance_iri, data=data)], named_graph
            )[0]

        if instance_iri is not None or data is not None:
            raise OGMUsageTypeError(
                "commit() takes instance_iri and data, or changes, but not both"
            )
        return self._commit(list(changes), named_graph)

    def _commit(
        self,
        changes: list[Change],
        named_graph: Optional[GraphNameLike],
    ) -> list[Node]:
        """Validate and read every change, then write them all with one ``triples_update``."""
        if not changes:
            return []

        # Each instance a change names, and whether an append names it. Several appends may
        # name one instance; any other change must be the only one that names it.
        named: dict[IRI, bool] = {}
        for change in changes:
            if not isinstance(change, (Create, Update, Append)):
                raise OGMUsageTypeError(
                    "commit() takes Create, Update and Append changes, "
                    f"got {type(change).__name__}"
                )
            if change.instance_iri is None:
                continue
            iri = IRI(change.instance_iri)
            is_append = isinstance(change, Append)
            if iri in named and not (named[iri] and is_append):
                raise OGMUsageError(
                    f"Two changes name {iri}. Put all changes to one instance in one change."
                )
            named[iri] = is_append

        nodes: list[Node] = []
        old_triples: set = set()
        new_triples: set = set()
        created: set[SubjectLike] = set()
        writers: dict[IRI, tuple[int, bool]] = {}
        bounded = _BoundedValues(self.db)
        for index, change in enumerate(changes):
            if isinstance(change, Create):
                node = self._new_node(
                    class_iri=change.class_iri,
                    data=change.data,
                    class_scope=change.class_scope,
                    instance_iri=change.instance_iri,
                )
                added = set(node.to_triples(links_from_data=True))
                # A create that lands on an existing instance would merge into it, because an
                # INSERT of statements the store already holds changes nothing. So the write
                # requires that the store holds no statement about any instance it creates.
                created.update(_created_instances(added))
                if self.logger.isEnabledFor(logging.DEBUG):
                    self.logger.debug(
                        f"Creating instance {node.id}: adding {len(added)} triples:\n\n{format_triples_turtle(added)}",
                    )
                removed: set = set()
            elif isinstance(change, Append):
                node, added, property_spec = self._appended_node(change)
                bounded.add(node, property_spec, added)
                removed = set()
            else:
                node, removed, added = self._changed_node(
                    instance_iri=IRI(change.instance_iri), data=change.data
                )
            # Each change is compared with the stored state on its own, so two changes that
            # write one instance would each apply their own diff to it. The guard above sees only
            # the named instances; this one also sees a named instance nested with properties.
            # Types do not count: a link typed by its range states what the instance's own
            # Create states. Appends read no stored state, so several of them write one
            # instance side by side.
            is_append = isinstance(change, Append)
            for instance in _described_instances(removed | added):
                first, first_is_append = writers.setdefault(
                    instance, (index, is_append)
                )
                if first != index and not (first_is_append and is_append):
                    raise OGMUsageError(
                        f"Two changes write {instance} (positions {first} and {index}). "
                        "Put all changes to one instance in one change."
                    )
            nodes.append(node)
            old_triples |= removed
            new_triples |= added

        if all(isinstance(change, Append) for change in changes):
            # Nothing to remove and nothing to check: one INSERT DATA, which the store applies
            # whatever another writer commits beside it. Inserting a triple the store holds
            # changes nothing, so the append is idempotent.
            self.db.triples_add(
                list(new_triples),
                check_exist=False,
                named_graph=named_graph,
            )
            return nodes

        # triples_update sends one atomic DELETE/INSERT update request, so
        # the removed and added triples are applied together. This is required for
        # SHACL correctness: a replacement of a cardinality-constrained property (e.g.
        # a possession handover under a "possessed by exactly one resource" shape)
        # must never expose the intermediate state where the property is absent. It is
        # also what makes the changes to several instances one write that the store
        # admits or refuses as a whole.
        self.db.triples_update(
            old_triples=old_triples,
            new_triples=new_triples,
            named_graph=named_graph,
            absent_subjects=sorted(created),
        )

        return nodes

    def _changed_node(
        self,
        *,
        instance_iri: IRI,
        data: dict,
    ) -> tuple[Node, set, set]:
        """The Node that ``data`` makes of a stored instance, and the triples to remove and to
        add. Nothing is written."""
        new_node = Node(id=instance_iri, data=data, ogm=self)
        class_spec, class_scope = self._stored_class_spec(new_node)
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

        new_node.materialize(refuse_unknown=True)

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

        return new_node, old_triples, new_triples

    def _stored_class_spec(self, node: Node) -> tuple[ClassSpec, ClassScope]:
        """The ClassSpec of the stored instance that ``node`` names, hydrated to the scope of
        the node's data, and that scope."""
        instance_iri = cast(IRI, node.id)
        class_iri_set = self.db.owl_get_classes_of_individual(instance_iri)
        if not class_iri_set:
            raise OGMNotFoundError(
                f"Could not determine class IRI for instance {instance_iri}",
                iri=instance_iri,
            )
        # The store gives IRIs: only a call with `local_name=True` gives strings.
        class_iri = cast(IRI, class_iri_set.pop())
        if class_iri_set:
            self.logger.warning(
                "Instance %s has multiple classes, using %s and not %s.",
                instance_iri,
                class_iri,
                class_iri_set,
            )

        class_scope = ClassScope.from_node_data(node)
        class_spec = self._data_class_spec(node, class_iri, class_scope)
        return class_spec, class_scope

    def _appended_node(self, change: Append) -> tuple[Node, set, PropertySpec]:
        """The Node that holds the appended value, the triples to add, and the property's
        spec. Nothing is read of the stored values, and nothing is written."""
        instance_iri = IRI(change.instance_iri)
        property_iri = IRI(change.property_iri)
        value = change.value

        data: dict[IRILike, list[Any]] = {property_iri: [value]}
        node = Node(id=instance_iri, data=data, ogm=self)
        class_spec, _ = self._stored_class_spec(node)

        # A property the class does not have is ignored on materialization (by design), so an
        # append of one would write nothing and return normally. It is refused here.
        property_spec = class_spec.properties.get(property_iri)
        if property_spec is None:
            raise OGMDataError(
                f"The class {class_spec.iri} of {instance_iri} has no property {property_iri}, "
                "so nothing can be appended to it.",
                instance_iri=instance_iri,
                property_iri=property_iri,
                class_iri=class_spec.iri,
                value=value,
            )
        keys = {str(key) for key in value} if isinstance(value, dict) else set()
        if property_spec.value_kind is PropertyValueKind.OBJECT and keys - {"id"}:
            raise OGMUsageError(
                f"An append to {property_iri} links an instance by its IRI and writes nothing "
                "about it. To make the instance, put a Create of it in the same commit and "
                "append its IRI."
            )
        if property_spec.value_kind is PropertyValueKind.COMPLEX and "id" in keys:
            raise OGMUsageError(
                f"An append to {property_iri} makes a new anonymous node, so its value takes no "
                "id. To change a stored anonymous node, commit an Update of its instance."
            )

        node.class_spec = class_spec
        node.materialize(refuse_unknown=True)

        # Only the link and the new anonymous node: the instance's types are stored already.
        added = {
            triple
            for triple in node.to_triples(links_from_data=True)
            if triple[0] != instance_iri or IRI(triple[1]) == property_iri
        }
        if self.logger.isEnabledFor(logging.DEBUG):
            self.logger.debug(
                f"Appending to {property_iri} of {instance_iri}: adding {len(added)} triples:\n\n{format_triples_turtle(added)}",
            )
        return node, added, property_spec

    # ------------------------------------------------------------------
    # deletion of instances
    # ------------------------------------------------------------------

    def _delete(
        self,
        *,
        node: Node,
    ) -> None:
        """
        Deletion is not part of the mapper's public interface, by decision.

        An individual is never deleted: other data may still refer to it, and a mapper that
        removed it would leave those references dangling. A property is retracted with ``commit``, which removes
        the triples that the new data no longer holds. This entry point is kept, private, for a
        later extension.

        Args:
            node: Node instance that would be deleted
        Raises:
            NotImplementedError: always
        """
        raise NotImplementedError(_DELETION_MESSAGE)

    def delete(
        self,
        *,
        node: Node,
    ) -> None:
        """
        Deprecated alias of the private ``_delete``, kept for one release.

        It warns with a ``DeprecationWarning`` and raises the error that ``_delete`` raises.

        Raises:
            NotImplementedError: always
        """
        warnings.warn(
            "OGM.delete is deprecated: an individual is never deleted, and the name goes "
            "in a later release. Retract properties with commit().",
            DeprecationWarning,
            stacklevel=2,
        )
        self._delete(node=node)
