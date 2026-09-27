"""数据模型与精确置信度表示。"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from typing import Any

SCALE = 1000  # 置信度以千分之一为单位存储


@functools.total_ordering
class Conf:
    """精确置信度：value = num / 1000**k。

    num 为非负整数，0 <= k <= 10（路径最多 10 跳）。
    比较与乘法全程使用整数运算，避免浮点误差。
    """

    __slots__ = ("num", "k")

    def __init__(self, num: int, k: int) -> None:
        self.num = num
        self.k = k

    def __repr__(self) -> str:
        return f"Conf({self.num}, {self.k})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Conf):
            return NotImplemented
        return self.num * SCALE ** other.k == other.num * SCALE ** self.k

    def __lt__(self, other: "Conf") -> bool:
        return self.num * SCALE ** other.k < other.num * SCALE ** self.k

    def __le__(self, other: "Conf") -> bool:
        return self.num * SCALE ** other.k <= other.num * SCALE ** self.k

    def __hash__(self) -> int:
        return hash(self.to_text())

    def __mul__(self, other: "Conf") -> "Conf":
        return Conf(self.num * other.num, self.k + other.k)

    def to_text(self) -> str:
        """精确十进制字符串，去掉末尾多余的 0，如 '0.07'、'1'、'0'。"""
        if self.num == 0:
            return "0"
        digits = str(self.num)
        point = len(digits) - 3 * self.k
        if point > 0:
            int_part, frac_part = digits[:point], digits[point:]
        else:
            int_part, frac_part = "0", "0" * (-point) + digits
        frac_part = frac_part.rstrip("0")
        return int_part if not frac_part else f"{int_part}.{frac_part}"


@dataclass
class Node:
    id: str
    type: str
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class AttrCondition:
    attr: str
    op: str  # "eq" | "ne" | "gt" | "lt"
    value: Any


@dataclass
class EdgeResult:
    rel_type: str
    confidence: str  # 精确十进制字符串


@dataclass
class PathResult:
    nodes: list[Node]
    edges: list[EdgeResult]
    hops: int
    confidence: str  # 路径置信度的精确十进制字符串
