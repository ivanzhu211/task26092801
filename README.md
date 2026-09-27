# kgpath：知识图谱路径查询库

基于 Python + SQLite 的知识图谱路径查询模块，以库的形式提供给上层数据编织平台调用，无接口层。图数据持久化在本地 SQLite 文件中。

## 功能

- 节点：字符串 id、实体类型、属性（值为字符串或数字）；同 id 写入覆盖。
- 边：有向，由（起点、关系类型、终点）唯一确定，带置信度（0 < c ≤ 1，最多三位小数）；重复写入覆盖置信度；支持平行边与自环。
- 节点和边按批写入（每批最多 5000 条），批内任一条不合法（含边指向不存在的节点）则整批不生效。
- 路径查询：给定起点、终点、最大深度（1–10）、返回条数 K（1–20）、路径置信度下限，以及可选的关系类型过滤、中间节点实体类型过滤、中间节点属性条件（eq / ne / gt / lt，多条取与）。
- 路径置信度为各边置信度乘积，所有比较按精确十进制进行（内部用整数千分位与 `Fraction`，无浮点误差）。
- 返回前 K 条，排序：置信度降序 → 跳数升序 → 途经节点 id 码点序 → 关系类型序列升序。同一查询多次执行结果完全一致。

## 用法

```python
from kgpath import GraphStore, Node, Edge, AttrCondition, find_paths

with GraphStore("graph.db") as store:
    store.write_nodes([
        Node("alice", "Person", {"age": 34, "city": "shanghai"}),
        Node("acme", "Company", {"size": 5000}),
    ])
    store.write_edges([
        Edge("alice", "works_at", "acme", 0.97),
    ])

    paths = find_paths(
        store, "alice", "acme",
        max_depth=5, k=10, min_confidence=0.1,
        allowed_relations={"works_at", "knows"},      # 可选
        node_types={"Person", "Company"},             # 可选，仅约束中间节点
        node_conditions=[AttrCondition("age", "gt", 30)],  # 可选，仅约束中间节点
    )
    for p in paths:
        print(p.confidence, p.hops, [n.id for n in p.nodes],
              [(e.relation, e.confidence) for e in p.edges])
```

`PathResult.confidence` 与 `PathEdge.confidence` 都是精确十进制字符串，去掉末尾多余的 0（如 `"0.07"`、`"1"`）。

## 错误

- `NodeNotFoundError`：起点或终点不存在。
- `ValidationError`：起点等于终点、参数越界、写入数据不合法等。

## 实现要点

- 存储：SQLite 单文件，`edges` 以 `(src, rel, dst)` 为主键，按起点取边走主键索引；查询只触及实际搜到的那部分图，不做全图加载。
- 查询：Yen 式 K 最优简单路径枚举。利用「前缀拼接保持排序键」的性质，把「剩余若干跳内的最优补全」做成按 (节点, 剩余跳数) 记忆化的 DP，每个分叉（spur）只在实际波及的分量上做 DP，因此分层全连接、稠密簇等场景都不需要枚举全部路径。
- 精确十进制：边置信度存整数千分位，路径置信度以 `分子 / 1000^跳数` 表示，比较走交叉相乘，恰好等于下限的路径不会漏。

## 测试

```bash
python3 -m pytest tests/ -q
```

- `tests/test_correctness.py`：300 个随机小图 × 多组随机查询，与暴力枚举全部简单路径的结果逐条比较（覆盖置信度并列、平行边、置信度 1、路径置信度恰好等于下限、中间节点过滤、关系类型过滤）。
- `tests/test_unit.py`：业务规则单元测试（批量原子性、覆盖语义、自环、参数校验、持久化、精确十进制输出等）。
- `tests/test_performance.py`：三个规模场景并断言耗时（100 万边局部查询 < 0.5s 含首次查询；近两百万条路径的分层图 < 2s；高置信度稠密簇 < 2s）。
