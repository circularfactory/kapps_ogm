import json
from typing import Any
from kapps_triplestore_interface import IRI


def _unline(obj: Any):
    """Recursively convert lined IRIs back to full IRIs and walk nested structures."""
    from ..node import Node

    # IRI objects
    if isinstance(obj, IRI):
        return str(obj)

    # Node objects: work on their data
    if isinstance(obj, Node):
        return _unline(obj.data)

    # Pydantic models
    if hasattr(obj, "model_dump"):
        return _unline(obj.model_dump())
    if hasattr(obj, "dict"):
        return _unline(obj.dict())

    # Dict: unline keys that are lined IRIs and recurse on values
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            try:
                key = str(IRI.from_lined(k)) if isinstance(k, str) else _unline(k)
            except Exception:
                key = _unline(k)
            out[key] = _unline(v)
        return out

    # Lists / tuples
    if isinstance(obj, (list, tuple)):
        return [_unline(x) for x in obj]

    return obj


class OGMEncoder(json.JSONEncoder):
    def default(self, obj):
        from ..node import Node

        if isinstance(obj, Node):
            return _unline(obj)
        if isinstance(obj, IRI):
            return str(obj)
        if isinstance(obj, set):
            return tuple(obj)
        if hasattr(obj, "model_dump"):
            return _unline(obj.model_dump())
        if hasattr(obj, "dict"):
            return _unline(obj.dict())

        return super().default(obj)
