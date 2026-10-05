from kapps_triplestore_interface import IRI
from typing import Protocol


class LoaderStrategy(Protocol):
    def expand(self, iri: IRI) -> list[list[IRI]]:
        """Expand the given IRI into property chains for selective instance loading."""
        ...
