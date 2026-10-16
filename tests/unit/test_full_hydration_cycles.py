"""A class specification at the FULL level ends where a class repeats on its own path.

At the FULL level a class gets every property of its domains and superclasses, and an object
property nests the specification of its range class. A class that a property leads back to,
directly or through other classes, would nest itself without end. So the specification keeps the
classes on its path, from the root to the current class, and nests a class that is already on it
as an IRI-only link: a class specification that holds the class's IRI and no properties. Every
reader of a specification takes a nested class without properties as a link, as it takes the one
that a class scope gives a property it names without a nested scope. A class on two paths without a cycle is specified in full on
both. The properties are visited in sorted order, so the specification does not depend on the
hash seed.

The ontology has the three shapes: a class that is its own range, a cycle of two classes, and a
diamond, the same class on two paths. The same checks run on a stub of the store's reads, offline,
and on the store itself, live. Each reader of a specification -- the read path, the write path,
blank-instance generation and display -- takes a specification with an IRI-only link as it is.

Run as a script, this module prints the FULL specifications of the ontology's root classes, so a
test can compare two processes with different hash seeds.
"""

import os
import subprocess
import sys
import uuid
from unittest.mock import patch

import pytest
from rdflib import Literal

from kapps_triplestore_interface import (
    GDBTokenAuth,
    IRI,
    SPARQLQuery,
    TripleStore,
    TripleStoreEndpoint,
    XSDToPythonTypes,
    connect,
)

from kapps_ogm.mapping.class_spec import ClassHydrationLevel, ClassSpec
from kapps_ogm.node.core import Node
from kapps_ogm.ogm import OGM
from kapps_ogm.utils.class_scope import ClassScope

FULL = ClassHydrationLevel.FULL
RDF_TYPE = IRI("rdf:type")
RDFS_DOMAIN = IRI("rdfs:domain")
RDFS_RANGE = IRI("rdfs:range")
XSD_STRING = IRI("http://www.w3.org/2001/XMLSchema#string")
XSD_INTEGER = IRI("http://www.w3.org/2001/XMLSchema#integer")

STUB_NAMESPACE = "https://example.org/cycles#"

CLASSES = ("Task", "Workflow", "Service", "Cell", "Left", "Right", "Part")

# The root classes of the three shapes.
ROOTS = ("Task", "Workflow", "Service", "Cell")

# name: (kind, domain, range). A range is a class of the ontology or a datatype.
PROPERTIES = {
    # A class that is its own range.
    "hasReferenceTask": ("Object", "Task", "Task"),
    "taskName": ("Datatype", "Task", XSD_STRING),
    # A cycle of two classes.
    "hasService": ("Object", "Workflow", "Service"),
    "inWorkflow": ("Object", "Service", "Workflow"),
    # A diamond: a cell reaches a part through its left side and through its right side. The cell
    # has four properties, so the order of a set of them changes with the hash seed.
    "hasLeft": ("Object", "Cell", "Left"),
    "hasRight": ("Object", "Cell", "Right"),
    "cellName": ("Datatype", "Cell", XSD_STRING),
    "cellCode": ("Datatype", "Cell", XSD_INTEGER),
    "leftPart": ("Object", "Left", "Part"),
    "rightPart": ("Object", "Right", "Part"),
    "partName": ("Datatype", "Part", XSD_STRING),
    "partNumber": ("Datatype", "Part", XSD_INTEGER),
}

# Three tasks, each the reference task of the one before.
TASKS = ("t1", "t2", "t3")


def vocabulary(namespace: str) -> dict[str, IRI]:
    names = CLASSES + tuple(PROPERTIES) + TASKS
    return {name: IRI(namespace + name) for name in names}


def ontology_triples(v: dict[str, IRI]) -> list[tuple]:
    """The ontology and the task chain."""
    triples = [(v[name], RDF_TYPE, IRI("owl:Class")) for name in CLASSES]
    for name, (kind, domain, range_) in PROPERTIES.items():
        triples += [
            (v[name], RDF_TYPE, IRI(f"owl:{kind}Property")),
            (v[name], RDFS_DOMAIN, v[domain]),
            (v[name], RDFS_RANGE, v.get(range_, range_)),
        ]
    for task, reference in zip(TASKS, TASKS[1:] + (None,)):
        triples += [
            (v[task], RDF_TYPE, v["Task"]),
            (v[task], v["taskName"], Literal(task)),
        ]
        if reference is not None:
            triples.append((v[task], v["hasReferenceTask"], v[reference]))
    return triples


class StoreStub:
    """The store's answers to the reads of a class specification and of a fetch, for the
    ontology above. A query is answered by the one IRI of the ontology that it names. A write is
    recorded and admitted.
    """

    def __init__(self, v: dict[str, IRI]):
        self.v = v
        self.triples = ontology_triples(v)
        self.written: list[set] = []

    def triples_get(self, sub=None, pred=None, **_) -> list[tuple]:
        """The matching triples, with a literal as its python value, as the store gives it."""
        return [
            (s, p, o.toPython() if isinstance(o, Literal) else o)
            for s, p, o in self.triples
            if s == IRI(sub) and (pred is None or p == IRI(pred))
        ]

    def owl_get_classes_of_individual(self, instance_iri, **_) -> list[IRI]:
        return [o for s, p, o in self.triples_get(sub=instance_iri, pred=RDF_TYPE)]

    def iri_exists(self, iri, **_) -> bool:
        return any(IRI(iri) in (s, o) for s, _, o in self.triples)

    def is_subclass(self, instance_iri, class_iri) -> bool:
        """The validator asks whether a known instance is of its class."""
        return (IRI(instance_iri), RDF_TYPE, IRI(class_iri)) in self.triples

    def query(self, query, **_) -> dict:
        text = query.to_string() if isinstance(query, SPARQLQuery) else query
        if text.lstrip().startswith("ASK"):
            return {"boolean": False}
        if "owl#onProperty" in text:
            # The maximum counts of a class: the ontology has no restriction.
            return {"results": {"bindings": []}}
        [named] = [name for name, iri in self.v.items() if f"<{iri}>" in text]
        if named in CLASSES:
            # The property discovery of a class: the properties whose domain is the class.
            bindings = [
                {"property": self.v[name]}
                for name, (_, domain, _) in PROPERTIES.items()
                if domain == named
            ]
        else:
            # The ranges of a property: a class, or the python type of a datatype.
            _, _, range_ = PROPERTIES[named]
            bindings = [{"obj": self.v.get(range_) or XSDToPythonTypes[range_]}]
        return {"results": {"bindings": bindings}}

    def triples_add(self, triples, **_) -> bool:
        self.written.append(set(triples))
        return True


def iri_only_link(class_iri: IRI) -> ClassSpec:
    """What a FULL specification nests where a class repeats on its path."""
    return ClassSpec(iri=class_iri, hydration_level=ClassHydrationLevel.SCOPE)


def full(ogm: OGM, class_iri: IRI) -> ClassSpec:
    return ogm.get_class_spec(class_iri=class_iri, hydration_level=FULL)


def nested(spec: ClassSpec, *properties: IRI) -> ClassSpec:
    """The class specification at the end of a chain of object properties."""
    for prop in properties:
        spec = spec.properties[prop].nested
    return spec


def class_specs(spec: ClassSpec):
    """``spec`` and every class specification nested below it."""
    yield spec
    for prop in spec.properties.values():
        if prop.nested is not None:
            yield from class_specs(prop.nested)


def rendered_specs(ogm: OGM, v: dict[str, IRI]) -> str:
    """The FULL specifications of the root classes, every field and every order included."""
    return "\n".join(repr(full(ogm, v[root])) for root in ROOTS)


def specs_under_hash_seed(seed: str, backend: list[str]) -> str:
    result = subprocess.run(
        [sys.executable, __file__, *backend],
        env={**os.environ, "PYTHONHASHSEED": seed},
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return result.stdout


class CycleChecks:
    """The checks, on the store that the subclass's fixtures give."""

    def test_a_class_that_is_its_own_range_ends_at_the_second_class(
        self, cycle_ogm: OGM, v: dict[str, IRI]
    ):
        task = full(cycle_ogm, v["Task"])

        assert set(task.properties) == {v["hasReferenceTask"], v["taskName"]}
        assert nested(task, v["hasReferenceTask"]) == iri_only_link(v["Task"])

    def test_a_cycle_of_two_classes_ends_where_a_class_repeats(
        self, cycle_ogm: OGM, v: dict[str, IRI]
    ):
        workflow = full(cycle_ogm, v["Workflow"])
        service = full(cycle_ogm, v["Service"])

        assert nested(workflow, v["hasService"]).hydration_level is FULL
        assert nested(workflow, v["hasService"], v["inWorkflow"]) == iri_only_link(
            v["Workflow"]
        )
        assert nested(service, v["inWorkflow"]).hydration_level is FULL
        assert nested(service, v["inWorkflow"], v["hasService"]) == iri_only_link(
            v["Service"]
        )

    def test_a_class_on_two_paths_is_specified_in_full_on_both(
        self, cycle_ogm: OGM, v: dict[str, IRI]
    ):
        cell = full(cycle_ogm, v["Cell"])

        left = nested(cell, v["hasLeft"], v["leftPart"])
        right = nested(cell, v["hasRight"], v["rightPart"])

        for part in (left, right):
            assert part.hydration_level is FULL
            assert set(part.properties) == {v["partName"], v["partNumber"]}
        assert left == right

    def test_the_properties_of_each_class_are_in_sorted_order(
        self, cycle_ogm: OGM, v: dict[str, IRI]
    ):
        for root in ROOTS:
            for spec in class_specs(full(cycle_ogm, v[root])):
                assert list(spec.properties) == sorted(spec.properties)

    def test_the_specification_is_the_same_under_two_hash_seeds(
        self, backend: list[str]
    ):
        assert specs_under_hash_seed("1", backend) == specs_under_hash_seed(
            "2", backend
        )

    def test_a_scoped_fetch_follows_its_scope_through_the_cycle(
        self, cycle_ogm: OGM, v: dict[str, IRI]
    ):
        """A scope that names the cycle twice nests the class twice: the guard is for FULL."""
        scope = ClassScope.from_property_chains(
            [[v["hasReferenceTask"], v["hasReferenceTask"]]]
        )

        t1 = cycle_ogm.fetch(instance_iri=v["t1"], class_scope=scope)

        [t2] = t1.data[v["hasReferenceTask"]]
        [t3] = t2.data[v["hasReferenceTask"]]
        assert (t2.id, t3.id) == (v["t2"], v["t3"])
        assert t3.data == {}
        assert set(nested(t1.class_spec, v["hasReferenceTask"]).properties) == {
            v["hasReferenceTask"]
        }


class TestCyclesOnAStub(CycleChecks):
    """The checks on the stub, offline."""

    @pytest.fixture
    def v(self) -> dict[str, IRI]:
        return vocabulary(STUB_NAMESPACE)

    @pytest.fixture
    def cycle_ogm(self, v: dict[str, IRI]) -> OGM:
        return OGM(db=StoreStub(v))

    @pytest.fixture
    def backend(self) -> list[str]:
        return ["stub", STUB_NAMESPACE]


@pytest.fixture(scope="module")
def live_namespace(db: TripleStore) -> str:
    """The ontology and the task chain in the store, in a namespace no other test uses."""
    namespace = f"https://example.org/cycles_{uuid.uuid4().hex[:8]}#"
    assert db.triples_add(ontology_triples(vocabulary(namespace)), check_exist=False)
    return namespace


class TestCyclesOnTheStore(CycleChecks):
    """The checks on the store, live."""

    @pytest.fixture
    def v(self, live_namespace: str) -> dict[str, IRI]:
        return vocabulary(live_namespace)

    @pytest.fixture
    def cycle_ogm(self, ogm: OGM) -> OGM:
        return ogm

    @pytest.fixture
    def backend(self, live_namespace: str, repository: str) -> list[str]:
        return ["store", live_namespace, repository]


class TestTheReadersTakeAnIriOnlyLink:
    """The specification of a task, whose reference task is an IRI-only link, made by the OGM
    from the stub, goes through each reader of a specification."""

    @pytest.fixture
    def v(self) -> dict[str, IRI]:
        return vocabulary(STUB_NAMESPACE)

    @pytest.fixture
    def store(self, v: dict[str, IRI]) -> StoreStub:
        return StoreStub(v)

    @pytest.fixture
    def ogm(self, store: StoreStub) -> OGM:
        return OGM(db=store)

    @pytest.fixture
    def task(self, ogm: OGM, v: dict[str, IRI]) -> ClassSpec:
        task = full(ogm, v["Task"])
        assert nested(task, v["hasReferenceTask"]) == iri_only_link(v["Task"])
        return task

    def test_the_read_path(self, ogm: OGM, v: dict[str, IRI], task: ClassSpec):
        t1 = ogm.fetch(instance_iri=v["t1"], class_spec=task, materialize=True)

        [t2] = t1.data[v["hasReferenceTask"]]
        assert isinstance(t2, Node) and t2.id == v["t2"]
        [t2_model] = getattr(t1.instance, v["hasReferenceTask"].lined)
        assert t2_model.model_dump() == {"id": v["t2"]}

    @pytest.mark.parametrize("as_dict", [False, True], ids=["iri", "id-dict"])
    def test_the_write_path(
        self,
        ogm: OGM,
        store: StoreStub,
        v: dict[str, IRI],
        task: ClassSpec,
        as_dict: bool,
    ):
        new = IRI("https://example.org/cycles_instances#t0")
        reference = {"id": v["t1"]} if as_dict else v["t1"]

        with patch.object(ogm, "get_class_spec", return_value=task):
            ogm.create(
                class_iri=v["Task"],
                data={v["taskName"]: ["t0"], v["hasReferenceTask"]: [reference]},
                instance_iri=new,
            )

        [written] = store.written
        assert (new, v["hasReferenceTask"], v["t1"]) in written
        assert {s for s, _, _ in written} == {new}

    def test_blank_instance_generation(
        self, ogm: OGM, v: dict[str, IRI], task: ClassSpec
    ):
        with patch.object(ClassSpec, "specify", return_value=task):
            blank = ogm.create_blank_instance(
                class_iri=v["Task"], instance_iri=IRI("https://example.org/blank#t")
            )

        [link] = getattr(blank, v["hasReferenceTask"].lined)
        assert list(type(link).model_fields) == ["id"]

    def test_display(self, v: dict[str, IRI], task: ClassSpec):
        text = task.to_string()

        assert text.count(f"iri={v['Task']}") == 2
        assert text.count(f"iri={v['taskName']}") == 1


def _main(backend: str, namespace: str, *repository: str) -> None:
    v = vocabulary(namespace)
    if backend == "stub":
        db = StoreStub(v)
    else:
        # The live tier's store, as `tests/conftest.py` builds it: the dataset is the one that
        # the parent process got from `--repository`, the rest comes from `TRIPLESTORE_*`.
        [name] = repository
        db = connect(
            TripleStoreEndpoint.from_env(dataset=name), GDBTokenAuth.from_env()
        )
    print(rendered_specs(OGM(db=db), v))


if __name__ == "__main__":
    _main(*sys.argv[1:])
