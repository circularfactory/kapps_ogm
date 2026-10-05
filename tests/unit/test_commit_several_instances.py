"""Tests for ``OGM.commit(changes=...)``: changes to several instances, written as one.

The live classes use a change of possession as their example shape: a workpiece points at a
possession state, and its possessor points into the same state. The example makes a new state and
changes two other subjects. One commit of one instance cannot reach all three, and three commits
leave states between them that the store may refuse, or that another reader may see. The tests
pin what the OGM does with such a call. They do not decide how a possession is modelled.

The first classes run on a mocked store and pin the rules of the call. The live classes run the
example on a real store: it is one update request, a refusal of one part leaves every part
unwritten, the existence check of a ``Create`` counts explicit statements only, a triple to remove
that the write does not find refuses the whole call, and two changes that write one instance are
refused, apart from its types. The refusal needs a repository with
SHACL validation on, as CI provisions.
"""

import uuid
from unittest.mock import Mock, patch

import pytest

from kapps_triplestore_interface import GraphDB, IRI
from kapps_triplestore_interface.exceptions import (
    PreconditionFailedError,
    TripleStoreInterfaceError,
)

from kapps_ogm import Create, Update
from kapps_ogm.mapping.class_spec import ClassHydrationLevel, ClassSpec
from kapps_ogm.ogm import OGM
from kapps_ogm.utils.class_scope import ClassScope

NS = "https://example.org/several#"
WORKPIECE = IRI(NS + "Workpiece")
RESOURCE = IRI(NS + "Resource")
POSSESSION_STATE = IRI(NS + "PossessionState")
HAS_POSSESSED_WORKPIECE = IRI(NS + "hasPossessedWorkpiece")
HAS_POSSESSOR = IRI(NS + "hasPossessor")

RDF_TYPE = IRI("rdf:type")
SHAPES_GRAPH = IRI("http://rdf4j.org/schema/rdf4j#SHACLShapeGraph")


def fresh(name: str) -> IRI:
    """An instance IRI no other test uses, because the live store is wiped only per session."""
    return IRI(f"{NS}{name}_{uuid.uuid4().hex[:8]}")


# ---------------------------------------------------------------------------
# The rules of the call, on a mocked store
# ---------------------------------------------------------------------------


def state_spec() -> ClassSpec:
    return ClassSpec(
        iri=POSSESSION_STATE,
        types=[],
        hydration_level=ClassHydrationLevel.FULL,
        properties={},
    )


@pytest.fixture
def store() -> Mock:
    db = Mock(spec=GraphDB)
    db.iri_exists.return_value = False
    return db


@pytest.fixture
def ogm(store: Mock) -> OGM:
    """An OGM on the mocked store. It shadows the live ``ogm`` of the root conftest here."""
    ogm = OGM(db=store)
    with patch.object(ogm, "get_class_spec", side_effect=lambda **_: state_spec()):
        yield ogm


def new_state(iri: IRI) -> Create:
    return Create(class_iri=POSSESSION_STATE, data={}, instance_iri=iri)


class TestTheTwoForms:
    """``commit`` takes one instance or a list of changes, never both and never neither."""

    def test_both_forms_at_once_are_refused(self, ogm, store):
        with pytest.raises(TypeError):
            ogm.commit(
                instance_iri=fresh("box"),
                data={},
                changes=[new_state(fresh("state"))],
            )
        store.triples_update.assert_not_called()

    @pytest.mark.parametrize(
        "arguments",
        [{}, {"instance_iri": IRI(NS + "box")}, {"data": {}}],
        ids=["nothing", "no data", "no instance"],
    )
    def test_an_incomplete_single_form_is_refused(self, ogm, store, arguments):
        with pytest.raises(TypeError):
            ogm.commit(**arguments)
        store.triples_update.assert_not_called()

    def test_an_entry_that_is_not_a_change_is_refused_before_any_read(self, ogm, store):
        with pytest.raises(TypeError):
            ogm.commit(changes=[new_state(fresh("state")), {"id": NS + "box"}])
        store.iri_exists.assert_not_called()
        store.triples_update.assert_not_called()

    def test_no_changes_write_nothing(self, ogm, store):
        assert ogm.commit(changes=[]) == []
        store.triples_update.assert_not_called()


class TestOneWrite:
    def test_every_change_goes_into_one_update(self, ogm, store):
        first, second = fresh("state"), fresh("state")
        ogm.commit(changes=[new_state(first), new_state(second)])

        store.triples_update.assert_called_once()
        call = store.triples_update.call_args
        assert call.kwargs["old_triples"] == set()
        assert {triple[0] for triple in call.kwargs["new_triples"]} == {first, second}

    def test_returns_one_node_per_change_in_order(self, ogm):
        first, second = fresh("state"), fresh("state")
        nodes = ogm.commit(changes=[new_state(first), new_state(second)])
        assert [node.id for node in nodes] == [first, second]

    def test_the_named_graph_applies_to_the_whole_write(self, ogm, store):
        graph = IRI("http://example.org/named_graph")
        ogm.commit(changes=[new_state(fresh("state"))], named_graph=graph)
        assert store.triples_update.call_args.kwargs["named_graph"] == graph


class TestNothingIsWritten:
    """Each of these is refused before the write, so no change of the call reaches the store."""

    def test_when_two_changes_name_one_instance(self, ogm, store):
        state = fresh("state")
        with pytest.raises(ValueError, match="Two changes name"):
            ogm.commit(
                changes=[
                    new_state(state),
                    Update(instance_iri=state, data={}),
                ]
            )
        store.iri_exists.assert_not_called()
        store.triples_update.assert_not_called()

    def test_the_instance_of_a_create_goes_into_the_write_as_absent(self, ogm, store):
        """The store refuses the write if the instance of a ``Create`` exists. The check is
        part of the write, so nothing is read before it to decide it."""
        state = fresh("state")
        ogm.commit(changes=[new_state(state)])
        assert store.triples_update.call_args.kwargs["absent_subjects"] == [state]
        for call in store.iri_exists.call_args_list:
            assert "include_implicit" not in call.kwargs

    def test_when_a_later_change_does_not_fit_its_class(self, ogm, store):
        store.owl_get_classes_of_individual.return_value = []
        with pytest.raises(ValueError, match="Could not determine class"):
            ogm.commit(
                changes=[
                    new_state(fresh("state")),
                    Update(instance_iri=fresh("unknown"), data={}),
                ]
            )
        store.triples_update.assert_not_called()

    def test_when_a_create_links_through_a_property_its_class_does_not_have(
        self, ogm, store
    ):
        """The data does not fit the class, so the call raises the ``ValueError`` that names
        the property, not an error from describing the half-built node."""
        with pytest.raises(ValueError, match="not defined in ClassSpec"):
            ogm.commit(
                changes=[
                    Create(
                        class_iri=POSSESSION_STATE,
                        data={NS + "carries": [{"id": NS + "box"}]},
                        instance_iri=fresh("state"),
                    )
                ]
            )
        store.triples_update.assert_not_called()


class TestARefusedWrite:
    def test_the_store_error_propagates_unchanged(self, ogm, store, store_error):
        store.triples_update.side_effect = store_error
        with pytest.raises(TripleStoreInterfaceError) as caught:
            ogm.commit(changes=[new_state(fresh("state")), new_state(fresh("state"))])
        assert caught.value is store_error


# ---------------------------------------------------------------------------
# A change of possession, on a live store
# ---------------------------------------------------------------------------

ONTOLOGY = [
    (WORKPIECE, RDF_TYPE, IRI("owl:Class")),
    (RESOURCE, RDF_TYPE, IRI("owl:Class")),
    (POSSESSION_STATE, RDF_TYPE, IRI("owl:Class")),
    (HAS_POSSESSED_WORKPIECE, RDF_TYPE, IRI("owl:ObjectProperty")),
    (HAS_POSSESSED_WORKPIECE, IRI("rdfs:domain"), WORKPIECE),
    (HAS_POSSESSED_WORKPIECE, IRI("rdfs:range"), POSSESSION_STATE),
    (HAS_POSSESSOR, RDF_TYPE, IRI("owl:ObjectProperty")),
    (HAS_POSSESSOR, IRI("rdfs:domain"), RESOURCE),
    (HAS_POSSESSOR, IRI("rdfs:range"), POSSESSION_STATE),
]

# A resource possesses at most one workpiece. Its target is this file's own class, so it cannot
# refuse a write of any other test.
ONE_WORKPIECE_PER_RESOURCE = f"""
    PREFIX sh: <http://www.w3.org/ns/shacl#>
    INSERT DATA {{
        GRAPH <{SHAPES_GRAPH}> {{
            <{NS}ResourceShape> a sh:NodeShape ;
                sh:targetClass <{RESOURCE}> ;
                sh:property [ sh:path <{HAS_POSSESSOR}> ; sh:maxCount 1 ] .
        }}
    }}
"""


@pytest.fixture
def live_ogm(db: GraphDB) -> OGM:
    db.triples_add(ONTOLOGY, check_exist=False)
    return OGM(db=db)


@pytest.fixture
def one_workpiece_per_resource(db: GraphDB):
    """The shape above, loaded for one test. ``CLEAR GRAPH`` removes it with every other shape.
    SPARQL cannot match the shapes graph, and ``DELETE DATA`` cannot name the blank node of
    ``sh:property [ ... ]``, so no narrower removal reaches this shape."""
    db.query(ONE_WORKPIECE_PER_RESOURCE, update=True)
    yield
    db.query(f"CLEAR GRAPH <{SHAPES_GRAPH}>", update=True)


def possess(db: GraphDB, resource: IRI, workpiece: IRI) -> IRI:
    """Seed ``resource`` as the possessor of ``workpiece``, directly, and return the state."""
    state = fresh("state")
    db.triples_add(
        [
            (workpiece, RDF_TYPE, WORKPIECE),
            (resource, RDF_TYPE, RESOURCE),
            (state, RDF_TYPE, POSSESSION_STATE),
            (workpiece, HAS_POSSESSED_WORKPIECE, state),
            (resource, HAS_POSSESSOR, state),
        ]
    )
    return state


def explicit(db: GraphDB, subject: IRI, predicate: IRI) -> set:
    return {
        triple[2]
        for triple in db.triples_get(
            sub=subject, pred=predicate, include_implicit=False
        )
    }


def change_of_possession(
    workpiece: IRI, receiver: IRI, state: IRI, receiver_keeps: list[IRI]
) -> list:
    return [
        Create(class_iri=POSSESSION_STATE, data={}, instance_iri=state),
        Update(
            instance_iri=workpiece,
            data={str(HAS_POSSESSED_WORKPIECE): [{"id": str(state)}]},
        ),
        Update(
            instance_iri=receiver,
            data={
                str(HAS_POSSESSOR): [
                    {"id": str(iri)} for iri in [*receiver_keeps, state]
                ]
            },
        ),
    ]


class TestAChangeOfPossession:
    @pytest.fixture
    def changed(self, db, live_ogm):
        box, giver, receiver = fresh("box"), fresh("giver"), fresh("receiver")
        old_state = possess(db, giver, box)
        db.triples_add([(receiver, RDF_TYPE, RESOURCE)])
        new_state = fresh("state")

        with patch.object(db, "query", wraps=db.query) as spy:
            nodes = live_ogm.commit(
                changes=change_of_possession(
                    box, receiver, new_state, receiver_keeps=[]
                )
            )
        updates = [call for call in spy.call_args_list if call.kwargs.get("update")]
        return box, giver, receiver, old_state, new_state, nodes, updates

    def test_is_one_update_request(self, changed):
        """Every change names the new state. The interface may send further update requests of
        its own, such as the removal of a write receipt, but none of them carries a change.
        """
        *_, new_state, _, updates = changed
        texts = [call.kwargs.get("query") or call.args[0] for call in updates]
        assert len([text for text in texts if str(new_state) in text]) == 1

    def test_makes_the_new_state(self, db, changed):
        _, _, _, _, new_state, _, _ = changed
        assert explicit(db, new_state, RDF_TYPE) >= {POSSESSION_STATE}

    def test_re_points_the_workpiece(self, db, changed):
        box, _, _, _, new_state, _, _ = changed
        assert explicit(db, box, HAS_POSSESSED_WORKPIECE) == {new_state}

    def test_links_the_receiver_to_the_new_state(self, db, changed):
        _, _, receiver, _, new_state, _, _ = changed
        assert explicit(db, receiver, HAS_POSSESSOR) == {new_state}

    def test_leaves_an_instance_the_call_does_not_name_unchanged(self, db, changed):
        _, giver, _, old_state, _, _, _ = changed
        assert explicit(db, giver, HAS_POSSESSOR) == {old_state}

    def test_returns_the_three_nodes(self, changed):
        box, _, receiver, _, new_state, nodes, _ = changed
        assert [node.id for node in nodes] == [new_state, box, receiver]


class TestARefusedChangeOfPossession:
    """The receiver already possesses a workpiece, so its part breaks the shape. The store
    refuses the whole write, and the two parts that were valid are not written either.
    """

    @pytest.fixture
    def refused(self, db, live_ogm, one_workpiece_per_resource):
        box, giver, receiver = fresh("box"), fresh("giver"), fresh("receiver")
        old_state = possess(db, giver, box)
        held = possess(db, receiver, fresh("other_box"))
        new_state = fresh("state")

        error = None
        try:
            live_ogm.commit(
                changes=change_of_possession(
                    box, receiver, new_state, receiver_keeps=[held]
                )
            )
        except TripleStoreInterfaceError as refusal:
            error = refusal
        if error is None:
            pytest.fail(
                "The store admitted a write that the shape forbids. Is SHACL validation on in "
                "the test repository? SHACL needs the repository's store to be the delegate "
                "of an rdf4j:ShaclSail. A graphdb:isShacl line in a Turtle config does not "
                "enable it: recreate the repository with that wrapper."
            )
        return box, giver, receiver, old_state, held, new_state, error

    def test_raises_the_store_refusal_of_the_shape(self, refused):
        *_, error = refused
        assert "MaxCountConstraintComponent" in str(error)

    def test_does_not_make_the_new_state(self, db, refused):
        _, _, _, _, _, new_state, _ = refused
        assert not db.iri_exists(new_state, as_sub=True, include_implicit=False)

    def test_does_not_re_point_the_workpiece(self, db, refused):
        box, _, _, old_state, _, _, _ = refused
        assert explicit(db, box, HAS_POSSESSED_WORKPIECE) == {old_state}

    def test_does_not_link_the_receiver(self, db, refused):
        _, _, receiver, _, held, _, _ = refused
        assert explicit(db, receiver, HAS_POSSESSOR) == {held}

    def test_leaves_an_instance_the_call_does_not_name_unchanged(self, db, refused):
        _, giver, _, old_state, _, _, _ = refused
        assert explicit(db, giver, HAS_POSSESSOR) == {old_state}


class TestTheExistenceCheckOnAStore:
    """A ``Create`` is refused only for an IRI with explicit statements. An IRI that is only the
    object of a statement has inferred statements, from an ``rdfs:range``, and is still new. The
    store checks it inside the write.
    """

    def test_an_iri_with_only_inferred_statements_is_created(self, db, live_ogm):
        workpiece, state = fresh("box"), fresh("state")
        db.triples_add(
            [
                (workpiece, RDF_TYPE, WORKPIECE),
                (workpiece, HAS_POSSESSED_WORKPIECE, state),
            ]
        )
        assert db.iri_exists(state, as_sub=True, include_implicit=True)
        assert not db.iri_exists(state, as_sub=True, include_implicit=False)

        live_ogm.commit(changes=[new_state(state)])

        assert explicit(db, state, RDF_TYPE) >= {POSSESSION_STATE}

    def test_an_iri_with_explicit_statements_is_refused_and_nothing_is_written(
        self, db, live_ogm
    ):
        existing = possess(db, fresh("giver"), fresh("box"))
        other = fresh("state")

        with pytest.raises(PreconditionFailedError):
            live_ogm.commit(changes=[new_state(other), new_state(existing)])

        assert not db.iri_exists(other, as_sub=True, include_implicit=False)


SINCE = IRI(NS + "since")
TRANSPORT = IRI(NS + "Transport")
CARRIES = IRI(NS + "carries")

# A value on the state, and a transport that links a workpiece, so that a change can write about
# an instance it does not name.
LINKS = [
    (SINCE, RDF_TYPE, IRI("owl:DatatypeProperty")),
    (SINCE, IRI("rdfs:domain"), POSSESSION_STATE),
    (SINCE, IRI("rdfs:range"), IRI("xsd:string")),
    (TRANSPORT, RDF_TYPE, IRI("owl:Class")),
    (CARRIES, RDF_TYPE, IRI("owl:ObjectProperty")),
    (CARRIES, IRI("rdfs:domain"), TRANSPORT),
    (CARRIES, IRI("rdfs:range"), WORKPIECE),
]


class TestTwoChangesThatWriteOneInstance:
    """A change writes about the instance it names and about every named instance that its data
    nests with properties. Each change is compared with the stored state on its own, so two
    changes that write one instance would each apply their own diff. Types do not count: a link
    that is typed by its range writes the same kind of statement as the instance's own ``Create``.
    """

    @pytest.fixture
    def linking_ogm(self, db, live_ogm):
        db.triples_add(LINKS, check_exist=False)
        return live_ogm

    def test_a_nested_instance_with_data_is_refused_and_nothing_is_written(
        self, db, linking_ogm
    ):
        receiver, state = fresh("receiver"), fresh("state")
        db.triples_add([(receiver, RDF_TYPE, RESOURCE)])

        with pytest.raises(ValueError, match="Two changes write") as caught:
            linking_ogm.commit(
                changes=[
                    Create(
                        class_iri=POSSESSION_STATE,
                        data={str(SINCE): ["10:00"]},
                        instance_iri=state,
                        class_scope=ClassScope.from_property_chains([[SINCE]]),
                    ),
                    Update(
                        instance_iri=receiver,
                        data={
                            str(HAS_POSSESSOR): [
                                {"id": str(state), str(SINCE): ["10:05"]}
                            ]
                        },
                    ),
                ]
            )

        assert str(state) in str(caught.value)
        assert not db.iri_exists(state, as_sub=True, include_implicit=False)
        assert explicit(db, receiver, HAS_POSSESSOR) == set()

    def test_a_link_to_an_instance_another_change_creates_is_admitted(
        self, db, linking_ogm
    ):
        transport, box, state = fresh("transport"), fresh("box"), fresh("state")

        nodes = linking_ogm.commit(
            changes=[
                Create(
                    class_iri=TRANSPORT,
                    data={str(CARRIES): [{"id": str(box)}]},
                    instance_iri=transport,
                    class_scope=ClassScope.from_property_chains(
                        [[CARRIES, HAS_POSSESSED_WORKPIECE]]
                    ),
                ),
                Create(
                    class_iri=WORKPIECE,
                    data={str(HAS_POSSESSED_WORKPIECE): [{"id": str(state)}]},
                    instance_iri=box,
                    class_scope=ClassScope.from_property_chains(
                        [[HAS_POSSESSED_WORKPIECE]]
                    ),
                ),
                new_state(state),
            ]
        )

        # The data gives the box by its IRI alone, so the transport's change only links it,
        # even though its scope hydrates the box's class. The box's own Create types it.
        transport_triples = nodes[0].to_triples(links_from_data=True)
        assert {s for s, _, _ in transport_triples} == {transport}
        assert explicit(db, box, RDF_TYPE) >= {WORKPIECE}
        assert explicit(db, transport, CARRIES) == {box}
        assert explicit(db, box, HAS_POSSESSED_WORKPIECE) == {state}


class TestATripleOutsideTheNamedGraph:
    """A change can remove a triple that the write does not find in ``named_graph``. The store
    refuses the whole call, so it raises, and nothing of the call is written."""

    def test_raises_and_nothing_of_the_call_is_written(self, db, live_ogm):
        box, giver, receiver = fresh("box"), fresh("giver"), fresh("receiver")
        old_state = possess(db, giver, box)
        db.triples_add([(receiver, RDF_TYPE, RESOURCE)])
        state = fresh("state")

        with pytest.raises(PreconditionFailedError) as caught:
            live_ogm.commit(
                changes=change_of_possession(box, receiver, state, receiver_keeps=[]),
                named_graph=IRI(NS + "another_graph"),
            )

        assert caught.value.named_graph == IRI(NS + "another_graph")
        assert not db.iri_exists(state, as_sub=True, include_implicit=False)
        assert explicit(db, box, HAS_POSSESSED_WORKPIECE) == {old_state}
        assert explicit(db, receiver, HAS_POSSESSOR) == set()
