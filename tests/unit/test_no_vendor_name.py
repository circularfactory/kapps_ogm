"""No vendor name in the package, its docstrings, the README or the docs site.

The triple store interface is the only layer that knows which store runs behind it. So the OGM
names no vendor in what it publishes: its package, the metadata that the package index shows (the
`[project]` table of `pyproject.toml`), the README, and the sources of the docs site, which renders
the README and the package's docstrings.

**A vendor name** is a name that tells which triple store product runs behind the interface:

- `GraphDB`, the store, and `Ontotext`, its maker, in any case and in any word, so also a
  `GRAPHDB_*` variable, a `graphdb` host or prefix and the class `GraphDB`;
- `RDF4J`, the framework that the store builds on, in any case, so also the IRI of the graph that
  an RDF4J store reads its SHACL shapes from, `http://rdf4j.org/schema/rdf4j#SHACLShapeGraph`;
- `SHACLShapeGraph`, the local name of that IRI, in any case, so also the IRI under another prefix;
- `GDB` at the start of a word, the store's own token authentication, as in `GDBTokenAuth`.

**The allowlist** names, for one file, the exact text that the file may hold, and nothing else in
it: `GDBTokenAuth` in the README, whose example logs in with the store's token provider, because a
store may refuse Basic authentication. Any other vendor name in the README still fails, also
`GDB` in another word.
"""

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

VENDOR_NAME = re.compile(
    r"(?i:graphdb|ontotext|rdf4j|shaclshapegraph)|\bGDB",
)

# The exact texts that one file may hold, by its path from the repository's root.
ALLOWED = {
    "README.md": ("GDBTokenAuth",),
}

# What the check reads: every file of the package, the README, and every text file of the docs
# site's sources.
SCOPES = {
    "the package": sorted((ROOT / "src" / "kapps_ogm").rglob("*")),
    "the README": [ROOT / "README.md"],
    "the docs site": sorted((ROOT / "docs" / "site").rglob("*")),
}


def names_a_vendor(line: str, allowed: tuple[str, ...] = ()) -> bool:
    """Whether ``line`` names a vendor outside the ``allowed`` texts, each a whole word."""
    for text in allowed:
        line = re.sub(rf"\b{re.escape(text)}\b", "", line)
    return bool(VENDOR_NAME.search(line))


def vendor_names(paths: list[Path]) -> list[str]:
    """Each line of a text file in ``paths`` that names a vendor, as ``path:line: text``."""
    found = []
    for path in paths:
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # An image of the docs site.
        allowed = ALLOWED.get(path.relative_to(ROOT).as_posix(), ())
        for number, line in enumerate(text.splitlines(), start=1):
            if names_a_vendor(line, allowed):
                found.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    return found


def metadata_texts(value) -> list[str]:
    """Each string in ``value``, a value of the `[project]` table, however deep it is nested."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        value = [*value, *value.values()]
    if isinstance(value, list):
        return [text for item in value for text in metadata_texts(item)]
    return []


@pytest.mark.parametrize("scope", list(SCOPES))
def test_names_no_vendor(scope):
    assert SCOPES[scope], f"the check found no file in {scope}"
    assert vendor_names(SCOPES[scope]) == []


def test_the_metadata_names_no_vendor():
    with (ROOT / "pyproject.toml").open("rb") as manifest:
        project = tomllib.load(manifest)["project"]
    texts = metadata_texts(project)

    assert "kapps-ogm" in texts
    assert [text for text in texts if VENDOR_NAME.search(text)] == []


@pytest.mark.parametrize(
    "text",
    [
        "a reachable GraphDB triplestore",
        "os.environ['GRAPHDB_URL']",
        "image: ontotext/graphdb:10.8.1",
        "an RDF4J store",
        "http://rdf4j.org/schema/rdf4j#SHACLShapeGraph",
        "shapes:SHACLShapeGraph",
        "auth = GDBTokenAuth.from_env()",
    ],
)
def test_the_check_finds_a_vendor_name(text):
    assert VENDOR_NAME.search(text)


@pytest.mark.parametrize(
    "text",
    [
        "a reachable triple store",
        "TRIPLESTORE_URL and TRIPLESTORE_DATASET",
        "the store's shapes registry, shapes_set",
        "auth = BasicAuth.from_env()",
        "onto the node about to be written",
    ],
)
def test_the_check_passes_a_neutral_text(text):
    assert not VENDOR_NAME.search(text)


def test_the_readme_may_name_the_token_provider():
    assert not names_a_vendor(
        "store = connect(endpoint, GDBTokenAuth.from_env())", ALLOWED["README.md"]
    )


@pytest.mark.parametrize(
    "text",
    [
        "GDBTokenAuth logs in for a GDB token",
        "GDBTokenAuth needs a reachable GraphDB",
        "GDBTokenAuthentication",
    ],
)
def test_the_readme_names_no_other_vendor_name(text):
    assert names_a_vendor(text, ALLOWED["README.md"])
