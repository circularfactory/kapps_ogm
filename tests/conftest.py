"""Shared pytest fixtures.

The `db` fixture here is a **live** triple store, and `ogm` is built on it. That matters more than
it looks, because `tests/unit/` is not all mock-driven. `tests/unit/conftest.py` offers a
mocked pair (`mock_db`, `ogm_with_mock_db`), but a test that asks for plain `ogm` gets the live
one. 28 tests do, spread over three files -- `test_range_merge_across_subproperties.py` (23),
`test_property_spec_edge_cases.py` (4) and `test_node_errors.py` (1). They cannot be moved onto
the mock: they write real triples and rely on the store to resolve `rdfs:subPropertyOf*` and to
report which IRIs are classes, which a `Mock(spec=TripleStore)` returning `[]` cannot do. 16 more,
in `test_commit_several_instances.py`, ask for `db` itself: they prove that the
store admits or refuses a write to several instances as a whole, and one of their classes needs
SHACL validation on in the dataset. A local dataset without SHACL validation refuses to take
shapes, so that class errors there: recreate the dataset with SHACL validation on. 5 more, in
`test_owl_minimums_not_required.py`, read OWL restrictions from
the store and create and commit instances through them. 20 more, in
`test_single_valued_property.py`, read each single-valued declaration from the store and go
through `OGM.create`, `OGM.commit` and a materializing `OGM.fetch`. 8 more, in
`test_create_and_commit_report_the_truth.py`, make writes whose precondition is false in the
store, and check that the call raises and that the store keeps what it held. 3 more, in
`test_suite_shapes.py`, check that the suite's SHACL shape comes and goes through the store's
shapes registry, and that a load replaces the shapes before it. 6 more, in
`test_explicit_statements_only.py`, check that a fetched anonymous node
holds only its asserted statements, and which properties a class gets through its domains, its
superclasses and a union domain. 6 more, in `test_full_hydration_cycles.py`, specify classes
that their properties lead back to at the FULL level, and fetch through such a cycle with a
class scope. 1 more, in `test_append_one_value.py`, appends to one property of one instance
from two threads at once, and checks that both values land. 2 more, in
`test_superclass_scope_covers_later_subclass.py`, declare a subclass after a class scope exists
and hydrate its instance under that scope. 5 more, in
`test_refused_write_leaves_the_store_unchanged.py`, refuse a write that names an undeclared
property and check that the store keeps what it held.

The development repository's CI provisions a disposable store for them, so the whole suite
runs there. A checkout without a triple store skips those 100, which is a report of what was
not run rather than a claim that it passed.

**The store comes from `connect()`** of the triple store interface, and from nowhere else. Its
URL and the credentials come from `TRIPLESTORE_URL`, `TRIPLESTORE_USERNAME` and
`TRIPLESTORE_PASSWORD`. Without one of the three, the live tier skips, also when the
interface's deprecated fallbacks of these variables are set: they are never read.

**The dataset is named on the command line, never read from the environment**:
`pytest --repository <name>`. It defaults to `OGM`, the dataset CI creates. `TRIPLESTORE_DATASET`
is not read, whatever it names.

`db` **wipes** three graphs of the dataset it connects to at session start: the default
graph and two named graphs under `example.org`. On a dataset that other code also uses,
whatever that code keeps in those graphs is gone after a run, and that code must load it again.
Check that nothing runs on the dataset before a run. Every other named graph is left alone.

**`db` also removes every SHACL shape of the dataset**, at session start and after each test that
loads one. The suite sets its shapes through the store's shapes registry, `shapes_set`, which
replaces all the shapes of the dataset in one write; it cannot remove only its own. So the shapes
that other code loaded into the dataset are gone after a run, and that code must load them again.
A dataset without SHACL validation has no shapes to remove, and the session start leaves it as it
is.

The institute's shared throwaway dataset `Tests` is such a dataset: other projects run on it and
keep their data and shapes in it. A run with `--repository Tests` clears the three graphs above
and removes the shapes in its shapes registry, and it leaves every other named graph as it was.
After such a run, other projects must seed their data and shapes again.

`db` refuses any target it cannot tell is disposable. See `is_disposable_target` below for what
disposable means and for the one environment variable that overrides it.
"""

import os
from urllib.parse import urlsplit

import pytest

from kapps_triplestore_interface import (
    GDBTokenAuth,
    TripleStore,
    TripleStoreEndpoint,
    TripleStoreNotSupportedError,
    connect,
)

from kapps_ogm.ogm import OGM

# The suite wipes graphs of the dataset it connects to, so the dataset is named on the command
# line, by the person who runs the suite, and never taken from the environment, which may point
# at anything -- the hazard that a dataset named by the environment may be a shared one.
# The default is the dataset CI creates, so a run without the option runs where it always did.
DEFAULT_REPOSITORY = "OGM"

# The media type of the documents the suite gives the store's shapes registry.
SHAPES_TYPE = "text/turtle"

SHACL_PREFIX = "@prefix sh: <http://www.w3.org/ns/shacl#> .\n"

# The suite's SHACL shapes, each loaded for one test and removed after it, as Turtle without
# the prefix of `sh:`.

# A resource possesses at most one workpiece: `test_commit_several_instances.py`. Its target is
# that file's own class, so it cannot refuse a write of any other test.
ONE_WORKPIECE_PER_RESOURCE = """
    <http://example.org/several#ResourceShape> a sh:NodeShape ;
        sh:targetClass <http://example.org/several#Resource> ;
        sh:property <http://example.org/several#ResourceShape_hasPossessor> .
    <http://example.org/several#ResourceShape_hasPossessor> a sh:PropertyShape ;
        sh:path <http://example.org/several#hasPossessor> ;
        sh:maxCount 1 .
"""

# A stand-in for a shape that the suite does not own: `test_suite_shapes.py` checks that a load
# of the shape above replaces it. Its target is a class no other test writes, so it refuses
# nothing else.
STAND_IN_SHAPE = """
    <http://example.org/other#ThingShape> a sh:NodeShape ;
        sh:targetClass <http://example.org/other#Thing> ;
        sh:property <http://example.org/other#ThingShape_label> .
    <http://example.org/other#ThingShape_label> a sh:PropertyShape ;
        sh:path <http://example.org/other#label> ;
        sh:maxCount 1 .
"""


def load_shape(db: TripleStore, shape: str) -> None:
    """Make ``shape``, Turtle with the ``sh:`` prefix, the only SHACL shapes of the dataset.

    The store's shapes registry replaces every shape of the dataset, so each shape that was there
    before is gone afterwards, also one that other code loaded.
    """
    db.shapes_set(SHACL_PREFIX + shape, SHAPES_TYPE)


def remove_shapes(db: TripleStore) -> None:
    """Remove every SHACL shape of the dataset: the suite's, and each one that other code loaded."""
    db.shapes_set("", SHAPES_TYPE)


REQUIRED_ENV = ("TRIPLESTORE_URL", "TRIPLESTORE_USERNAME", "TRIPLESTORE_PASSWORD")

# A fixture that destroys data must be **told** its target is disposable. It must not infer the
# permission from a URL being set, which is all `db` below did until now. Nothing was lost:
# the exported value has always been the designated test dataset. But it was safe by habit
# rather than by construction, and one
# edited environment variable was the whole distance between that and a store somebody cares
# about.
#
# Disposable means one of exactly two things:
#
# - **The host is loopback, or a bare hostname carrying no dot.** A bare name is a container
#   alias -- `graphdb`, which is this project's CI service alias and a service name in
#   a compose file -- and it resolves only inside the network that
#   defines it. A store that other people share is always reached by a dotted name. The rule
#   needs no allowlist and no per-dataset upkeep.
#
#   The institute's shared store is the dotted name this matters for, and it is deliberately
#   not written here: `tests/` ships, and check 4 in `scripts/release_checks.py` rejects a
#   private host anywhere in the public tree. The tests use `example.org` for the same reason.
# - **`TRIPLESTORE_ALLOW_DESTRUCTIVE=1` is set**, which is a person saying so in as many words.
#   Only that exact variable, set to `1`, counts.
#
# The opt-in is deliberately not named after any of the three variables on group
# `circular_factory` (the shared store's user and password, and a GitHub token). A
# protected group variable outranks one declared in `.gitlab-ci.yml` and arrives only on
# protected refs, which is exactly how a job passes on every feature branch and then fails the
# moment it runs on the default branch.
ALLOW_DESTRUCTIVE_ENV = "TRIPLESTORE_ALLOW_DESTRUCTIVE"

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def is_disposable_target(url: str | None) -> bool:
    """Whether wiping the store at ``url`` is safe by construction rather than by habit.

    A URL this cannot parse a host out of is **not** disposable. The failure direction matters:
    every uncertain case must land on refusal, because the cost of a wrong `False` is a skipped
    test run and the cost of a wrong `True` is somebody else's data.
    """
    host = (urlsplit(url or "").hostname or "").lower()
    if not host:
        return False
    return host in LOOPBACK_HOSTS or "." not in host


def live_store(repository: str) -> TripleStore:
    """The store of the live tier, on the dataset ``repository``, or skip, or refuse.

    The URL and the credentials come from `TRIPLESTORE_*`, the dataset from ``repository``
    only. Skips rather than exits when a variable is missing. The previous ``sys.exit(1)`` was not
    a skip but a hard collection failure: it reported 28 errors on a checkout whose only fault was
    having no triple store, and a real regression could not have been seen among them. Refuses a
    target that `is_disposable_target` cannot tell is disposable, before `connect()` sends a
    request, unless `TRIPLESTORE_ALLOW_DESTRUCTIVE=1` is set.
    """
    missing = [name for name in REQUIRED_ENV if not os.getenv(name)]
    if missing:
        pytest.skip(f"needs a reachable triple store; unset: {', '.join(missing)}")

    endpoint = TripleStoreEndpoint.from_env(dataset=repository)
    if (
        not is_disposable_target(endpoint.url)
        and os.getenv(ALLOW_DESTRUCTIVE_ENV) != "1"
    ):
        pytest.fail(
            f"refusing to clear graphs in dataset {repository!r} on {endpoint.url!r}.\n"
            "This fixture wipes the default context and two named graphs at session start, and "
            "that host is neither loopback nor a container alias, so nothing here can tell that "
            "it is disposable.\n"
            "Point TRIPLESTORE_URL at a local store or a service alias, or set "
            f"{ALLOW_DESTRUCTIVE_ENV}=1 to state that the target is disposable.",
            pytrace=False,
        )

    return connect(endpoint, GDBTokenAuth.from_env())


def pytest_collection_modifyitems(items):
    """Mark every test that reaches the triple store as ``live``.

    Derived from the fixture graph rather than written on each test, so the marker cannot drift
    out of step with what a test actually needs. ``item.fixturenames`` is the resolved closure,
    so a test that requests `db` **through** `ogm` is marked as surely as one that names `db`
    itself. That transitive case is the one that gets missed by hand: it is why this project's
    CI carried a comment saying its test job needed no triplestore while 28 tests under
    `tests/unit/` needed one.

    ``-m "not live"`` then selects the tier that runs without a triple store. Nothing in CI
    passes that flag: CI has a triple store and runs the whole suite.
    """
    for item in items:
        if "db" in item.fixturenames:
            item.add_marker("live")


def pytest_addoption(parser):
    parser.addoption(
        "--repository",
        default=DEFAULT_REPOSITORY,
        help=(
            "The dataset the live tests clear graphs and shapes of and use "
            f"(default: {DEFAULT_REPOSITORY})"
        ),
    )


@pytest.fixture(scope="session")
def repository(request) -> str:
    """The dataset named on the command line."""
    return request.config.getoption("--repository")


@pytest.fixture(scope="session")
def db(repository: str) -> TripleStore:
    """The live store, from `live_store`, with three graphs and its SHACL shapes cleared."""
    db = live_store(repository)

    for graph in [
        None,
        "http://example.org/named_graph",
        "http://example.org/local_named_graph",
    ]:
        assert db.graph_clear(graph)
    # A shape survives a clear of the default graph. A test that loads one of the suite's shapes
    # removes the shapes again, but a run killed before that leaves its shape active, so the next
    # session starts without shapes. A dataset without SHACL validation holds none: only the tests
    # that load a shape need it, and they fail there.
    try:
        remove_shapes(db)
    except TripleStoreNotSupportedError:
        pass

    return db


@pytest.fixture
def ogm(db: TripleStore) -> OGM:
    """An OGM instance backed by the **live** store.

    The mocked counterpart is `ogm_with_mock_db` in `tests/unit/conftest.py`.
    """
    ogm = OGM(db=db)
    return ogm
