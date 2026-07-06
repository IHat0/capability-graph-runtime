import pytest
from pydantic import ValidationError

from cgr.kernel.graph import CapabilityEdge, CapabilityGraph, CapabilityNode


def make_node(node_id: str) -> CapabilityNode:
    return CapabilityNode(
        id=node_id,
        capability_id=f"capability-{node_id}",
        label=f"Node {node_id}",
    )


def make_graph(*node_ids: str) -> CapabilityGraph:
    graph = CapabilityGraph()
    for node_id in node_ids:
        graph.add_node(make_node(node_id))
    return graph


def test_capability_node_is_immutable() -> None:
    node = make_node("one")

    with pytest.raises(ValidationError):
        node.label = "Changed"


@pytest.mark.parametrize(
    ("node_id", "capability_id", "label"),
    [
        ("", "echo", "Echo"),
        ("one", "", "Echo"),
        ("one", "echo", ""),
    ],
)
def test_capability_node_rejects_empty_required_fields(
    node_id: str,
    capability_id: str,
    label: str,
) -> None:
    with pytest.raises(ValidationError):
        CapabilityNode(
            id=node_id,
            capability_id=capability_id,
            label=label,
        )


def test_capability_edge_is_immutable() -> None:
    edge = CapabilityEdge(source_id="one", target_id="two")

    with pytest.raises(ValidationError):
        edge.label = "Changed"


@pytest.mark.parametrize(
    ("source_id", "target_id"),
    [("", "two"), ("one", "")],
)
def test_capability_edge_rejects_empty_endpoint(
    source_id: str,
    target_id: str,
) -> None:
    with pytest.raises(ValidationError):
        CapabilityEdge(source_id=source_id, target_id=target_id)


def test_capability_edge_rejects_self_edge() -> None:
    with pytest.raises(ValidationError):
        CapabilityEdge(source_id="one", target_id="one")


def test_add_node_preserves_insertion_order_and_rejects_duplicate() -> None:
    graph = make_graph("one", "two")

    assert [node.id for node in graph.nodes()] == ["one", "two"]
    with pytest.raises(ValueError, match="already exists"):
        graph.add_node(make_node("one"))


def test_add_edge_preserves_insertion_order() -> None:
    graph = make_graph("one", "two", "three")
    second = CapabilityEdge(source_id="two", target_id="three")
    first = CapabilityEdge(source_id="one", target_id="two")

    graph.add_edge(second)
    graph.add_edge(first)

    assert graph.edges() == [second, first]


def test_add_edge_rejects_missing_source() -> None:
    graph = make_graph("two")

    with pytest.raises(ValueError, match="Source node 'one' does not exist"):
        graph.add_edge(CapabilityEdge(source_id="one", target_id="two"))


def test_add_edge_rejects_missing_target() -> None:
    graph = make_graph("one")

    with pytest.raises(ValueError, match="Target node 'two' does not exist"):
        graph.add_edge(CapabilityEdge(source_id="one", target_id="two"))


def test_add_edge_rejects_identical_duplicate() -> None:
    graph = make_graph("one", "two")
    edge = CapabilityEdge(
        source_id="one",
        target_id="two",
        label="next",
    )
    graph.add_edge(edge)

    with pytest.raises(ValueError, match="Edge already exists"):
        graph.add_edge(edge)


def test_get_node_returns_node_and_raises_for_missing_id() -> None:
    graph = make_graph("one")

    assert graph.get_node("one") == make_node("one")
    with pytest.raises(KeyError):
        graph.get_node("missing")


def test_successors_and_predecessors_follow_edge_insertion_order() -> None:
    graph = make_graph("one", "two", "three")
    graph.add_edge(CapabilityEdge(source_id="one", target_id="three"))
    graph.add_edge(CapabilityEdge(source_id="one", target_id="two"))

    assert [node.id for node in graph.successors("one")] == ["three", "two"]
    assert [node.id for node in graph.predecessors("two")] == ["one"]


def test_has_cycle_distinguishes_dag_and_cycle() -> None:
    graph = make_graph("one", "two", "three")
    graph.add_edge(CapabilityEdge(source_id="one", target_id="two"))
    graph.add_edge(CapabilityEdge(source_id="two", target_id="three"))
    assert graph.has_cycle() is False

    graph.add_edge(CapabilityEdge(source_id="three", target_id="one"))
    assert graph.has_cycle() is True


def test_topological_order_for_simple_chain() -> None:
    graph = make_graph("one", "two", "three")
    graph.add_edge(CapabilityEdge(source_id="one", target_id="two"))
    graph.add_edge(CapabilityEdge(source_id="two", target_id="three"))

    assert [node.id for node in graph.topological_order()] == [
        "one",
        "two",
        "three",
    ]


def test_topological_order_preserves_node_order_for_branching_graph() -> None:
    graph = make_graph("root", "left", "right", "last")
    graph.add_edge(CapabilityEdge(source_id="root", target_id="right"))
    graph.add_edge(CapabilityEdge(source_id="root", target_id="left"))
    graph.add_edge(CapabilityEdge(source_id="left", target_id="last"))
    graph.add_edge(CapabilityEdge(source_id="right", target_id="last"))

    assert [node.id for node in graph.topological_order()] == [
        "root",
        "left",
        "right",
        "last",
    ]


def test_topological_order_rejects_cycle() -> None:
    graph = make_graph("one", "two")
    graph.add_edge(CapabilityEdge(source_id="one", target_id="two"))
    graph.add_edge(CapabilityEdge(source_id="two", target_id="one"))

    with pytest.raises(
        ValueError,
        match="Cannot topologically sort graph with cycles",
    ):
        graph.topological_order()


def test_nodes_and_edges_return_copies() -> None:
    graph = make_graph("one", "two")
    graph.add_edge(CapabilityEdge(source_id="one", target_id="two"))

    nodes = graph.nodes()
    edges = graph.edges()
    nodes.clear()
    edges.clear()

    assert [node.id for node in graph.nodes()] == ["one", "two"]
    assert len(graph.edges()) == 1
