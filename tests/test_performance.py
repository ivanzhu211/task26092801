"""性能测试：三个规模场景，断言查询耗时。"""

from __future__ import annotations

import os
import tempfile
import time
import unittest

from kgpath import GraphStore, find_paths

NODES_1M = 200_000
EDGES_EXTRA = 800_001  # 加 199,999 条链边，总计 1,000,000 条


def _write_batched(g: GraphStore, items, batch=5000):
    for i in range(0, len(items), batch):
        g.write_edges(items[i:i + batch])


class PerfScenario1(unittest.TestCase):
    """一百万条边 / 二十万节点：局部查询 < 0.5s，含打开后首次查询。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.db = os.path.join(cls.tmp, "big.db")
        t0 = time.perf_counter()
        with GraphStore(cls.db) as g:
            for i in range(0, NODES_1M, 5000):
                g.write_nodes(
                    {"id": f"n{i + j:06d}", "type": "T"} for j in range(5000)
                )
            # 链边：n_i -> n_{i+1}
            chain = (
                {"src": f"n{i:06d}", "rel": "chain",
                 "dst": f"n{i + 1:06d}", "confidence": 0.9}
                for i in range(NODES_1M - 1)
            )
            _write_batched(g, list(chain))
            # 额外 80 万条边，只落在下标 >= 1000 的主体区域
            extra = []
            for i in range(EDGES_EXTRA):
                src = 1000 + i % (NODES_1M - 1000)
                dst = 1000 + (i * 31 + i // (NODES_1M - 1000)) % (NODES_1M - 1000)
                extra.append({
                    "src": f"n{src:06d}", "rel": "x", "dst": f"n{dst:06d}",
                    "confidence": ((i * 37) % 1000 + 1) / 1000,
                })
            _write_batched(g, extra)
        cls.build_seconds = time.perf_counter() - t0

    def test_local_query_under_half_second(self):
        with GraphStore(self.db) as g:
            # 打开图库后的第一次查询
            t0 = time.perf_counter()
            res = find_paths(g, "n000000", "n000005", 10, 10)
            first = time.perf_counter() - t0
            self.assertLess(first, 0.5, f"首次查询耗时 {first:.3f}s")
            self.assertEqual(len(res), 1)
            self.assertEqual(res[0].hops, 5)
            # 后续查询同样要求
            for s, e in (("n000010", "n000019"), ("n000100", "n000105")):
                t0 = time.perf_counter()
                res = find_paths(g, s, e, 10, 10)
                dt = time.perf_counter() - t0
                self.assertLess(dt, 0.5, f"{s}->{e} 耗时 {dt:.3f}s")
                self.assertTrue(res)


class PerfScenario2(unittest.TestCase):
    """9 层 × 5 节点全连接：约 195 万条等置信度路径，Top-10 < 2s。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.db = os.path.join(cls.tmp, "layered.db")
        layers = [[f"L{h}N{i}" for i in range(5)] for h in range(9)]
        with GraphStore(cls.db) as g:
            g.write_nodes([{"id": "S", "type": "T"}, {"id": "T", "type": "T"}])
            g.write_nodes({"id": nid, "type": "T"} for layer in layers for nid in layer)
            edges = []
            for nid in layers[0]:
                edges.append({"src": "S", "rel": "r", "dst": nid, "confidence": 0.9})
            for h in range(8):
                for a in layers[h]:
                    for b in layers[h + 1]:
                        edges.append({"src": a, "rel": "r", "dst": b, "confidence": 0.9})
            for nid in layers[-1]:
                edges.append({"src": nid, "rel": "r", "dst": "T", "confidence": 0.9})
            g.write_edges(edges)

    def test_top10_under_two_seconds(self):
        with GraphStore(self.db) as g:
            t0 = time.perf_counter()
            res = find_paths(g, "S", "T", 10, 10)
            dt = time.perf_counter() - t0
            self.assertLess(dt, 2.0, f"耗时 {dt:.3f}s")
            self.assertEqual(len(res), 10)
            for p in res:
                self.assertEqual(p.hops, 10)
            # 全部路径置信度相同，按节点 id 序列字典序取前 10
            ids = [tuple(n.id for n in p.nodes) for p in res]
            self.assertEqual(ids, sorted(ids))
            self.assertEqual(ids[0][1], "L0N0")


class PerfScenario3(unittest.TestCase):
    """30 节点高置信度团 + 低置信度出口：Top-10 < 2s。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.db = os.path.join(cls.tmp, "clique.db")
        clique = [f"c{i:02d}" for i in range(30)]
        with GraphStore(cls.db) as g:
            g.write_nodes([{"id": "S", "type": "T"}, {"id": "T", "type": "T"}])
            g.write_nodes({"id": nid, "type": "T"} for nid in clique)
            edges = []
            for nid in clique:
                edges.append({"src": "S", "rel": "in", "dst": nid, "confidence": 1})
                edges.append({"src": nid, "rel": "out", "dst": "T", "confidence": 0.01})
            for a in clique:
                for b in clique:
                    if a != b:
                        edges.append({"src": a, "rel": "c", "dst": b, "confidence": 0.99})
            g.write_edges(edges)

    def test_top10_under_two_seconds(self):
        with GraphStore(self.db) as g:
            t0 = time.perf_counter()
            res = find_paths(g, "S", "T", 10, 10)
            dt = time.perf_counter() - t0
            self.assertLess(dt, 2.0, f"耗时 {dt:.3f}s")
            self.assertEqual(len(res), 10)
            # 最可信的 10 条都是 S -> ci -> T，置信度 0.01
            for p in res:
                self.assertEqual(p.confidence, "0.01")
                self.assertEqual(p.hops, 2)
            ids = [tuple(n.id for n in p.nodes) for p in res]
            self.assertEqual(ids, sorted(ids))
            self.assertEqual(ids[0][1], "c00")


if __name__ == "__main__":
    unittest.main()
