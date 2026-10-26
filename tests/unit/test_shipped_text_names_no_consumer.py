"""The mapper's shipped text names no project built with it and no publication about it.

Shipped means the source, the tests with their data, the README, CONTEXT.md, the site docs and the
Unreleased section of the CHANGELOG. Released CHANGELOG sections are history and stay as written.

The pattern is assembled from pieces, so that this file does not contain the words it forbids.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

_FORBIDDEN = re.compile(
    "|".join(
        [
            "j" + "ms",
            r"use[\s_-]?" + "case",
            r"\bU" + r"C[0-9]\b",
            r"\bpa" + r"per\b",
        ]
    ),
    re.IGNORECASE,
)

_SUFFIXES = {".py", ".md", ".toml", ".json", ".ttl", ".rst", ".txt", ".html", ".yml", ".yaml"}
_TREES = ["src", "tests", "docs/site"]
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


@pytest.mark.parametrize("name,text", _shipped_texts(), ids=lambda v: v if len(v) < 80 else "")
def test_shipped_text_names_no_consumer(name: str, text: str) -> None:
    hits = [
        f"line {i}: {line.strip()[:100]}"
        for i, line in enumerate(text.splitlines(), 1)
        if _FORBIDDEN.search(line)
    ]
    assert not hits, f"{name} names a consumer or a publication:\n" + "\n".join(hits)
