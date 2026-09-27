"""SQLite 持久化：节点与边的批量写入、按起点取边。"""

from __future__ import annotations

import json
import math
import sqlite3
from typing import Any, Iterable

from ._conf import format_milli, parse_confidence
from .errors import ValidationError
from .types import Edge, Node

MAX_BATCH = 5000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id    TEXT PRIMARY KEY,
    type  TEXT NOT NULL,
    attrs TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS edges (
    src  TEXT NOT NULL,
    rel  TEXT NOT NULL,
    dst  TEXT NOT NULL,
    conf INTEGER NOT NULL,
    PRIMARY KEY (src, rel, dst)
);
"""


def _validate_node(node: Node) -> tuple[str, str, str]:
    if not isinstance(node.id, str) or not node.id:
        raise ValidationError(f"节点 id 必须是非空字符串: {node.id!r}")
    if not isinstance(node.type, str):
        raise ValidationError(f"节点 {node.id!r} 的实体类型必须是字符串: {node.type!r}")
    attrs = node.attrs or {}
    if not isinstance(attrs, dict):
        raise ValidationError(f"节点 {node.id!r} 的属性必须是 dict: {attrs!r}")
    for key, value in attrs.items():
        if not isinstance(key, str):
            raise ValidationError(f"节点 {node.id!r} 的属性名必须是字符串: {key!r}")
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ValidationError(
                f"节点 {node.id!r} 的属性 {key!r} 的值必须是字符串或数字: {value!r}"
            )
        if isinstance(value, float) and not math.isfinite(value):
            raise ValidationError(
                f"节点 {node.id!r} 的属性 {key!r} 的值必须是有限数字: {value!r}"
            )
    return node.id, node.type, json.dumps(attrs, ensure_ascii=False, sort_keys=True)


class GraphStore:
    """知识图谱存储。图数据保存在本地 SQLite 文件中。"""

    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None

    def open(self) -> "GraphStore":
        if self._conn is None:
            self._conn = sqlite3.connect(self._path)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(_SCHEMA)
        return self

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "GraphStore":
        return self.open()

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _require_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise ValidationError("GraphStore 尚未 open()")
        return self._conn

    @staticmethod
    def _check_batch(items: list[Any], what: str) -> None:
        if len(items) > MAX_BATCH:
            raise ValidationError(f"{what} 一批最多 {MAX_BATCH} 条，实际 {len(items)} 条")

    def write_nodes(self, nodes: Iterable[Node]) -> None:
        """批量写入节点；同 id 覆盖。任一条不合法则整批不生效。"""
        conn = self._require_conn()
        items = list(nodes)
        self._check_batch(items, "节点")
        rows = [_validate_node(node) for node in items]
        with conn:
            conn.executemany(
                "INSERT OR REPLACE INTO nodes (id, type, attrs) VALUES (?, ?, ?)",
                rows,
            )

    def write_edges(self, edges: Iterable[Edge]) -> None:
        """批量写入边；(src, relation, dst) 相同则覆盖置信度。任一条不合法则整批不生效。"""
        conn = self._require_conn()
        items = list(edges)
        self._check_batch(items, "边")
        rows = []
        for edge in items:
            for field_name, field_value in (
                ("起点", edge.src),
                ("关系类型", edge.relation),
                ("终点", edge.dst),
            ):
                if not isinstance(field_value, str) or not field_value:
                    raise ValidationError(f"边的{field_name}必须是非空字符串: {field_value!r}")
            rows.append((edge.src, edge.relation, edge.dst, parse_confidence(edge.confidence)))
        missing = self._missing_nodes({row[0] for row in rows} | {row[2] for row in rows})
        if missing:
            raise ValidationError(f"边指向不存在的节点: {sorted(missing)!r}")
        with conn:
            conn.executemany(
                "INSERT OR REPLACE INTO edges (src, rel, dst, conf) VALUES (?, ?, ?, ?)",
                rows,
            )

    def _missing_nodes(self, node_ids: set[str]) -> set[str]:
        conn = self._require_conn()
        missing: set[str] = set()
        ids = list(node_ids)
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            placeholders = ",".join("?" for _ in chunk)
            found = {
                row[0]
                for row in conn.execute(
                    f"SELECT id FROM nodes WHERE id IN ({placeholders})", chunk
                )
            }
            missing.update(set(chunk) - found)
        return missing

    def get_node(self, node_id: str) -> Node | None:
        conn = self._require_conn()
        row = conn.execute(
            "SELECT id, type, attrs FROM nodes WHERE id = ?", (node_id,)
        ).fetchone()
        if row is None:
            return None
        return Node(id=row[0], type=row[1], attrs=json.loads(row[2]))

    def out_edges(self, node_id: str) -> list[Edge]:
        """取节点的全部出边，置信度为精确十进制字符串。"""
        return [
            Edge(src=node_id, relation=rel, dst=dst, confidence=format_milli(milli))
            for rel, dst, milli in self._out_edges_milli(node_id)
        ]

    def _out_edges_milli(self, node_id: str) -> list[tuple[str, str, int]]:
        """取节点的全部出边：(关系类型, 终点, 置信度 milli)。查询引擎内部用。"""
        conn = self._require_conn()
        return conn.execute(
            "SELECT rel, dst, conf FROM edges WHERE src = ?", (node_id,)
        ).fetchall()
