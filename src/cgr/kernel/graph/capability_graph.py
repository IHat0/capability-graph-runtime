"""Directed capability workflow graph."""

import heapq

from .capability_edge import CapabilityEdge
from .capability_node import CapabilityNode


class CapabilityGraph:
    """Mutable directed graph with deterministic traversal and ordering."""

    def __init__(self) -> None:
        self._nodes: dict[str, CapabilityNode] = {}
        self._edges: list[CapabilityEdge] = []

    def add_node(self, node: CapabilityNode) -> None:
        """Add a node, preserving insertion order."""
        if node.id in self._nodes:
            raise ValueError(f"Node '{node.id}' already exists.")
        self._nodes[node.id] = node

    def add_edge(self, edge: CapabilityEdge) -> None:
        """Add an edge whose endpoints already exist."""
        if edge.source_id not in self._nodes:
            raise ValueError(f"Source node '{edge.source_id}' does not exist.")
        if edge.target_id not in self._nodes:
            raise ValueError(f"Target node '{edge.target_id}' does not exist.")
        if edge in self._edges:
            raise ValueError("Edge already exists.")
        self._edges.append(edge)

    def get_node(self, node_id: str) -> CapabilityNode:
        """Return a node by identifier."""
        return self._nodes[node_id]

    def nodes(self) -> list[CapabilityNode]:
        """Return a copy of nodes in insertion order."""
        return list(self._nodes.values())

    def edges(self) -> list[CapabilityEdge]:
        """Return a copy of edges in insertion order."""
        return list(self._edges)

    def successors(self, node_id: str) -> list[CapabilityNode]:
        """Return outgoing target nodes in edge insertion order."""
        self.get_node(node_id)
        return [
            self._nodes[edge.target_id]
            for edge in self._edges
            if edge.source_id == node_id
        ]

    def predecessors(self, node_id: str) -> list[CapabilityNode]:
        """Return incoming source nodes in edge insertion order."""
        self.get_node(node_id)
        return [
            self._nodes[edge.source_id]
            for edge in self._edges
            if edge.target_id == node_id
        ]

    def has_cycle(self) -> bool:
        """Return whether the graph contains a directed cycle."""
        return len(self._topological_node_ids()) != len(self._nodes)

    def topological_order(self) -> list[CapabilityNode]:
        """Return nodes in deterministic topological order."""
        node_ids = self._topological_node_ids()
        if len(node_ids) != len(self._nodes):
            raise ValueError("Cannot topologically sort graph with cycles.")
        return [self._nodes[node_id] for node_id in node_ids]

    def _topological_node_ids(self) -> list[str]:
        """Return the acyclic prefix produced by Kahn's algorithm."""
        positions = {
            node_id: position for position, node_id in enumerate(self._nodes)
        }
        indegrees = {node_id: 0 for node_id in self._nodes}
        successors: dict[str, list[str]] = {
            node_id: [] for node_id in self._nodes
        }
        for edge in self._edges:
            indegrees[edge.target_id] += 1
            successors[edge.source_id].append(edge.target_id)

        ready = [
            (positions[node_id], node_id)
            for node_id, indegree in indegrees.items()
            if indegree == 0
        ]
        heapq.heapify(ready)
        ordered: list[str] = []
        while ready:
            _, node_id = heapq.heappop(ready)
            ordered.append(node_id)
            for target_id in successors[node_id]:
                indegrees[target_id] -= 1
                if indegrees[target_id] == 0:
                    heapq.heappush(
                        ready,
                        (positions[target_id], target_id),
                    )
        return ordered
