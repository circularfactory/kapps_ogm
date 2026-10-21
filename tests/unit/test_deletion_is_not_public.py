"""Deletion is a stated decision: ``_delete`` raises and says why. The public ``delete`` is a
deprecated alias for one release: it warns and raises the same error."""

from unittest.mock import Mock

import pytest

from kapps_ogm.ogm import OGM

DECISION = (
    "An individual is never deleted. Retract its properties with commit(); "
    "the entry point is kept for a later extension."
)

# The public methods of the OGM. A new public name is a decision, so it is listed here.
PUBLIC_API = {
    "commit",
    "create",
    "create_blank_instance",
    "fetch",
    "get_class_spec",
}
DEPRECATED_API = {"delete"}


def test_the_public_api_lists_delete_as_deprecated():
    public = {
        name for name in dir(OGM) if not name.startswith("_") and callable(getattr(OGM, name))
    }
    assert public == PUBLIC_API | DEPRECATED_API


def test_private_delete_raises_with_the_decision():
    ogm = OGM.__new__(OGM)
    with pytest.raises(NotImplementedError) as info:
        ogm._delete(node=Mock())
    assert str(info.value) == DECISION


def test_public_delete_warns_and_raises_the_same_error():
    ogm = OGM.__new__(OGM)
    with pytest.warns(DeprecationWarning, match="deprecated"):
        with pytest.raises(NotImplementedError) as info:
            ogm.delete(node=Mock())
    assert str(info.value) == DECISION
