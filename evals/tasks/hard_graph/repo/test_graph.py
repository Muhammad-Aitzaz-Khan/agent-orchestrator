from graph import shortest_path, topological_sort


def test_topological_sort_chain():
    assert topological_sort({"a": ["b"], "b": ["c"], "c": []}) == ["a", "b", "c"]


def test_topological_sort_target_only_nodes():
    assert topological_sort({"shirt": ["tie"], "tie": ["jacket"]}) == ["shirt", "tie", "jacket"]


def test_shortest_path():
    graph = {"a": ["b", "c"], "b": ["d"], "c": ["e"], "e": ["d"]}
    assert shortest_path(graph, "a", "d") == ["a", "b", "d"]
