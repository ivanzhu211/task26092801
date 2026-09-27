"""正确性测试：随机小图上与暴力枚举的全部简单路径逐条比较。"""

from __future__ import annotations

import random
from decimal import Decimal, getcontext
from fractions import Fraction

import pytest

from kgpath import (
    AttrCondition,
    Edge,
    GraphStore,
    Node,
    find_paths,
)
from kgpath._conf import parse_confidence, parse_threshold

getcontext().prec = 60

REL_POOL = ["r0", "r1", "r2", "r3"]
TYPE_POOL = ["T0", "T1", "T2"]
CONF_POOL = [1, 0.999, 0.99, 0.9, 0.7, 0.5, 0.456, 0.123, 0.1, 0.07, 0.01, 0.001]


def fmt_fraction(conf: Fraction) -> str:
    """独立的精确十进制格式化（基于 Decimal），用于校验模块输出。"""
    dec = Decimal(conf.numerator) / Decimal(conf.denominator)
    text = format(dec, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def conf_fraction(milli_value) -> Fraction:
    return Fraction(parse_confidence(milli_value), 1000)


def cond_ok(attrs, cond):
    """独立的条件判断实现。"""
    attr, op, value = cond
    if attr not in attrs:
        return False
    actual = attrs[attr]
    a_num = isinstance(actual, (int, float)) and not isinstance(actual, bool)
    c_num = isinstance(value, (int, float)) and not isinstance(value, bool)
    if op == "gt":
        return a_num and c_num and actual > value
    if op == "lt":
        return a_num and c_num and actual < value
    if a_num != c_num:
        return False
    if not a_num and not isinstance(actual, str):
        return False
    return (actual == value) if op == "eq" else (actual != value)


def brute_force(nodes, edges, start, end, max_depth, k, min_conf, rels, types, conds):
    """暴力枚举全部简单路径，按规则排序取前 K。"""
    node_map = {n.id: n for n in nodes}
    adj = {}
    for e in edges:
        adj.setdefault(e.src, []).append(e)

    def node_ok(nid):
        n = node_map[nid]
        if types is not None and n.type not in types:
            return False
        return all(cond_ok(n.attrs, c) for c in conds)

    threshold = Fraction(parse_threshold(min_conf), 1000)
    found = []

    def dfs(path_ids, path_edges, visited):
        last = path_ids[-1]
        if last == end:
            conf = Fraction(1)
            for e in path_edges:
                conf *= conf_fraction(e.confidence)
            if conf >= threshold:
                found.append((conf, tuple(path_ids), tuple(path_edges)))
            return
        if len(path_edges) >= max_depth:
            return
        for e in adj.get(last, []):
            if rels is not None and e.relation not in rels:
                continue
            if e.dst in visited:
                continue
            if e.dst != end and not node_ok(e.dst):
                continue
            dfs(path_ids + [e.dst], path_edges + [e], visited | {e.dst})

    dfs([start], [], {start})
    found.sort(
        key=lambda item: (
            -item[0],
            len(item[2]),
            item[1],
            tuple(e.relation for e in item[2]),
        )
    )
    return found[:k]


def actual_tuple(result):
    return (
        tuple(n.id for n in result.nodes),
        tuple(n.type for n in result.nodes),
        tuple(tuple(sorted(n.attrs.items())) for n in result.nodes),
        tuple(e.relation for e in result.edges),
        tuple(e.confidence for e in result.edges),
        result.hops,
        result.confidence,
    )


def expected_full(item, nodes):
    node_map = {n.id: n for n in nodes}
    conf, ids, path_edges = item
    return (
        ids,
        tuple(node_map[i].type for i in ids),
        tuple(tuple(sorted(node_map[i].attrs.items())) for i in ids),
        tuple(e.relation for e in path_edges),
        tuple(fmt_fraction(conf_fraction(e.confidence)) for e in path_edges),
        len(path_edges),
        fmt_fraction(conf),
    )


def random_graph(rng):
    n = rng.randint(3, 12)
    nodes = []
    for i in range(n):
        attrs = {}
        if rng.random() < 0.7:
            attrs["a"] = rng.randint(0, 10)
        if rng.random() < 0.4:
            attrs["b"] = rng.choice(["x", "y"])
        if rng.random() < 0.3:
            attrs["c"] = rng.choice([0.5, 1.5, 2.5])
        nodes.append(Node(f"n{i}", rng.choice(TYPE_POOL), attrs))
    edges = []
    seen = set()
    for _ in range(rng.randint(1, 30)):
        src = rng.choice(nodes).id
        dst = rng.choice(nodes).id  # 允许自环
        rel = rng.choice(REL_POOL)
        if (src, rel, dst) in seen:
            continue
        seen.add((src, rel, dst))
        if rng.random() < 0.7:
            conf = rng.choice(CONF_POOL)  # 小池子，并列常见；含置信度 1
        else:
            conf = rng.randint(1, 1000) / 1000
        edges.append(Edge(src, rel, dst, conf))
    return nodes, edges


def random_threshold(rng, edges):
    roll = rng.random()
    if roll < 0.3 and edges:
        # 尝试构造恰好等于某条路径置信度的下限（乘积必须仍是三位小数）
        product = Fraction(1)
        for e in rng.sample(edges, min(len(edges), rng.randint(1, 3))):
            product *= conf_fraction(e.confidence)
        milli = product * 1000
        if milli.denominator == 1:
            return milli.numerator / 1000
    if roll < 0.7:
        return rng.randint(0, 1000) / 1000
    return 0


def random_query(rng, nodes, edges):
    ids = [n.id for n in nodes]
    start, end = rng.sample(ids, 2)
    max_depth = rng.randint(1, 8)
    k = rng.randint(1, 20)
    min_conf = random_threshold(rng, edges)
    rels = None
    if rng.random() < 0.5:
        rels = set(rng.sample(REL_POOL, rng.randint(1, len(REL_POOL))))
    types = None
    if rng.random() < 0.4:
        types = set(rng.sample(TYPE_POOL, rng.randint(1, len(TYPE_POOL))))
    conds = []
    for _ in range(rng.randint(0, 2)):
        attr = rng.choice(["a", "b", "c", "missing"])
        op = rng.choice(["eq", "ne", "gt", "lt"])
        if attr == "b":
            value = rng.choice(["x", "y", "z"])
        elif rng.random() < 0.8:
            value = rng.randint(0, 10)
        else:
            value = rng.choice([0.5, 1.5])
        conds.append((attr, op, value))
    return start, end, max_depth, k, min_conf, rels, types, conds


@pytest.mark.parametrize("seed", range(300))
def test_random_graphs_match_brute_force(seed, tmp_path):
    rng = random.Random(seed)
    nodes, edges = random_graph(rng)
    store = GraphStore(str(tmp_path / "g.db")).open()
    store.write_nodes(nodes)
    if edges:
        store.write_edges(edges)
    for _ in range(3):
        start, end, max_depth, k, min_conf, rels, types, conds = random_query(rng, nodes, edges)
        cond_objs = [AttrCondition(a, o, v) for a, o, v in conds]
        kwargs = dict(
            min_confidence=min_conf,
            allowed_relations=rels,
            node_types=types,
            node_conditions=cond_objs,
        )
        got = find_paths(store, start, end, max_depth, k, **kwargs)
        expected = brute_force(nodes, edges, start, end, max_depth, k, min_conf, rels, types, conds)
        assert len(got) == len(expected), (
            f"seed={seed} count mismatch: got {len(got)} want {len(expected)}"
        )
        for g, e in zip(got, expected):
            assert actual_tuple(g) == expected_full(e, nodes), (
                f"seed={seed}\ngot:  {actual_tuple(g)}\nwant: {expected_full(e, nodes)}"
            )
        # 同一查询跑两次，结果必须完全一致
        again = find_paths(store, start, end, max_depth, k, **kwargs)
        assert [actual_tuple(r) for r in again] == [actual_tuple(r) for r in got]
    store.close()
