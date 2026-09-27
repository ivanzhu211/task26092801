"""自定义异常类型。"""


class KGPathError(Exception):
    """本模块所有错误的基类。"""


class ValidationError(KGPathError):
    """写入数据或查询参数不合法。"""


class NodeNotFoundError(KGPathError):
    """查询的起点或终点不存在。"""
