import os
import sys
import pytest

from kapps_triplestore_interface import GraphDB, GraphDBCredentials

from kapps_ogm.ogm import OGM

REPOSITORY = "OGM"


@pytest.fixture(scope="session")
def db() -> GraphDB:
    """Fixture to create a GraphDB client."""
    for env_var in [
        "GRAPHDB_URL",
        "GRAPHDB_USERNAME",
        "GRAPHDB_PASSWORD",
        "GRAPHDB_REPOSITORY",
    ]:
        if os.getenv(env_var) is None:
            print(f"Missing environment variable '{env_var}'.", file=sys.stderr)
            sys.exit(1)

    credentials = GraphDBCredentials(
        base_url=os.getenv("GRAPHDB_URL"),
        username=os.getenv("GRAPHDB_USERNAME"),
        password=os.getenv("GRAPHDB_PASSWORD"),
        repository=REPOSITORY or os.getenv("GRAPHDB_REPOSITORY"),
    )

    db = GraphDB(credentials=credentials)

    for graph in [
        None,
        "http://example.org/named_graph",
        "http://example.org/local_named_graph",
    ]:
        assert db.clear_graph(graph)

    return db


@pytest.fixture
def ogm(db: GraphDB) -> OGM:
    """Create an OGM instance with a mocked database."""
    ogm = OGM(db=db)
    return ogm
