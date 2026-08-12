from __future__ import annotations

from typing import TYPE_CHECKING

from kapps_triplestore_interface import IRI
from kapps_triplestore_interface.utils.types import IRILike

if TYPE_CHECKING:
    from kapps_ogm.node.core import Node


class ClassScope(dict[IRI, "ClassScope"]):
    def __setitem__(self, key: IRI, value: ClassScope):
        key = IRI(key)
        value = ClassScope(value or {})
        super(ClassScope, self).__setitem__(key, value)

    @classmethod
    def from_node_data(cls, node: "Node") -> ClassScope:
        from kapps_ogm.node.core import Node

        def chains_from_node_data(node: Node) -> list[list[IRI]]:
            if not isinstance(node, Node) or not node.data:
                return [[]]

            property_chains = []
            for property_iri, nested_nodes in node.data.items():
                if not nested_nodes:
                    # Empty-valued property: still include it as a leaf chain so the
                    # derived scope covers it. Without this, a commit that clears a
                    # property (data value == []) would derive a scope that omits the
                    # property, fetch the old state without it, and therefore never
                    # diff it away — making property removal impossible.
                    property_chains.append([property_iri])
                    continue
                for nested_node in nested_nodes:
                    nested_chains = chains_from_node_data(nested_node)
                    for chain in nested_chains:
                        property_chains.append([property_iri] + chain)

            return property_chains

        chains = chains_from_node_data(node)
        return cls.from_property_chains(chains)

    @classmethod
    def from_data_dict(cls, data: dict) -> ClassScope:
        from kapps_ogm.node.core import Node

        node = Node(data=data)
        return cls.from_node_data(node)

    @classmethod
    def from_property_chains(cls, property_chains: list[list[IRILike]]) -> ClassScope:
        # sort property chains by first element

        next_property_chains: dict[IRI, list[list[IRILike]]] = {}
        for chain in property_chains:
            next_property_iri = IRI(chain[0])
            next_property_chains.setdefault(next_property_iri, [])
            if len(chain) > 1:
                next_property_chains[next_property_iri].append(chain[1:])

        root = cls()
        for property_iri, chains in next_property_chains.items():
            root[property_iri] = cls.from_property_chains(chains)

        return root

    def to_property_chains(self) -> list[list[IRI]]:
        """
        Convert the ClassScope to a list of property chains.

        Returns:
            list[list[IRI]]: The property chains representing the ClassScope.
        """
        property_chains = []

        for property_iri, nested_scope in self.items():
            child_chains = nested_scope.to_property_chains()
            if not child_chains:
                property_chains.append([property_iri])
            else:
                for chain in child_chains:
                    property_chains.append([property_iri] + chain)

        return property_chains
