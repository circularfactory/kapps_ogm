"""Every ontology namespace the mapper ships or tests spells its scheme `http://`.

Web links stay `https://`. The guard matches a closed list of the project's namespace prefixes
anywhere in a shipped file, and any `https://` IRI inside a Turtle or SPARQL prefix declaration.

Named exceptions, each listed in `_EXEMPT`: the test that shows a stored `https` skolem IRI is still
recognised. Released CHANGELOG sections are history and stay as written. External vocabularies whose
own namespace is `https`, web URLs and plain links match no prefix of the list, so they pass.

The pattern is assembled from pieces, so that this file does not contain the strings it forbids.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

_SCHEME = "https" + "://"

_NAMESPACES = re.compile(
    _SCHEME
    + "(?:"
    + "|".join(
        [
            r"w3id\.org/circularfactory",
            r"(?:www\.)?sfb1574\.kit\.edu/(?:ontologies|\.well-known)",
            r"ex\.org\b",
            r"example\.org\b",
        ]
    )
    + ")"
)
_PREFIX_DECLARATION = re.compile(r"(?<![\w@])(?:@prefix|prefix)\b[^<\n]*<" + _SCHEME, re.IGNORECASE)

# (file, text that must be on the line): the stored-https skolem IRI that the recognition test needs.
_EXEMPT = [
    ("tests/unit/test_skolem_identity.py", _SCHEME + "w3id.org/circularfactory/.well-known/genid/ab12"),
]

_SUFFIXES = {".py", ".md", ".toml", ".json", ".ttl", ".rst", ".txt", ".html", ".yml", ".yaml"}
_TREES = ["src", "tests", "tests_release", "scripts", "docs/site", "release/probes"]
_FILES = ["README.md", "CONTEXT.md", "pyproject.toml"]


def _unreleased_changelog() -> str:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    start = text.index("## Unreleased")
    following = re.search(r"^## ", text[start + 1 :], re.MULTILINE)
    end = start + 1 + following.start() if following else len(text)
    return text[start:end]


def _shipped_texts() -> list[tuple[str, str]]:
    texts = [("CHANGELOG.md (Unreleased)", _unreleased_changelog())]
    paths = [ROOT / f for f in _FILES if (ROOT / f).is_file()]
    for tree in _TREES:
        paths += [
            p
            for p in sorted((ROOT / tree).rglob("*"))
            if p.is_file() and p.suffix in _SUFFIXES and "__pycache__" not in p.parts
        ]
    texts += [(str(p.relative_to(ROOT)), p.read_text(encoding="utf-8")) for p in paths]
    return texts


def _exempt(name: str, line: str) -> bool:
    return any(name == file and fragment in line for file, fragment in _EXEMPT)


def _offences(name: str, text: str) -> list[str]:
    return [
        f"line {i}: {line.strip()[:100]}"
        for i, line in enumerate(text.splitlines(), 1)
        if (_NAMESPACES.search(line) or _PREFIX_DECLARATION.search(line)) and not _exempt(name, line)
    ]


@pytest.mark.parametrize("name,text", _shipped_texts(), ids=lambda v: v if len(v) < 80 else "")
def test_ontology_namespaces_use_http(name: str, text: str) -> None:
    hits = _offences(name, text)
    assert not hits, f"{name} spells an ontology namespace with the secure scheme:\n" + "\n".join(hits)


@pytest.mark.parametrize(
    "line",
    [
        "x = " + _SCHEME + "example.org/thing",
        "@prefix ex: <" + _SCHEME + "other.example/ns#> .",
        "PREFIX ex: <" + _SCHEME + "other.example/ns#>",
        _SCHEME + "www.sfb1574.kit.edu/ontologies/TransferUnit#hasUnit",
    ],
)
def test_the_guard_refuses_an_https_ontology_namespace(line: str) -> None:
    assert _offences("somewhere.py", line)


@pytest.mark.parametrize(
    "line",
    [
        "see " + _SCHEME + "graphdb.example.org/",
        "[docs](" + _SCHEME + "github.com/circularfactory/kapps_ogm)",
        "@prefix ex: <http://example.org/ns#> .",
        "[CRC 1574](" + _SCHEME + "www.sfb1574.kit.edu/english/index.php)",
    ],
)
def test_the_guard_leaves_web_links_alone(line: str) -> None:
    assert not _offences("somewhere.py", line)
