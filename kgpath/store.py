"""SQLite 存储层：建表、批量写入节点与边、按需读取。"""

from __future__ import annotations

import json
import sqlite3
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Optional

from .errors import ValidationError
from .models import SCALE, Node

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
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst);
"""


def parse_confidence(value: Any) -> int:
    """把 0 < v <= 1、最多三位小数的置信度转成整数千分位 (1..1000)。"""
    if isinstance(value, bool):
        raise ValidationError(f"置信度类型非法: {value!r}")
    if isinstance(value, (int, float, str, Decimal)):
        try:
            dec = Decimal(str(value))
        except InvalidOperation:
            raise ValidationError(f"置信度不是合法数值: {value!r}") from None
    else:
        raise ValidationError(f"置信度类型非法: {value!r}")
    if not dec.is_finite() or dec <= 0 or dec > 1:
        raise ValidationError(f"置信度必须在 (0, 1] 内: {value!r}")
    milli = dec * SCALE
    if milli != milli.to_integral_value():
        raise ValidationError(f"置信度最多三位小数: {value!r}")
    return int(milli)


def _validate_attrs(attrs: Any) -> dict[str, Any]:
    if not isinstance(attrs, dict):
        raise ValidationError("节点属性必须是 dict")
    for key, val in attrs.items():
        if not isinstance(key, str):
            raise ValidationError(f"属性名必须是字符串: {key!r}")
        if isinstance(val, bool) or not isinstance(val, (str, int, float)):
            raise ValidationError(f"属性值必须是字符串或数字: {key}={val!r}")
        if isinstance(val, float) and val != val:  # NaN
            raise ValidationError(f"属性值不能是 NaN: {key}")
    return dict(attrs)


class GraphStore:
    """本地 SQLite 文件中的知识图谱。"""

    def __init__(self, path: str) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "GraphStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---------------- 写入 ----------------

    def write_nodes(self, nodes: Iterable[dict]) -> None:
        items = list(nodes)
        if len(items) > MAX_BATCH:
            raise ValidationError(f"一批最多 {MAX_BATCH} 条")
        rows = []
        for item in items:
            try:
                nid, ntype = item["id"], item["type"]
                attrs = _validate_attrs(item.get("attrs", {}))
            except KeyError as e:
                raise ValidationError(f"节点缺少字段: {e}") from None
            if not isinstance(nid, str) or not nid:
                raise ValidationError(f"节点 id 必须是非空字符串: {nid!r}")
            if not isinstance(ntype, str):
                raise ValidationError(f"节点 type 必须是字符串: {ntype!r}")
            rows.append((nid, ntype, json.dumps(attrs, sort_keys=True)))
        with self._conn:
            self._conn.executemany(
                "INSERT OR REPLACE INTO nodes (id, type, attrs) VALUES (?, ?, ?)",
                rows,
            )

    def write_edges(self, edges: Iterable[dict]) -> None:
        items = list(edges)
        if len(items) > MAX_BATCH:
            raise ValidationError(f"一批最多 {MAX_BATCH} 条")
        rows = []
        endpoints: set[str] = set()
        for item in items:
            try:
                src, rel, dst = item["src"], item["rel"], item["dst"]
            except KeyError as e:
                raise ValidationError(f"边缺少字段: {e}") from None
            for name, val in (("src", src), ("rel", rel), ("dst", dst)):
                if not isinstance(val, str) or not val:
                    raise ValidationError(f"边的 {name} 必须是非空字符串: {val!r}")
            milli = parse_confidence(item["confidence"])
            rows.append((src, rel, dst, milli))
            endpoints.add(src)
            endpoints.add(dst)
        if endpoints:
            marks = ",".join("?" * len(endpoints))
            found = {
                r[0]
                for r in self._conn.execute(
                    f"SELECT id FROM nodes WHERE id IN ({marks})", tuple(endpoints)
                )
            }
            missing = endpoints - found
            if missing:
                raise ValidationError(f"边指向不存在的节点: {sorted(missing)!r}")
        with self._conn:
            self._conn.executemany(
                "INSERT OR REPLACE INTO edges (src, rel, dst, conf) VALUES (?, ?, ?, ?)",
                rows,
            )

    # ---------------- 读取（供查询引擎按需调用） ----------------

    def get_node(self, node_id: str) -> Optional[Node]:
        row = self._conn.execute(
            "SELECT id, type, attrs FROM nodes WHERE id = ?", (node_id,)
        ).fetchone()
        if row is None:
            return None
        return Node(id=row[0], type=row[1], attrs=json.loads(row[2]))

    def get_outgoing(self, src: str) -> list[tuple[str, str, int]]:
        """(dst, rel, conf_milli) 列表。"""
        return self._conn.execute(
            "SELECT dst, rel, conf FROM edges WHERE src = ?", (src,)
        ).fetchall()

    def get_incoming(self, dst: str) -> list[tuple[str, int]]:
        """(src, conf_milli) 列表。"""
        return self._conn.execute(
            "SELECT src, conf FROM edges WHERE dst = ?", (dst,)
        ).fetchall()
