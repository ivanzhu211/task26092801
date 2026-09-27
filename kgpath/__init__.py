"""kgpath：基于 SQLite 的知识图谱路径查询库。"""

from .errors import KGPathError, NodeNotFoundError, ValidationError
from .query import find_paths
from .store import GraphStore
from .types import (
    OP_EQ,
    OP_GT,
    OP_LT,
    OP_NE,
    AttrCondition,
    Edge,
    Node,
    PathEdge,
    PathResult,
)

__all__ = [
    "AttrCondition",
    "Edge",
    "GraphStore",
    "KGPathError",
    "Node",
    "NodeNotFoundError",
    "OP_EQ",
    "OP_GT",
    "OP_LT",
    "OP_NE",
    "PathEdge",
    "PathResult",
    "ValidationError",
    "find_paths",
]
