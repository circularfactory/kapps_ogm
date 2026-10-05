"""Shared pytest fixtures.

The `db` fixture here is a **live** GraphDB, and `ogm` is built on it. That matters more than
it looks, because `tests/unit/` is not all mock-driven. `tests/unit/conftest.py` offers a
mocked pair (`mock_db`, `ogm_with_mock_db`), but a test that asks for plain `ogm` gets the live
one. 28 tests do, spread over three files -- `test_range_merge_across_subproperties.py` (23),
`test_property_spec_edge_cases.py` (4) and `test_node_errors.py` (1). They cannot be moved onto
the mock: they write real triples and rely on the store to resolve `rdfs:subPropertyOf*` and to
report an `rdfs:range` target as a class, which a `Mock(spec=GraphDB)` returning `[]` cannot
do. 16 more, in `test_commit_several_instances.py`, ask for `db` itself: they prove that the
store admits or refuses a write to several instances as a whole, and one of their classes needs
SHACL validation on in the repository: its store must be the delegate of an `rdf4j:ShaclSail`.
A local repository without that wrapper has no SHACL, so that class errors there: recreate the
repository with it. 5 more, in `test_owl_minimums_not_required.py`, read OWL restrictions from
the store and create and commit instances through them. 20 more, in
`test_single_valued_property.py`, read each single-valued declaration from the store and go
through `OGM.create`, `OGM.commit` and a materializing `OGM.fetch`. 8 more, in
`test_create_and_commit_report_the_truth.py`, make writes whose precondition is false in the
store, and check that the call raises and that the store keeps what it held.

The development repository's CI provisions a disposable GraphDB for them, so the whole suite
runs there. A checkout without a triple store skips those 77, which is a report of what was
not run rather than a claim that it passed.

`db` **wipes** the store it connects to, so it refuses any target it cannot tell is disposable.
See `is_disposable_target` below for what disposable means and for the one environment
variable that overrides it.
"""

import os
from urllib.parse import urlsplit

import pytest

from kapps_triplestore_interface import GraphDB, GraphDBCredentials

from kapps_ogm.ogm import OGM

# The suite wipes the graphs it connects to, so it names its repository itself rather than
# taking one from the environment, which may point at anything -- the same hazard
# `kapps_semantic_middleware` pinned its own repository for.
#
# This was already the effective behaviour and is now the stated one. The previous
# `REPOSITORY or os.getenv("GRAPHDB_REPOSITORY")` could never reach its second half, because
# the constant is always truthy: the variable was demanded by the guard below and then ignored.
# That dead demand is what made the `GRAPHDB_REPOSITORY` / `GRAPHDB_TEST_REPOSITORY` split
# between this repository and `kapps_triplestore_interface` look like a live disagreement.
REPOSITORY = "OGM"

REQUIRED_ENV = ("GRAPHDB_URL", "GRAPHDB_USERNAME", "GRAPHDB_PASSWORD")

# A fixture that destroys data must be **told** its target is disposable. It must not infer the
# permission from a URL being set, which is all `db` below did until now. Nothing was lost:
# the exported value has always been the designated test repository. But it was safe by habit
# rather than by construction, and one
# edited environment variable was the whole distance between that and a store somebody cares
# about.
#
# Disposable means one of exactly two things:
#
# - **The host is loopback, or a bare hostname carrying no dot.** A bare name is a container
#   alias -- `graphdb`, which is this project's CI service alias and the service name in
#   `kapps_semantic_middleware`'s compose file -- and it resolves only inside the network that
#   defines it. A store that other people share is always reached by a dotted name. The rule
#   needs no allowlist and no per-repository upkeep.
#
#   The institute's shared store is the dotted name this matters for, and it is deliberately
#   not written here: `tests/` ships, and check 4 in `scripts/release_checks.py` rejects a
#   private host anywhere in the public tree. The tests use `example.org` for the same reason.
# - **`GRAPHDB_ALLOW_DESTRUCTIVE=1` is set**, which is a person saying so in as many words.
#
# The opt-in is deliberately not named after any of the three variables on group
# `circular_factory` (`GRAPHDB_USER`, `GRAPHDB_PASSWORD`, `GITHUB_CIRCULAR_FACTORY_TOKEN`). A
# protected group variable outranks one declared in `.gitlab-ci.yml` and arrives only on
# protected refs, which is exactly how a job passes every merge request and then fails the
# moment it merges.
ALLOW_DESTRUCTIVE_ENV = "GRAPHDB_ALLOW_DESTRUCTIVE"

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


@pytest.fixture(scope="session")
def db() -> GraphDB:
    """A live GraphDB client from the environment, or skip.

    Skips rather than exits. The previous ``sys.exit(1)`` was not a skip but a hard collection
    failure: it reported 28 errors on a checkout whose only fault was having no triple store,
    and a real regression could not have been seen among them.
    """
    missing = [name for name in REQUIRED_ENV if os.getenv(name) is None]
    if missing:
        pytest.skip(f"needs a reachable GraphDB; unset: {', '.join(missing)}")

    url = os.getenv("GRAPHDB_URL")
    if not is_disposable_target(url) and os.getenv(ALLOW_DESTRUCTIVE_ENV) != "1":
        pytest.fail(
            f"refusing to clear graphs in repository {REPOSITORY!r} on {url!r}.\n"
            "This fixture wipes the default context, two named graphs and the SHACL shapes "
            "graph at session start, and "
            "that host is neither loopback nor a container alias, so nothing here can tell that "
            "it is disposable.\n"
            "Point GRAPHDB_URL at a local GraphDB or a service alias, or set "
            f"{ALLOW_DESTRUCTIVE_ENV}=1 to state that the target is disposable.",
            pytrace=False,
        )

    credentials = GraphDBCredentials(
        base_url=os.getenv("GRAPHDB_URL"),
        username=os.getenv("GRAPHDB_USERNAME"),
        password=os.getenv("GRAPHDB_PASSWORD"),
        repository=REPOSITORY,
    )

    db = GraphDB(credentials=credentials)

    for graph in [
        None,
        "http://example.org/named_graph",
        "http://example.org/local_named_graph",
    ]:
        assert db.clear_graph(graph)
    # A shape survives a clear of the default graph. A test that loads one removes it again,
    # but a run killed before that leaves it active, so the next session starts without it.
    db.query(
        "CLEAR SILENT GRAPH <http://rdf4j.org/schema/rdf4j#SHACLShapeGraph>",
        update=True,
    )

    return db


@pytest.fixture
def ogm(db: GraphDB) -> OGM:
    """An OGM instance backed by the **live** database.

    The mocked counterpart is `ogm_with_mock_db` in `tests/unit/conftest.py`. This docstring
    used to say "with a mocked database", which is what this one is not.
    """
    ogm = OGM(db=db)
    return ogm
