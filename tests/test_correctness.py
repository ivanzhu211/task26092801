"""正确性测试：随机小图上与暴力枚举逐条对比。"""

from __future__ import annotations

import os
import random
import tempfile
import unittest
from decimal import Decimal
from fractions import Fraction

from kgpath import (
    AttrCondition,
    GraphStore,
    NodeNotFoundError,
    ValidationError,
    find_paths,
)


def frac_text(fr: Fraction) -> str:
    """与模块无关的独立实现：精确十进制字符串，去末尾 0。"""
    d = Decimal(fr.numerator) / Decimal(fr.denominator)
    return format(d.normalize(), "f")


def match_value(actual, op, expected) -> bool:
    def is_num(x):
        return isinstance(x, (int, float)) and not isinstance(x, bool)

    if is_num(actual) and is_num(expected):
        pass
    elif isinstance(actual, str) and isinstance(expected, str):
        if op in ("gt", "lt"):
            return False
    else:
        return False
    return {"eq": actual == expected, "ne": actual != expected,
            "gt": actual > expected, "lt": actual < expected}[op]


def brute_force(nodes, edges, start, end, max_depth, k, min_conf,
                rel_types, mid_types, conds):
    """独立暴力实现：枚举全部简单路径，排序取前 K。"""
    attrs = {n["id"]: n.get("attrs", {}) for n in nodes}
    types = {n["id"]: n["type"] for n in nodes}
    adj: dict[str, list[tuple[str, str, int]]] = {}
    for e in edges:
        if rel_types is not None and e["rel"] not in rel_types:
            continue
        adj.setdefault(e["src"], []).append((e["dst"], e["rel"], e["milli"]))

    def mid_ok(nid):
        if mid_types is not None and types[nid] not in mid_types:
            return False
        for attr, op, val in conds:
            if attr not in attrs[nid]:
                return False
            if not match_value(attrs[nid][attr], op, val):
                return False
        return True

    min_fr = Fraction(min_conf, 1000)
    found = []  # (Fraction conf, ids, rels, millis)

    def dfs(ids, rels, millis, fr):
        if ids[-1] == end:
            if fr >= min_fr:
                found.append((fr, ids, rels, millis))
            return
        if len(rels) >= max_depth:
            return
        for dst, rel, milli in adj.get(ids[-1], []):
            if dst in ids:
                continue
            if dst != end and not mid_ok(dst):
                continue
            dfs(ids + (dst,), rels + (rel,), millis + (milli,),
                fr * Fraction(milli, 1000))

    dfs((start,), (), (), Fraction(1))
    found.sort(key=lambda t: t[2])
    found.sort(key=lambda t: t[1])
    found.sort(key=lambda t: len(t[2]))
    found.sort(key=lambda t: t[0], reverse=True)
    return found[:k]


def expected_tuple(rec):
    fr, ids, rels, millis = rec
    return (
        frac_text(fr),
        len(rels),
        tuple(ids),
        tuple(rels),
        tuple(frac_text(Fraction(m, 1000)) for m in millis),
    )


def actual_tuple(p):
    return (
        p.confidence,
        p.hops,
        tuple(n.id for n in p.nodes),
        tuple(e.rel_type for e in p.edges),
        tuple(e.confidence for e in p.edges),
    )


def random_graph(rng):
    n_nodes = rng.randint(3, 8)
    ids = [f"n{i}" for i in range(n_nodes)]
    types = ["T1", "T2", "T3"]
    nodes = []
    for nid in ids:
        attrs = {}
        if rng.random() < 0.8:
            attrs["score"] = rng.choice([1, 2, 3, 5, 10, 0.5, 2.5])
        if rng.random() < 0.5:
            attrs["tag"] = rng.choice(["a", "b", "c"])
        nodes.append({"id": nid, "type": rng.choice(types), "attrs": attrs})
    # 小置信度池以制造并列；含 1 与三位小数
    conf_pool = [1, 0.5, 0.7, 0.9, 0.1, 0.25, 0.125, 0.999, 0.001]
    edges = []
    rels = ["ra", "rb", "rc"]
    for src in ids:
        for dst in ids:
            r = rng.random()
            if r < 0.35:
                continue
            chosen = rng.sample(rels, rng.randint(1, 2))  # 平行边
            for rel in chosen:
                if rng.random() < 0.7:
                    edges.append({"src": src, "rel": rel, "dst": dst,
                                  "confidence": rng.choice(conf_pool)})
    return nodes, edges


class CorrectnessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "g.db")
        self._counter = iter(range(10**6))

    def run_one(self, rng, nodes, edges, start, end, max_depth, k,
                min_conf, rel_types, mid_types, conds):
        db = os.path.join(self.tmp, f"g{next(self._counter)}.db")
        with GraphStore(db) as g:
            g.write_nodes(nodes)
            for i in range(0, len(edges), 500):
                g.write_edges(edges[i:i + 500])
            milli_edges = []
            for e in edges:
                milli_edges.append({**e, "milli": int(
                    Decimal(str(e["confidence"])) * 1000)})
            ac = [AttrCondition(*c) for c in conds]
            got = find_paths(g, start, end, max_depth, k,
                             min_confidence=min_conf[1],
                             rel_types=rel_types, mid_types=mid_types,
                             attr_conditions=ac)
        want = brute_force(nodes, milli_edges, start, end, max_depth, k,
                           min_conf[0], rel_types, mid_types, conds)
        got_t = [actual_tuple(p) for p in got]
        want_t = [expected_tuple(r) for r in want]
        self.assertEqual(
            got_t, want_t,
            msg=f"\nstart={start} end={end} depth={max_depth} k={k} "
                f"min_conf={min_conf[1]} rel={rel_types} mid={mid_types} "
                f"conds={conds}\nnodes={nodes}\nedges={edges}\n"
                f"got ={got_t}\nwant={want_t}",
        )
        # 节点内容也要一致
        node_map = {n["id"]: n for n in nodes}
        for p in got:
            for n in p.nodes:
                self.assertEqual(n.type, node_map[n.id]["type"])
                self.assertEqual(n.attrs, node_map[n.id].get("attrs", {}))

    def test_random_small_graphs(self):
        rng = random.Random(20260928)
        n_cases = 400
        for case in range(n_cases):
            nodes, edges = random_graph(rng)
            ids = [n["id"] for n in nodes]
            start, end = rng.sample(ids, 2)
            max_depth = rng.randint(1, 6)
            k = rng.randint(1, 20)
            # 下限：随机取 0、任意值、或某条完整路径的置信度（命中边界）
            milli_edges = [{**e, "milli": int(Decimal(str(e["confidence"])) * 1000)}
                           for e in edges]
            all_paths = brute_force(nodes, milli_edges, start, end, max_depth,
                                    10**9, 0, None, None, [])
            choice = rng.random()
            if all_paths and choice < 0.35:
                # 只取本身不超过三位小数的路径置信度作为下限（恰好等于下限）
                exact = [fr for fr, *_ in all_paths if (fr * 1000).denominator == 1]
                if exact:
                    m = int(rng.choice(exact) * 1000)
                    min_conf = (m, str(Decimal(m) / 1000))
                else:
                    min_conf = (0, 0)
            elif choice < 0.7:
                m = rng.choice([0, 1, 10, 100, 500, 999, 1000])
                min_conf = (m, str(Decimal(m) / 1000))
            else:
                min_conf = (0, 0)
            rel_types = (set(rng.sample(["ra", "rb", "rc"], rng.randint(1, 2)))
                         if rng.random() < 0.4 else None)
            mid_types = (set(rng.sample(["T1", "T2", "T3"], rng.randint(1, 2)))
                         if rng.random() < 0.3 else None)
            conds = []
            if rng.random() < 0.4:
                for _ in range(rng.randint(1, 2)):
                    conds.append((
                        rng.choice(["score", "tag", "missing"]),
                        rng.choice(["eq", "ne", "gt", "lt"]),
                        rng.choice([1, 2, 3, 5, 0.5, 2.5, "a", "b"]),
                    ))
            with self.subTest(case=case):
                self.run_one(rng, nodes, edges, start, end, max_depth, k,
                             min_conf, rel_types, mid_types, conds)

    def test_errors(self):
        with GraphStore(self.db) as g:
            g.write_nodes([{"id": "a", "type": "T"}, {"id": "b", "type": "T"}])
            g.write_edges([{"src": "a", "rel": "r", "dst": "b", "confidence": 1}])
            with self.assertRaises(NodeNotFoundError):
                find_paths(g, "zzz", "b", 3, 5)
            with self.assertRaises(NodeNotFoundError):
                find_paths(g, "a", "zzz", 3, 5)
            with self.assertRaises(ValidationError):
                find_paths(g, "a", "a", 3, 5)
            for bad_depth in (0, 11, -1, 2.5, "3"):
                with self.assertRaises(ValidationError):
                    find_paths(g, "a", "b", bad_depth, 5)
            for bad_k in (0, 21, -1, 1.5):
                with self.assertRaises(ValidationError):
                    find_paths(g, "a", "b", 3, bad_k)
            for bad_mc in (-0.1, 1.1, 0.0001, "x"):
                with self.assertRaises(ValidationError):
                    find_paths(g, "a", "b", 3, 5, min_confidence=bad_mc)

    def test_persistence_and_determinism(self):
        nodes, edges = random_graph(random.Random(7))
        ids = [n["id"] for n in nodes]
        with GraphStore(self.db) as g:
            g.write_nodes(nodes)
            g.write_edges(edges)
        # 关掉再打开，两次查询结果完全一致
        results = []
        for _ in range(2):
            with GraphStore(self.db) as g:
                res = find_paths(g, ids[0], ids[1], 5, 10)
                results.append([actual_tuple(p) for p in res])
        self.assertEqual(results[0], results[1])


if __name__ == "__main__":
    unittest.main()
