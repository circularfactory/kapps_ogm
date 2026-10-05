"""The destructive-fixture guard.

These tests reach no triple store: they exercise the decision, not the wipe, so they run in
every tier including `-m "not live"`. That is deliberate. The guard is the whole distance
between `tests/conftest.py`'s session fixture and whatever `GRAPHDB_URL` happens to name, and a
guard nobody exercises is a guard that quietly stops working.

`conftest` imports as a top-level module because `tests/` carries no `__init__.py`, so pytest
puts that directory on `sys.path`.
"""

import pytest

from conftest import is_disposable_target


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:7200",
        "http://127.0.0.1:7200",
        "http://[::1]:7200",
        # The CI service alias, and the service name in kapps_semantic_middleware's compose
        # file. A bare name resolves only inside the network that defines it.
        "http://graphdb:7200",
        "http://graphdb:7200/repositories/OGM",
    ],
)
def test_a_disposable_target_is_accepted(url):
    assert is_disposable_target(url)


@pytest.mark.parametrize(
    "url",
    [
        # A shared store on a real domain. This is the case the guard exists for. The
        # institute's own hostname is not written here on purpose: `tests/` ships, and check 4
        # in `scripts/release_checks.py` rejects a private host anywhere in the public tree.
        "https://graphdb.example.org",
        "https://graphdb.example.org/",
        "http://graphdb.example.org:7200",
        # A dotted name is a dotted name, whoever owns it.
        "http://192.168.1.10:7200",
        "http://db.internal:7200",
    ],
)
def test_a_shared_host_is_refused(url):
    assert not is_disposable_target(url)


@pytest.mark.parametrize("url", ["", None, "not a url", "http://", "://7200"])
def test_an_unparseable_target_is_refused(url):
    """Every uncertain case lands on refusal.

    A wrong `False` costs a skipped test run. A wrong `True` costs somebody else's data.
    """
    assert not is_disposable_target(url)
