"""异常类型定义。"""


class KGError(Exception):
    """知识图谱模块所有异常的基类。"""


class ValidationError(KGError):
    """输入数据或查询参数不合法。"""


class NodeNotFoundError(KGError):
    """查询的起点或终点不存在。"""
