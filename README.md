# task26092801

# kgpath：知识图谱 Top-K 路径查询库

基于 Python + SQLite 的知识图谱路径查询模块，以库的形式提供给上层数据编织平台调用，无接口层、无外部服务依赖，图数据持久化在本地 SQLite 文件中。

## 数据模型

- **节点**：字符串 `id`、实体类型 `type`、属性字典 `attrs`（值为字符串或数字）。同一 `id` 再次写入时覆盖。
- **边**：有向，由 `(src, rel, dst)` 唯一确定，带置信度（大于 0、不超过 1、最多三位小数）。重复写入覆盖置信度。允许平行边（不同关系类型）与自环。
- 节点和边均按批写入，一批最多 5000 条；批内任何一条不合法（含边指向不存在的节点）则整批不生效。

## 用法

```python
from kgpath import GraphStore, find_paths, AttrCondition

with GraphStore("graph.db") as g:
    g.write_nodes([
        {"id": "alice", "type": "Person", "attrs": {"age": 30}},
        {"id": "acme",  "type": "Company"},
    ])
    g.write_edges([
        {"src": "alice", "rel": "works_at", "dst": "acme", "confidence": 0.95},
    ])

    paths = find_paths(
        g, "alice", "acme",
        max_depth=5,          # 1..10 跳
        k=10,                 # 1..20 条
        min_confidence=0.07,  # 0..1，最多三位小数
        rel_types={"works_at", "knows"},      # 可选：允许的关系类型
        mid_types={"Person"},                  # 可选：中间节点实体类型
        attr_conditions=[AttrCondition("age", "gt", 25)],  # 可选：中间节点属性条件
    )
    for p in paths:
        print(p.confidence, p.hops,
              [n.id for n in p.nodes],
              [(e.rel_type, e.confidence) for e in p.edges])
```

- 路径只顺边方向走，不重复经过节点；节点序列相同但边不同算不同路径。
- 路径置信度 = 各边置信度乘积，精确十进制计算（内部用整数千分位，无浮点误差），
  返回去尾零的十进制字符串，如 `"0.07"`、`"1"`。
- 排序：置信度降序 → 跳数升序 → 节点 id 序列码点序 → 关系类型序列；同一查询多次运行结果完全一致。
- 属性条件支持 `eq` / `ne` / `gt` / `lt`，多条同时满足；属性缺失或类型不匹配一律不满足，`gt`/`lt` 只对数字生效。
- 起点/终点不存在抛 `NodeNotFoundError`；起点等于终点、参数越界、数据不合法抛 `ValidationError`。

## 性能设计

- 查询耗时只取决于实际波及的图区域：反向 DP 预计算到终点的置信度上界（带预算上限），
  第一遍按上界 best-first 搜索并用第 K 大置信度剪枝，第二遍按字典序枚举并列路径。
- 百万边规模下局部查询 < 0.5s（含打开后首次查询）；约 195 万条候选路径的分层图、
  高置信度团陷阱图上的 Top-10 查询均在毫秒级返回。

## 测试

```bash
python3 -m pytest tests/ -q
```

- `tests/test_correctness.py`：400 个随机小图上与暴力枚举全部简单路径逐条对比，
  覆盖置信度并列、平行边、置信度 1、恰好等于下限、中间节点过滤、关系类型过滤、
  参数报错、持久化与确定性。
- `tests/test_performance.py`：三个规模场景（百万边局部查询、分层全连接、团陷阱），断言耗时。
