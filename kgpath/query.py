"""路径查询引擎：Top-K 最可信简单路径。

算法概述（所有置信度比较均为精确十进制，见 models.Conf）：
1. 反向动态规划：从终点沿反向边做层数 <= max_depth 的松弛，得到
   reach[n][r] = 从 n 出发 r 跳内到达终点的最大置信度乘积（上界）。
   计算带预算上限，超限时对未知区域退回上界 1.0，保证正确性。
2. 第一遍 best-first：按部分路径置信度上界从大到小扩展，维护已见合法
   完整路径的第 K 大置信度 T*。上界 <= T* 的部分路径不可能进入 Top-K，
   直接剪枝。收集所有置信度严格大于 T* 的完整路径（至多 K-1 条）。
3. 第二遍按 (跳数, 节点id序列, 关系序列) 字典序枚举置信度恰好等于 T*
   的完整路径，补足剩余名额。
"""

from __future__ import annotations

import bisect
import heapq
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from .errors import NodeNotFoundError, ValidationError
from .models import SCALE, AttrCondition, Conf, EdgeResult, Node, PathResult
from .store import GraphStore

_ONE = Conf(1, 0)
_ZERO = Conf(0, 0)

MAX_DEPTH_LIMIT = 10
MAX_K = 20
_RELAX_BUDGET = 500_000  # 反向DP每次查询最多松弛的边数

_OPS = ("eq", "ne", "gt", "lt")


class _Partial:
    """搜索中的部分（或完整）路径。"""

    __slots__ = ("ids", "rels", "millis", "conf", "visited")

    def __init__(self, ids, rels, millis, conf, visited):
        self.ids = ids          # tuple[str]
        self.rels = rels        # tuple[str]
        self.millis = millis    # tuple[int]
        self.conf = conf        # Conf
        self.visited = visited  # frozenset[str]

    @property
    def hops(self) -> int:
        return len(self.rels)


class _Item:
    """第一遍搜索的堆元素：u 大优先，其次跳数大，再其次 id/关系序列小。"""

    __slots__ = ("u", "path")

    def __init__(self, u: Conf, path: _Partial) -> None:
        self.u = u
        self.path = path

    def __lt__(self, other: "_Item") -> bool:
        a, b = self.path, other.path
        if self.u != other.u:
            return other.u < self.u
        if a.hops != b.hops:
            return a.hops > b.hops
        if a.ids != b.ids:
            return a.ids < b.ids
        return a.rels < b.rels


def _parse_min_confidence(value: Any) -> Conf:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValidationError(f"置信度下限类型非法: {value!r}")
    try:
        dec = Decimal(str(value))
    except InvalidOperation:
        raise ValidationError(f"置信度下限不是合法数值: {value!r}") from None
    if not dec.is_finite() or dec < 0 or dec > 1:
        raise ValidationError(f"置信度下限必须在 [0, 1] 内: {value!r}")
    milli = dec * SCALE
    if milli != milli.to_integral_value():
        raise ValidationError(f"置信度下限最多三位小数: {value!r}")
    return Conf(int(milli), 1)


def _match_value(actual: Any, op: str, expected: Any) -> bool:
    def is_num(x: Any) -> bool:
        return isinstance(x, (int, float)) and not isinstance(x, bool)

    if is_num(actual) and is_num(expected):
        pass  # 数值比较
    elif isinstance(actual, str) and isinstance(expected, str):
        if op in ("gt", "lt"):
            return False  # 大于、小于只对数字生效
    else:
        return False  # 类型对不上，条件不满足
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "gt":
        return actual > expected
    return actual < expected


class _Query:
    def __init__(
        self,
        store: GraphStore,
        start_id: str,
        end_id: str,
        max_depth: int,
        k: int,
        min_confidence: Any,
        rel_types: Optional[set[str]],
        mid_types: Optional[set[str]],
        attr_conditions: Optional[list],
    ) -> None:
        # ---- 参数校验 ----
        if isinstance(max_depth, bool) or not isinstance(max_depth, int):
            raise ValidationError("max_depth 必须是整数")
        if not 1 <= max_depth <= MAX_DEPTH_LIMIT:
            raise ValidationError("max_depth 必须在 1..10")
        if isinstance(k, bool) or not isinstance(k, int):
            raise ValidationError("k 必须是整数")
        if not 1 <= k <= MAX_K:
            raise ValidationError("k 必须在 1..20")
        if start_id == end_id:
            raise ValidationError("起点不能等于终点")
        self.min_conf = _parse_min_confidence(min_confidence)
        if rel_types is not None:
            rel_types = set(rel_types)
            if not all(isinstance(r, str) for r in rel_types):
                raise ValidationError("rel_types 必须是字符串集合")
        if mid_types is not None:
            mid_types = set(mid_types)
            if not all(isinstance(t, str) for t in mid_types):
                raise ValidationError("mid_types 必须是字符串集合")
        self.conds: list[AttrCondition] = []
        for c in attr_conditions or []:
            if isinstance(c, dict):
                c = AttrCondition(c["attr"], c["op"], c["value"])
            if c.op not in _OPS:
                raise ValidationError(f"不支持的属性比较符: {c.op!r}")
            if isinstance(c.value, bool) or not isinstance(c.value, (str, int, float)):
                raise ValidationError(f"属性条件值必须是字符串或数字: {c.value!r}")
            self.conds.append(c)

        self.store = store
        self.start_id = start_id
        self.end_id = end_id
        self.max_depth = max_depth
        self.k = k
        self.rel_types = rel_types
        self.mid_types = mid_types

        # ---- 起点终点必须存在 ----
        self._node_cache: dict[str, Optional[Node]] = {}
        if self._node(start_id) is None:
            raise NodeNotFoundError(f"起点不存在: {start_id!r}")
        if self._node(end_id) is None:
            raise NodeNotFoundError(f"终点不存在: {end_id!r}")

        self._adj_cache: dict[str, list[tuple[str, str, int]]] = {}
        self.reach: dict[str, list[Optional[Conf]]] = {}
        self.minhop: dict[str, int] = {}
        self.reach_capped = False
        self.completed_layers = 0

    # ---------------- 基础访问 ----------------

    def _node(self, nid: str) -> Optional[Node]:
        if nid not in self._node_cache:
            self._node_cache[nid] = self.store.get_node(nid)
        return self._node_cache[nid]

    def _adj(self, nid: str) -> list[tuple[str, str, int]]:
        """出边 (dst, rel, milli)，已按关系类型过滤并按 (dst, rel) 排序。"""
        cached = self._adj_cache.get(nid)
        if cached is None:
            rows = self.store.get_outgoing(nid)
            if self.rel_types is not None:
                rows = [r for r in rows if r[1] in self.rel_types]
            rows.sort(key=lambda r: (r[0], r[1]))
            self._adj_cache[nid] = rows
            cached = rows
        return cached

    def _mid_ok(self, nid: str) -> bool:
        node = self._node(nid)
        if node is None:
            return False
        if self.mid_types is not None and node.type not in self.mid_types:
            return False
        for c in self.conds:
            if c.attr not in node.attrs:
                return False  # 属性缺失一律不满足
            if not _match_value(node.attrs[c.attr], c.op, c.value):
                return False
        return True

    # ---------------- 反向 DP：到达终点的置信度上界 ----------------

    def _build_reach(self) -> None:
        depth = self.max_depth
        self.reach = {self.end_id: [_ONE] * (depth + 1)}
        self.minhop = {self.end_id: 0}
        frontier = {self.end_id}
        budget = _RELAX_BUDGET
        for h in range(1, depth + 1):
            layer: dict[str, Conf] = {}
            for m in frontier:
                rm = self.reach[m][h - 1]
                if rm is None:
                    continue
                for src, milli in self.store.get_incoming(m):
                    budget -= 1
                    if budget < 0:
                        self.reach_capped = True
                        self.completed_layers = h - 1
                        return
                    cand = Conf(rm.num * milli, rm.k + 1)
                    cur = layer.get(src)
                    if cur is None or cur < cand:
                        layer[src] = cand
            for nid in layer:
                if nid not in self.reach:
                    self.reach[nid] = [None] * (depth + 1)
            nxt = set()
            for nid, lst in self.reach.items():
                best = layer.get(nid)
                prev = lst[h - 1]
                if best is None or (prev is not None and prev > best):
                    best = prev
                lst[h] = best
                if best is not None:
                    nxt.add(nid)
                    if nid not in self.minhop:
                        self.minhop[nid] = h
            frontier = nxt
            if not frontier:
                break
        self.completed_layers = depth if not self.reach_capped else self.completed_layers

    def _reach_upper(self, nid: str, remaining: int) -> Conf:
        """n 在 remaining 跳内到终点的置信度上界；未知按 1.0（安全上界）。"""
        if remaining > self.completed_layers:
            return _ONE
        lst = self.reach.get(nid)
        if lst is None:
            return _ZERO
        val = lst[remaining]
        return val if val is not None else _ZERO

    def _upper(self, path: _Partial) -> Conf:
        remaining = self.max_depth - path.hops
        return path.conf * self._reach_upper(path.ids[-1], remaining)

    # ---------------- 第一遍：确定第 K 大置信度 T* 与更优组 ----------------

    def _pass1(self) -> tuple[list[_Partial], Optional[Conf]]:
        kth_list: list[Conf] = []  # 已见合法完整路径置信度，升序，最多 K 个
        better: list[_Partial] = []  # 置信度 > 当前第 K 大的完整路径
        start = _Partial((self.start_id,), (), (), _ONE, frozenset({self.start_id}))
        heap: list[_Item] = []
        self._push(heap, start)

        def kth() -> Optional[Conf]:
            return kth_list[0] if len(kth_list) == self.k else None

        while heap:
            item = heapq.heappop(heap)
            u = item.u
            if u.num == 0 or u < self.min_conf:
                continue
            k = kth()
            if k is not None and u <= k:
                continue
            path = item.path
            if path.ids[-1] == self.end_id:
                # 完整合法路径（剪枝已保证 conf >= min_conf）
                bisect.insort(kth_list, path.conf)
                if len(kth_list) > self.k:
                    kth_list.pop(0)
                k = kth()
                if k is None or path.conf > k:
                    better.append(path)
                continue
            if path.hops >= self.max_depth:
                continue
            self._expand(heap, path)
        t_star = kth_list[0] if len(kth_list) == self.k else None
        return better, t_star

    def _push(self, heap: list[_Item], path: _Partial) -> None:
        u = self._upper(path)
        if u.num == 0 or u < self.min_conf:
            return
        heapq.heappush(heap, _Item(u, path))

    def _expand(self, heap: list[_Item], path: _Partial) -> None:
        nid = path.ids[-1]
        for dst, rel, milli in self._adj(nid):
            if dst in path.visited:
                continue
            if dst != self.end_id and not self._mid_ok(dst):
                continue
            child = _Partial(
                path.ids + (dst,),
                path.rels + (rel,),
                path.millis + (milli,),
                path.conf * Conf(milli, 1),
                path.visited | {dst},
            )
            self._push(heap, child)

    # ---------------- 第二遍：枚举置信度 == T* 的路径 ----------------

    def _pass2(self, t_star: Conf, need: int) -> list[_Partial]:
        results: list[_Partial] = []
        default_minhop = 0 if self.reach_capped else self.max_depth + 1
        for h in range(1, self.max_depth + 1):
            if len(results) >= need:
                break
            self._dfs_exact(h, t_star, need, results, default_minhop)
        return results

    def _dfs_exact(
        self,
        target_hops: int,
        t_star: Conf,
        need: int,
        results: list[_Partial],
        default_minhop: int,
    ) -> None:
        start = _Partial((self.start_id,), (), (), _ONE, frozenset({self.start_id}))
        stack: list[tuple[_Partial, int]] = [(start, 0)]
        while stack and len(results) < need:
            path, child_idx = stack[-1]
            nid = path.ids[-1]
            if child_idx == 0:
                # 进入节点时剪枝
                remaining = target_hops - path.hops
                if self.minhop.get(nid, default_minhop) > remaining:
                    stack.pop()
                    continue
                bound = path.conf * self._reach_upper(nid, remaining)
                if bound < t_star:
                    stack.pop()
                    continue
            edges = self._adj(nid) if path.hops < target_hops else ()
            if child_idx >= len(edges):
                stack.pop()
                continue
            stack[-1] = (path, child_idx + 1)
            dst, rel, milli = edges[child_idx]
            if dst in path.visited:
                continue
            if dst == self.end_id:
                if path.hops + 1 == target_hops:
                    conf = path.conf * Conf(milli, 1)
                    if conf == t_star:
                        results.append(
                            _Partial(
                                path.ids + (dst,),
                                path.rels + (rel,),
                                path.millis + (milli,),
                                conf,
                                path.visited | {dst},
                            )
                        )
                continue
            if not self._mid_ok(dst):
                continue
            child = _Partial(
                path.ids + (dst,),
                path.rels + (rel,),
                path.millis + (milli,),
                path.conf * Conf(milli, 1),
                path.visited | {dst},
            )
            stack.append((child, 0))

    # ---------------- 结果装配 ----------------

    def _to_result(self, path: _Partial) -> PathResult:
        nodes = [self._node(nid) for nid in path.ids]
        edges = [
            EdgeResult(rel_type=rel, confidence=Conf(m, 1).to_text())
            for rel, m in zip(path.rels, path.millis)
        ]
        return PathResult(
            nodes=nodes,
            edges=edges,
            hops=path.hops,
            confidence=path.conf.to_text(),
        )

    def run(self) -> list[PathResult]:
        self._build_reach()
        better, t_star = self._pass1()
        if t_star is None:
            chosen = better  # 合法完整路径不足 K 条，全部返回
        else:
            chosen = [p for p in better if p.conf > t_star]
            need = self.k - len(chosen)
            if need > 0:
                chosen = chosen + self._pass2(t_star, need)
        # 更优组按完整排序键排序；并列组在第二遍中已按序产生
        chosen_better = [p for p in chosen if t_star is None or p.conf > t_star]
        ties = [p for p in chosen if t_star is not None and p.conf == t_star]
        chosen_better.sort(key=lambda p: p.rels)
        chosen_better.sort(key=lambda p: p.ids)
        chosen_better.sort(key=lambda p: p.hops)
        chosen_better.sort(key=lambda p: p.conf, reverse=True)
        return [self._to_result(p) for p in chosen_better + ties]


def find_paths(
    store: GraphStore,
    start_id: str,
    end_id: str,
    max_depth: int,
    k: int,
    min_confidence: Any = 0,
    rel_types: Optional[set[str]] = None,
    mid_types: Optional[set[str]] = None,
    attr_conditions: Optional[list] = None,
) -> list[PathResult]:
    """查询 start_id 到 end_id 的 Top-K 最可信简单路径。"""
    return _Query(
        store,
        start_id,
        end_id,
        max_depth,
        k,
        min_confidence,
        rel_types,
        mid_types,
        attr_conditions,
    ).run()
