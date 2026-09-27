"""性能测试：三个规模场景，断言查询耗时。"""

import random
import time
from collections import deque

import pytest

from kgpath import Edge, GraphStore, Node, find_paths

COMPONENT_SIZE = 300
RING_SIZE = 199_700
TOTAL_NODES = COMPONENT_SIZE + RING_SIZE  # 200,000 个节点
RING_DEGREE = 5
COMPONENT_DEGREE = 10


def _write_batched(store, items, batch=5000):
    items = list(items)
    for start in range(0, len(items), batch):
        store.write_nodes(items[start : start + batch]) if isinstance(
            items[start], Node
        ) else store.write_edges(items[start : start + batch])


def _build_large_graph(path):
    """约一百万条边、二十万个节点。起点所在分量只有几百个节点。"""
    rng = random.Random(42)
    store = GraphStore(str(path)).open()
    nodes = [Node(f"c{i}", "T") for i in range(COMPONENT_SIZE)]
    nodes += [Node(f"r{i}", "T") for i in range(RING_SIZE)]
    _write_batched(store, nodes)

    # 起点附近：300 个节点的稠密分量
    component_edges = set()
    for i in range(COMPONENT_SIZE):
        for _ in range(COMPONENT_DEGREE):
            component_edges.add((f"c{i}", f"c{rng.randrange(COMPONENT_SIZE)}"))
    edges = [
        Edge(src, "r", dst, (500 + rng.randrange(500)) / 1000)
        for src, dst in sorted(component_edges)
    ]
    # 其余节点：长环，与起点所在分量不连通
    for i in range(RING_SIZE):
        for j in range(RING_DEGREE):
            edges.append(
                Edge(f"r{i}", "r", f"r{(i + 1 + j) % RING_SIZE}", (100 + i % 900) / 1000)
            )
    _write_batched(store, edges)
    store.close()
    return component_edges


def _reachable(edge_set, start, max_depth):
    adj = {}
    for src, dst in edge_set:
        adj.setdefault(src, []).append(dst)
    seen = {start}
    frontier = [start]
    for _ in range(max_depth):
        nxt = []
        for node in frontier:
            for dst in adj.get(node, []):
                if dst not in seen:
                    seen.add(dst)
                    nxt.append(dst)
        frontier = nxt
    return seen


@pytest.fixture(scope="module")
def large_graph(tmp_path_factory):
    path = tmp_path_factory.mktemp("perf") / "large.db"
    component_edges = _build_large_graph(path)
    return str(path), component_edges


def test_large_graph_local_query(large_graph):
    """一百万条边的图上，只波及几百个节点的查询（含打开图库后的第一次查询）。"""
    path, component_edges = large_graph
    reachable = _reachable(component_edges, "c0", 10)
    assert len(reachable) <= 400
    target = sorted(reachable - {"c0"})[-1]

    store = GraphStore(path)
    t0 = time.perf_counter()
    store.open()
    result = find_paths(store, "c0", target, 10, 10)
    elapsed = time.perf_counter() - t0
    store.close()
    assert len(result) > 0
    assert elapsed < 0.5, f"首次查询耗时 {elapsed:.3f}s，超过 0.5s"


def test_layered_graph(tmp_path):
    """9 层 × 5 节点全连接，近两百万条路径，深度 10 取前 10。"""
    store = GraphStore(str(tmp_path / "layered.db")).open()
    nodes = [Node("s", "T"), Node("t", "T")]
    nodes += [Node(f"L{i}_{j}", "T") for i in range(1, 10) for j in range(5)]
    store.write_nodes(nodes)
    edges = [Edge("s", "r", f"L1_{j}", 0.9) for j in range(5)]
    for i in range(1, 9):
        for a in range(5):
            for b in range(5):
                edges.append(Edge(f"L{i}_{a}", "r", f"L{i+1}_{b}", 0.9))
    edges += [Edge(f"L9_{j}", "r", "t", 0.9) for j in range(5)]
    store.write_edges(edges)

    t0 = time.perf_counter()
    result = find_paths(store, "s", "t", 10, 10)
    elapsed = time.perf_counter() - t0
    store.close()
    assert len(result) == 10
    assert elapsed < 2.0, f"分层图查询耗时 {elapsed:.3f}s，超过 2s"


def test_clique_graph(tmp_path):
    """30 节点全连接高置信度簇 + 低置信度出口边，深度 10 取前 10。"""
    store = GraphStore(str(tmp_path / "clique.db")).open()
    store.write_nodes([Node(f"c{i}", "T") for i in range(30)] + [Node("t", "T")])
    edges = []
    for i in range(30):
        for j in range(30):
            if i != j:
                edges.append(Edge(f"c{i}", "r", f"c{j}", (990 + (i + j) % 10) / 1000))
    for i in range(30):
        edges.append(Edge(f"c{i}", "out", "t", (10 + i % 10) / 1000))
    store.write_edges(edges)

    t0 = time.perf_counter()
    result = find_paths(store, "c0", "t", 10, 10)
    elapsed = time.perf_counter() - t0
    store.close()
    assert len(result) == 10
    assert elapsed < 2.0, f"稠密簇查询耗时 {elapsed:.3f}s，超过 2s"
