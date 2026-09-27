"""针对业务规则的单元测试。"""

import pytest

from kgpath import (
    AttrCondition,
    Edge,
    GraphStore,
    Node,
    NodeNotFoundError,
    ValidationError,
    find_paths,
)


@pytest.fixture()
def store(tmp_path):
    s = GraphStore(str(tmp_path / "g.db")).open()
    yield s
    s.close()


def test_node_overwrite(store):
    store.write_nodes([Node("a", "T1", {"x": 1})])
    store.write_nodes([Node("a", "T2", {"y": 2})])
    node = store.get_node("a")
    assert node.type == "T2" and node.attrs == {"y": 2}


def test_edge_overwrite(store):
    store.write_nodes([Node("a", "T"), Node("b", "T")])
    store.write_edges([Edge("a", "r", "b", 0.5)])
    store.write_edges([Edge("a", "r", "b", 0.8)])
    edges = store.out_edges("a")
    assert len(edges) == 1 and edges[0].confidence == "0.8"


def test_parallel_edges_and_self_loop(store):
    store.write_nodes([Node("a", "T"), Node("b", "T")])
    store.write_edges([Edge("a", "r1", "b", 0.5), Edge("a", "r2", "b", 0.6), Edge("a", "loop", "a", 1)])
    assert len(store.out_edges("a")) == 3


def test_batch_atomic_on_invalid_confidence(store):
    store.write_nodes([Node("a", "T"), Node("b", "T")])
    with pytest.raises(ValidationError):
        store.write_edges([Edge("a", "r1", "b", 0.5), Edge("a", "r2", "b", 1.5)])
    assert store.out_edges("a") == []


def test_batch_atomic_on_missing_node(store):
    store.write_nodes([Node("a", "T"), Node("b", "T")])
    with pytest.raises(ValidationError):
        store.write_edges([Edge("a", "r1", "b", 0.5), Edge("a", "r2", "ghost", 0.5)])
    assert store.out_edges("a") == []


def test_batch_atomic_on_invalid_node(store):
    store.write_nodes([Node("a", "T")])
    with pytest.raises(ValidationError):
        store.write_nodes([Node("b", "T"), Node("", "T")])
    assert store.get_node("b") is None


def test_batch_size_limit(store):
    store.write_nodes([Node(f"n{i}", "T") for i in range(5000)])
    with pytest.raises(ValidationError):
        store.write_nodes([Node(f"m{i}", "T") for i in range(5001)])
    with pytest.raises(ValidationError):
        store.write_edges([Edge("n0", "r", "n1", 0.5)] * 5001)


@pytest.mark.parametrize("bad", [0, -0.1, 1.001, 1.5, 0.1234, "abc", None, True, float("nan")])
def test_invalid_confidence_rejected(store, bad):
    store.write_nodes([Node("a", "T"), Node("b", "T")])
    with pytest.raises(ValidationError):
        store.write_edges([Edge("a", "r", "b", bad)])


def test_persistence(store, tmp_path):
    store.write_nodes([Node("a", "T", {"x": 1}), Node("b", "T")])
    store.write_edges([Edge("a", "r", "b", 0.7)])
    path = store._path
    store.close()
    s2 = GraphStore(path).open()
    assert s2.get_node("a").attrs == {"x": 1}
    assert s2.out_edges("a")[0].confidence == "0.7"
    assert find_paths(s2, "a", "b", 3, 5)[0].confidence == "0.7"
    s2.close()


def _chain(store):
    store.write_nodes([Node(c, "T", {}) for c in "abcd"])
    store.write_edges([
        Edge("a", "r", "b", 0.7),
        Edge("b", "r", "c", 0.1),
        Edge("a", "r", "d", 1),
        Edge("d", "r", "c", 1),
    ])


def test_exact_decimal_threshold_boundary(store):
    """0.7 * 0.1 = 0.07 在下限 0.07 时必须返回（精确十进制）。"""
    _chain(store)
    res = find_paths(store, "a", "c", 3, 10, min_confidence=0.07)
    confs = [r.confidence for r in res]
    assert "0.07" in confs
    res = find_paths(store, "a", "c", 3, 10, min_confidence=0.071)
    assert "0.07" not in [r.confidence for r in res]


def test_confidence_formatting(store):
    _chain(store)
    res = find_paths(store, "a", "c", 3, 10)
    by_conf = {r.confidence for r in res}
    assert "1" in by_conf  # 1 * 1，去掉末尾多余的 0
    assert "0.07" in by_conf
    for r in res:
        assert r.hops == len(r.edges) == len(r.nodes) - 1


def test_query_param_errors(store):
    _chain(store)
    with pytest.raises(NodeNotFoundError):
        find_paths(store, "ghost", "c", 3, 5)
    with pytest.raises(NodeNotFoundError):
        find_paths(store, "a", "ghost", 3, 5)
    with pytest.raises(ValidationError):
        find_paths(store, "a", "a", 3, 5)
    for depth, k in [(0, 5), (11, 5), (3, 0), (3, 21), (-1, 5), (3, -1)]:
        with pytest.raises(ValidationError):
            find_paths(store, "a", "c", depth, k)
    with pytest.raises(ValidationError):
        find_paths(store, "a", "c", 3, 5, min_confidence=1.5)
    with pytest.raises(ValidationError):
        find_paths(store, "a", "c", 3, 5, min_confidence=-0.1)
    with pytest.raises(ValidationError):
        find_paths(store, "a", "c", 3, 5, min_confidence=0.1234)


def test_intermediate_filter_exempts_endpoints(store):
    store.write_nodes([
        Node("a", "BAD", {"w": 0}),
        Node("m", "MID", {"w": 5}),
        Node("b", "BAD", {"w": 0}),
    ])
    store.write_edges([Edge("a", "r", "m", 1), Edge("m", "r", "b", 1)])
    res = find_paths(
        store, "a", "b", 3, 5,
        node_types={"MID"},
        node_conditions=[AttrCondition("w", "gt", 3)],
    )
    assert len(res) == 1  # 起点终点不过滤，只有中间节点 m 被过滤


def test_condition_semantics(store):
    store.write_nodes([
        Node("a", "T"), Node("b", "T"),
        Node("m1", "T", {"w": 5, "s": "x"}),
        Node("m2", "T", {"w": "5"}),          # 类型对不上
        Node("m3", "T", {}),                   # 属性缺失
        Node("m4", "T", {"w": 7, "s": "y"}),
    ])
    store.write_edges([
        Edge("a", "r", "m1", 1), Edge("m1", "r", "b", 1),
        Edge("a", "r", "m2", 1), Edge("m2", "r", "b", 1),
        Edge("a", "r", "m3", 1), Edge("m3", "r", "b", 1),
        Edge("a", "r", "m4", 1), Edge("m4", "r", "b", 1),
    ])

    def mids(**kw):
        return {r.nodes[1].id for r in find_paths(store, "a", "b", 3, 20, **kw)}

    assert mids(node_conditions=[AttrCondition("w", "gt", 4)]) == {"m1", "m4"}
    assert mids(node_conditions=[AttrCondition("w", "lt", 6)]) == {"m1"}
    assert mids(node_conditions=[AttrCondition("w", "eq", 5)]) == {"m1"}
    # ne 在属性缺失或类型对不上时也不满足
    assert mids(node_conditions=[AttrCondition("w", "ne", 5)]) == {"m4"}
    assert mids(node_conditions=[AttrCondition("s", "ne", "x")]) == {"m4"}
    # 多条条件同时满足
    assert mids(node_conditions=[
        AttrCondition("w", "gt", 4), AttrCondition("s", "eq", "y"),
    ]) == {"m4"}


def test_self_loop_not_in_simple_path(store):
    store.write_nodes([Node("a", "T"), Node("b", "T")])
    store.write_edges([Edge("a", "loop", "a", 1), Edge("a", "r", "b", 0.5)])
    res = find_paths(store, "a", "b", 5, 10)
    assert len(res) == 1 and res[0].hops == 1  # 自环会重复节点，不得出现在路径里


def test_tie_break_order(store):
    """置信度并列时按跳数、节点 id 序列、关系类型序列排序。"""
    store.write_nodes([Node(c, "T") for c in ["s", "t", "b", "a"]])
    store.write_edges([
        Edge("s", "r2", "t", 0.5),          # 1 跳
        Edge("s", "r1", "b", 1), Edge("b", "r1", "t", 0.5),   # 2 跳经 b
        Edge("s", "r1", "a", 1), Edge("a", "r2", "t", 0.5),   # 2 跳经 a
        Edge("s", "r1", "a", 1),  # 覆盖同上
    ])
    store.write_edges([Edge("a", "r1", "t", 0.5)])  # 与 r2 平行，同节点序列
    res = find_paths(store, "s", "t", 3, 10)
    keys = [([n.id for n in r.nodes], [e.relation for e in r.edges]) for r in res]
    assert keys[0] == (["s", "t"], ["r2"])                       # 跳数少优先
    assert keys[1] == (["s", "a", "t"], ["r1", "r1"])            # 节点 a < b
    assert keys[2] == (["s", "a", "t"], ["r1", "r2"])            # 同节点比关系
    assert keys[3] == (["s", "b", "t"], ["r1", "r1"])
    assert all(r.confidence == "0.5" for r in res)
