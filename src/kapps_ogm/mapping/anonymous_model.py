"""Pydantic base model for anonymous nodes behind COMPLEX properties.

The anonymous node carries its RDF address (a Skolem IRI) out of band in a PrivateAttr,
so that the address is reachable by the serializer but invisible in every projection.
See PRD requirement R4 and RDF 1.1 Concepts section 3.5.

Important: the private attribute is a mirror, not the source of truth. It does not
survive a dump-then-revalidate round trip. Node.data is the authoritative carrier.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, PrivateAttr, model_validator


class AnonymousNodeModel(BaseModel):
    """Base model for anonymous nodes behind COMPLEX properties.

    The RDF address (Skolem IRI) is stored out of band in _node_iri, ensuring it is
    absent from model_dump(), model_dump_json(), and model_json_schema(). This keeps
    the address invisible to northbound consumers while still allowing the OGM to
    track it for serialization purposes.
    """

    _node_iri: Optional[Any] = PrivateAttr(default=None)

    @model_validator(mode="wrap")
    @classmethod
    def _capture_address(cls, data: Any, handler: Any) -> Any:
        """Capture the 'id' key from incoming payload into _node_iri.

        When the payload is a mapping containing an 'id' key, the key is popped
        before delegation so the address never reaches field validation. The popped
        value is then assigned to _node_iri on the constructed instance.

        When the payload has no 'id' key, or is not a mapping (e.g. already a model
        instance during revalidation), delegation proceeds untouched and _node_iri
        remains at its default.
        """
        if isinstance(data, dict) and "id" in data:
            data_copy = dict(data)
            node_iri = data_copy.pop("id")
            instance = handler(data_copy)
            if isinstance(instance, AnonymousNodeModel):
                instance._node_iri = node_iri
            return instance
        return handler(data)
