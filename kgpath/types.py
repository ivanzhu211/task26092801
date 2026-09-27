"""对外暴露的数据类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Node:
    """实体节点。attrs 的值为 str / int / float。"""

    id: str
    type: str
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Edge:
    """有向语义边。confidence 为 0 < c <= 1、最多三位小数。"""

    src: str
    relation: str
    dst: str
    confidence: Any  # int / float / str / Decimal，写入时校验并归一化


@dataclass(frozen=True)
class PathEdge:
    """路径中经过的一条边。confidence 为精确十进制字符串。"""

    relation: str
    confidence: str


@dataclass(frozen=True)
class PathResult:
    """一条命中路径。confidence 为精确十进制字符串（去掉末尾多余的 0）。"""

    nodes: tuple[Node, ...]
    edges: tuple[PathEdge, ...]
    hops: int
    confidence: str


# 属性条件操作符
OP_EQ = "eq"
OP_NE = "ne"
OP_GT = "gt"
OP_LT = "lt"
VALID_OPS = (OP_EQ, OP_NE, OP_GT, OP_LT)


@dataclass(frozen=True)
class AttrCondition:
    """中间节点属性条件：attr op value。多条条件之间为与关系。"""

    attr: str
    op: str
    value: Any
