"""Graph algorithms on adjacency lists: {node: [nodes it points to]}."""
import heapq
from collections import deque


def topological_sort(graph):
    """Order the nodes so every node comes before the nodes it points to.

    Nodes may appear only as targets (without their own key). When several nodes are
    available at the same time, take them in alphabetical order. Raise ValueError if the
    graph contains a cycle.
    """
    indegree = {node: 0 for node in graph}
    for targets in graph.values():
        for target in targets:
            indegree[target] = indegree.get(target, 0) + 1

    ready = [node for node, degree in indegree.items() if degree == 0]
    heapq.heapify(ready)
    order = []
    while ready:
        node = heapq.heappop(ready)
        order.append(node)
        for target in graph.get(node, []):
            indegree[target] -= 1
            if indegree[target] == 0:
                heapq.heappush(ready, target)
    if len(order) != len(indegree):
        raise ValueError("graph contains a cycle")
    return order


def shortest_path(graph, start, goal):
    """Return the shortest path from start to goal as a list of nodes, including both ends.

    Returns [start] when start == goal, and None when goal cannot be reached.
    """
    queue = deque([start])
    parent = {start: None}
    while queue:
        node = queue.popleft()
        if node == goal:
            break
        for neighbour in graph.get(node, []):
            if neighbour not in parent:
                parent[neighbour] = node
                queue.append(neighbour)

    if goal not in parent:
        return None
    path = []
    node = goal
    while node is not None:
        path.append(node)
        node = parent[node]
    return path[::-1]
