"""路径查询：Yen 式 K 最优简单路径枚举。

排序键（精确字典序）：(-路径置信度, 跳数, 途经节点 id 序列, 关系类型序列)。
置信度用 Fraction 精确表示（分母为 1000 的幂），比较不涉及浮点。

核心观察：把同一条边 prepend 到两条补全路径上时，上述键的大小关系保持
不变，因此"从某节点出发、剩余若干跳内的最优补全"可以用 (节点, 剩余跳数)
记忆化 DP 求出；最优走法必然是无环简单路径（环的置信度乘积 <= 1，去掉
环后键更小）。在此基础上用 Yen 的分叉（spur）枚举按序取出前 K 条。
"""

from __future__ import annotations

import heapq
import itertools
from fractions import Fraction
from typing import Any, Iterable

from ._conf import format_milli, format_product, parse_threshold
from .errors import NodeNotFoundError, ValidationError
from .store import GraphStore
from .types import (
    OP_EQ,
    OP_GT,
    OP_LT,
    OP_NE,
    VALID_OPS,
    AttrCondition,
    Node,
    PathEdge,
    PathResult,
)

MIN_DEPTH, MAX_DEPTH = 1, 10
MIN_K, MAX_K = 1, 20

# 1000 的幂：置信度以整数分子表示，值 = num / _POW1000[hops]，比较走交叉相乘
_POW1000 = tuple(1000**i for i in range(MAX_DEPTH + 1))


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _condition_ok(attrs: dict[str, Any], cond: AttrCondition) -> bool:
    """属性缺失或类型对不上时一律不满足；大于、小于只对数字生效。"""
    if cond.attr not in attrs:
        return False
    value = attrs[cond.attr]
    cond_value = cond.value
    value_is_num = _is_number(value)
    cond_is_num = _is_number(cond_value)
    if cond.op in (OP_GT, OP_LT):
        if not (value_is_num and cond_is_num):
            return False
        return value > cond_value if cond.op == OP_GT else value < cond_value
    if value_is_num != cond_is_num:
        return False
    if not value_is_num and not (isinstance(value, str) and isinstance(cond_value, str)):
        return False
    equal = value == cond_value
    return equal if cond.op == OP_EQ else not equal


class _Solver:
    """某个 avoid 集合（前缀节点）下的最优补全 DP。"""

    __slots__ = ("_query", "_avoid", "_memo")

    def __init__(self, query: "_Query", avoid: frozenset[str]) -> None:
        self._query = query
        self._avoid = avoid
        self._memo: dict[tuple[str, int], tuple | None] = {}

    def best(self, node_id: str, rem: int) -> tuple | None:
        """从 node_id 出发、不超过 rem 跳到终点的最优补全。

        返回 (num: int, hops: int, ids: tuple, rels: tuple) 或 None，
        置信度 = num / 1000 ** hops。
        """
        query = self._query
        if node_id == query.end:
            return (1, 0, (node_id,), ())
        if rem <= 0:
            return None
        key = (node_id, rem)
        if key in self._memo:
            return self._memo[key]
        result = self.best_via_edges(node_id, rem, None)
        self._memo[key] = result
        return result

    def best_via_edges(
        self, node_id: str, rem: int, banned: frozenset[tuple[str, str]] | None
    ) -> tuple | None:
        """best() 的边展开部分；banned 仅禁止第一层边（(rel, dst) 标识）。"""
        query = self._query
        avoid = self._avoid
        end = query.end
        node_ok = query.node_ok
        best_cand = None
        best_num = 0
        best_hops = 0
        for rel, dst, milli in query.adj(node_id):
            if banned is not None and (rel, dst) in banned:
                continue
            if dst in avoid:
                continue
            if dst != end and not node_ok(dst):
                continue
            sub = self.best(dst, rem - 1)
            if sub is None:
                continue
            num = milli * sub[0]
            hops = sub[1] + 1
            if best_cand is not None:
                # 精确比较 num/1000^hops 与 best_num/1000^best_hops
                lhs = num * _POW1000[best_hops]
                rhs = best_num * _POW1000[hops]
                if lhs < rhs:
                    continue
                if lhs == rhs:
                    if hops > best_hops:
                        continue
                    if hops == best_hops:
                        cand_ids = (node_id,) + sub[2]
                        cand_rels = (rel,) + sub[3]
                        if cand_ids > best_cand[2] or (
                            cand_ids == best_cand[2] and cand_rels >= best_cand[3]
                        ):
                            continue
                        best_cand = (num, hops, cand_ids, cand_rels)
                        continue
            best_cand = (num, hops, (node_id,) + sub[2], (rel,) + sub[3])
            best_num = num
            best_hops = hops
        return best_cand


class _Query:
    def __init__(
        self,
        store: GraphStore,
        start: str,
        end: str,
        max_depth: int,
        k: int,
        min_confidence: Any,
        allowed_relations: Iterable[str] | None,
        node_types: Iterable[str] | None,
        node_conditions: Iterable[AttrCondition] | None,
    ) -> None:
        self.store = store
        self.start = self._check_node_id("起点", start)
        self.end = self._check_node_id("终点", end)
        if self.start == self.end:
            raise ValidationError("起点和终点不能相同")
        self.max_depth = self._check_int("最大深度", max_depth, MIN_DEPTH, MAX_DEPTH)
        self.k = self._check_int("返回条数 K", k, MIN_K, MAX_K)
        self.threshold = Fraction(parse_threshold(min_confidence), 1000)
        if allowed_relations is not None:
            allowed_relations = self._check_str_set("允许的关系类型", allowed_relations)
        self.allowed_relations = allowed_relations
        if node_types is not None:
            node_types = self._check_str_set("中间节点实体类型", node_types)
        self.node_types = node_types
        self.conditions = self._check_conditions(node_conditions)

        self._adj_cache: dict[str, list[tuple[str, str, int]]] = {}
        self._node_cache: dict[str, Node | None] = {}
        self._node_ok_cache: dict[str, bool] = {}

    @staticmethod
    def _check_node_id(what: str, value: Any) -> str:
        if not isinstance(value, str) or not value:
            raise ValidationError(f"{what} id 必须是非空字符串: {value!r}")
        return value

    @staticmethod
    def _check_int(what: str, value: Any, low: int, high: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValidationError(f"{what}必须是整数: {value!r}")
        if not (low <= value <= high):
            raise ValidationError(f"{what}必须在 {low} 到 {high} 之间: {value!r}")
        return value

    @staticmethod
    def _check_str_set(what: str, values: Iterable[str]) -> frozenset[str]:
        result = set()
        for value in values:
            if not isinstance(value, str) or not value:
                raise ValidationError(f"{what}必须是非空字符串: {value!r}")
            result.add(value)
        return frozenset(result)

    @staticmethod
    def _check_conditions(
        conditions: Iterable[AttrCondition] | None,
    ) -> tuple[AttrCondition, ...]:
        result = []
        for cond in conditions or ():
            if not isinstance(cond.attr, str) or not cond.attr:
                raise ValidationError(f"属性条件的属性名必须是非空字符串: {cond.attr!r}")
            if cond.op not in VALID_OPS:
                raise ValidationError(f"属性条件的操作符必须是 {VALID_OPS} 之一: {cond.op!r}")
            value = cond.value
            if not (_is_number(value) or isinstance(value, str)):
                raise ValidationError(f"属性条件的值必须是字符串或数字: {value!r}")
            if isinstance(value, float) and value != value:
                raise ValidationError(f"属性条件的值必须是有限数字: {value!r}")
            result.append(AttrCondition(cond.attr, cond.op, value))
        return tuple(result)

    def node(self, node_id: str) -> Node | None:
        if node_id not in self._node_cache:
            self._node_cache[node_id] = self.store.get_node(node_id)
        return self._node_cache[node_id]

    def adj(self, node_id: str) -> list[tuple[str, str, int]]:
        if node_id not in self._adj_cache:
            edges = self.store._out_edges_milli(node_id)
            if self.allowed_relations is not None:
                edges = [e for e in edges if e[0] in self.allowed_relations]
            self._adj_cache[node_id] = edges
        return self._adj_cache[node_id]

    def node_ok(self, node_id: str) -> bool:
        """中间节点过滤：实体类型集合 + 属性条件（全部满足）。"""
        if node_id not in self._node_ok_cache:
            node = self.node(node_id)
            ok = node is not None
            if ok and self.node_types is not None:
                ok = node.type in self.node_types
            if ok:
                ok = all(_condition_ok(node.attrs, c) for c in self.conditions)
            self._node_ok_cache[node_id] = ok
        return self._node_ok_cache[node_id]

    def check_nodes_exist(self) -> None:
        if self.node(self.start) is None:
            raise NodeNotFoundError(f"起点不存在: {self.start!r}")
        if self.node(self.end) is None:
            raise NodeNotFoundError(f"终点不存在: {self.end!r}")

    def run(self) -> list[PathResult]:
        self.check_nodes_exist()
        first = _Solver(self, frozenset({self.start})).best(self.start, self.max_depth)
        if first is None:
            return []
        first_conf = Fraction(first[0], _POW1000[first[1]])
        if first_conf < self.threshold:
            return []
        emitted: list[tuple] = []  # (conf, hops, ids, rels, millis)
        seen: set[tuple[tuple, tuple]] = set()
        heap: list[tuple] = []
        counter = itertools.count()

        def edge_millis(ids: tuple, rels: tuple) -> tuple[int, ...]:
            millis = []
            adj = {src: {(rel, dst): m for rel, dst, m in self.adj(src)} for src, dst, rel in zip(ids, ids[1:], rels)}
            for index, rel in enumerate(rels):
                millis.append(adj[ids[index]][(rel, ids[index + 1])])
            return tuple(millis)

        def accept(candidate: tuple) -> None:
            conf, hops, ids, rels = candidate
            emitted.append((conf, hops, ids, rels, edge_millis(ids, rels)))
            seen.add((ids, rels))

        accept((first_conf, first[1], first[2], first[3]))
        while len(emitted) < self.k:
            _, last_hops, last_ids, last_rels, last_millis = emitted[-1]
            # 前一路径的前缀置信度（Fraction，精确）
            prefix_confs = [Fraction(1)]
            for milli in last_millis:
                prefix_confs.append(prefix_confs[-1] * Fraction(milli, 1000))
            for i in range(last_hops):
                prefix_ids = last_ids[: i + 1]
                prefix_rels = last_rels[:i]
                banned = frozenset(
                    (rels[i], ids[i + 1])
                    for _, _, ids, rels, _ in emitted
                    if len(rels) > i
                    and ids[: i + 1] == prefix_ids
                    and rels[:i] == prefix_rels
                )
                solver = _Solver(self, frozenset(prefix_ids))
                sub = solver.best_via_edges(last_ids[i], self.max_depth - i, banned)
                if sub is None:
                    continue
                conf = prefix_confs[i] * Fraction(sub[0], _POW1000[sub[1]])
                if conf < self.threshold:
                    continue
                ids = last_ids[:i] + sub[2]
                rels = last_rels[:i] + sub[3]
                if (ids, rels) in seen:
                    continue
                seen.add((ids, rels))
                key = (-conf, i + sub[1], ids, rels, next(counter))
                heapq.heappush(heap, (key, conf, i + sub[1], ids, rels))
            if not heap:
                break
            _, conf, hops, ids, rels = heapq.heappop(heap)
            accept((conf, hops, ids, rels))

        return [self._to_result(item) for item in emitted[: self.k]]

    def _to_result(self, item: tuple) -> PathResult:
        conf, hops, ids, rels, millis = item
        nodes = tuple(self.node(node_id) for node_id in ids)
        edges = tuple(
            PathEdge(relation=rel, confidence=format_milli(milli))
            for rel, milli in zip(rels, millis)
        )
        scaled = conf * (1000**hops)
        confidence = format_product(scaled.numerator, hops)
        return PathResult(nodes=nodes, edges=edges, hops=hops, confidence=confidence)


def find_paths(
    store: GraphStore,
    start: str,
    end: str,
    max_depth: int,
    k: int,
    min_confidence: Any = 0,
    allowed_relations: Iterable[str] | None = None,
    node_types: Iterable[str] | None = None,
    node_conditions: Iterable[AttrCondition] | None = None,
) -> list[PathResult]:
    """查询 start 到 end 的前 K 条最可信路径。

    排序：置信度降序；跳数升序；途经节点 id 序列按码点升序；关系类型序列升序。
    """
    return _Query(
        store,
        start,
        end,
        max_depth,
        k,
        min_confidence,
        allowed_relations,
        node_types,
        node_conditions,
    ).run()
