"""The live tier builds its store through `connect()`, from `TRIPLESTORE_*` and `--repository`.

`tests/conftest.py` takes the store's URL and the credentials from `TRIPLESTORE_URL`,
`TRIPLESTORE_USERNAME` and `TRIPLESTORE_PASSWORD`, and the dataset from `pytest --repository`
only, never from the environment. Then it refuses a target that it cannot tell is disposable,
unless `TRIPLESTORE_ALLOW_DESTRUCTIVE=1` says so, and only then does it call `connect()`.

These tests reach no store: `connect` is replaced by a recorder, so they run in every tier,
including `-m "not live"`. A test that sets no variable of the store would skip the live tier
rather than run it, so each variable is removed first, the deprecated `GRAPHDB_*` ones too.

`conftest` imports as a top-level module because `tests/` carries no `__init__.py`, so pytest
puts that directory on `sys.path`.
"""

import pytest

from kapps_triplestore_interface import TripleStoreEndpoint

import conftest

STORE_VARIABLES = {
    "TRIPLESTORE_URL": "http://localhost:7200",
    "TRIPLESTORE_USERNAME": "user",
    "TRIPLESTORE_PASSWORD": "secret",
}

EVERY_VARIABLE = (
    *STORE_VARIABLES,
    "TRIPLESTORE_DATASET",
    "TRIPLESTORE_ALLOW_DESTRUCTIVE",
    "GRAPHDB_URL",
    "GRAPHDB_USERNAME",
    "GRAPHDB_PASSWORD",
    "GRAPHDB_REPOSITORY",
    "GRAPHDB_ALLOW_DESTRUCTIVE",
)

SHARED_HOST = "https://graphdb.example.org"


@pytest.fixture
def connected(monkeypatch) -> list:
    """The calls of `connect()`, which returns a marker instead of a store."""
    for name in EVERY_VARIABLE:
        monkeypatch.delenv(name, raising=False)
    calls = []

    def recorder(endpoint, auth):
        calls.append((endpoint, auth))
        return "the connected store"

    monkeypatch.setattr(conftest, "connect", recorder)
    return calls


def set_store_variables(monkeypatch, **changes) -> None:
    for name, value in {**STORE_VARIABLES, **changes}.items():
        monkeypatch.setenv(name, value)


def test_the_store_comes_from_connect(monkeypatch, connected):
    set_store_variables(monkeypatch)

    store = conftest.live_store("OGM")

    assert store == "the connected store"
    [(endpoint, auth)] = connected
    assert endpoint == TripleStoreEndpoint(url="http://localhost:7200", dataset="OGM")
    assert repr(auth) == "GDBTokenAuth(username='user')"


def test_the_dataset_comes_from_the_command_line_and_not_the_environment(
    monkeypatch, connected
):
    set_store_variables(monkeypatch, TRIPLESTORE_DATASET="Production")

    conftest.live_store("OGM")

    [(endpoint, _)] = connected
    assert endpoint.dataset == "OGM"


@pytest.mark.parametrize("unset", list(STORE_VARIABLES))
def test_without_a_variable_of_the_store_the_live_tier_skips(
    monkeypatch, connected, unset
):
    set_store_variables(monkeypatch)
    monkeypatch.delenv(unset)

    with pytest.raises(pytest.skip.Exception, match=unset):
        conftest.live_store("OGM")

    assert connected == []


def test_the_deprecated_variables_alone_do_not_reach_a_store(monkeypatch, connected):
    monkeypatch.setenv("GRAPHDB_URL", "http://localhost:7200")
    monkeypatch.setenv("GRAPHDB_USERNAME", "user")
    monkeypatch.setenv("GRAPHDB_PASSWORD", "secret")

    with pytest.raises(pytest.skip.Exception):
        conftest.live_store("OGM")

    assert connected == []


def test_a_shared_host_is_refused_without_the_opt_in(monkeypatch, connected):
    set_store_variables(monkeypatch, TRIPLESTORE_URL=SHARED_HOST)

    with pytest.raises(pytest.fail.Exception, match="TRIPLESTORE_ALLOW_DESTRUCTIVE"):
        conftest.live_store("OGM")

    assert connected == []


def test_the_opt_in_admits_a_shared_host(monkeypatch, connected):
    set_store_variables(
        monkeypatch, TRIPLESTORE_URL=SHARED_HOST, TRIPLESTORE_ALLOW_DESTRUCTIVE="1"
    )

    conftest.live_store("OGM")

    [(endpoint, _)] = connected
    assert endpoint.url == SHARED_HOST


def test_the_old_opt_in_does_not_admit_a_shared_host(monkeypatch, connected):
    set_store_variables(monkeypatch, TRIPLESTORE_URL=SHARED_HOST)
    monkeypatch.setenv("GRAPHDB_ALLOW_DESTRUCTIVE", "1")

    with pytest.raises(pytest.fail.Exception):
        conftest.live_store("OGM")

    assert connected == []
