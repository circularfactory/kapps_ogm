"""A deprecated name of the triple store interface is an error in this suite.

The interface keeps each name that it replaces for one release, as an alias that emits one
`DeprecationWarning` naming its successor. The package and the tests use only the new names, and
`filterwarnings` in `pyproject.toml` turns each of these warnings into an error, so a use of an old
name fails the test that makes it, also in the live tier.

The interface attributes each warning to the line of its caller, so the filter cannot select them
by module. It selects them by the form that every one of them has: `<old> is deprecated; use`,
`set` or `pass` `<successor>`. These tests cover each kind of old name: a renamed method, a
deprecated alias of an error type, the deprecated credentials, the deprecated flag and a
deprecated variable of the environment. None of them reaches a store.
"""

import pytest

import kapps_triplestore_interface
from kapps_triplestore_interface import BasicAuth, GraphDB, TripleStoreEndpoint
from kapps_triplestore_interface import exceptions


def unreached_adapter(**options) -> GraphDB:
    """The adapter, built without a request: each case raises before it sends one."""
    endpoint = TripleStoreEndpoint(url="http://localhost:1", dataset="unused")
    return GraphDB(endpoint=endpoint, auth=BasicAuth("user", "password"), **options)


def a_renamed_method():
    unreached_adapter().clear_graph()


def a_renamed_attribute():
    return unreached_adapter().repository


def an_alias_of_an_error_type():
    return exceptions.GraphDbException


def the_alias_of_the_dataset_error():
    return exceptions.InvalidRepositoryError


def the_deprecated_credentials():
    return getattr(kapps_triplestore_interface, "GraphDBCredentials")


def the_deprecated_token_flag():
    unreached_adapter(use_gdb_token=True)


@pytest.mark.parametrize(
    "use",
    [
        a_renamed_method,
        a_renamed_attribute,
        an_alias_of_an_error_type,
        the_alias_of_the_dataset_error,
        the_deprecated_credentials,
        the_deprecated_token_flag,
    ],
)
def test_a_deprecated_interface_name_raises(use):
    with pytest.raises(DeprecationWarning, match="is deprecated"):
        use()


def test_a_deprecated_variable_of_the_environment_raises(monkeypatch):
    monkeypatch.delenv("TRIPLESTORE_URL", raising=False)
    monkeypatch.setenv("GRAPHDB_URL", "http://localhost:1")

    with pytest.raises(DeprecationWarning, match="set TRIPLESTORE_URL"):
        TripleStoreEndpoint.from_env(dataset="unused")
