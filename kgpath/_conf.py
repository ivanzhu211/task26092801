"""置信度的精确十进制表示与运算。

内部统一用整数 milli（千分之一）表示一个 0..1 的置信度，
路径置信度用 (分子, 跳数) 表示：值 = 分子 / 1000 ** 跳数。
所有比较与格式化都是精确十进制，不经过浮点。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from .errors import ValidationError

SCALE = 1000  # 每条边最多三位小数


def _to_decimal(value: Any, what: str) -> Decimal:
    if isinstance(value, bool):
        raise ValidationError(f"{what} 类型不合法: {value!r}")
    if isinstance(value, Decimal):
        dec = value
    elif isinstance(value, int):
        dec = Decimal(value)
    elif isinstance(value, float):
        dec = Decimal(str(value))
    elif isinstance(value, str):
        try:
            dec = Decimal(value.strip())
        except InvalidOperation:
            raise ValidationError(f"{what} 不是合法数字: {value!r}") from None
    else:
        raise ValidationError(f"{what} 类型不合法: {value!r}")
    if not dec.is_finite():
        raise ValidationError(f"{what} 不是有限数字: {value!r}")
    return dec


def _to_milli(value: Any, what: str, allow_zero: bool) -> int:
    dec = _to_decimal(value, what)
    milli = dec * SCALE
    if milli != milli.to_integral_value():
        raise ValidationError(f"{what} 最多三位小数: {value!r}")
    result = int(milli)
    low = 0 if allow_zero else 1
    if not (low <= result <= SCALE):
        lo_text = "0" if allow_zero else "大于 0"
        raise ValidationError(f"{what} 必须在 {lo_text} 到 1 之间: {value!r}")
    return result


def parse_confidence(value: Any) -> int:
    """边置信度 -> milli，要求 0 < c <= 1 且最多三位小数。"""
    return _to_milli(value, "边置信度", allow_zero=False)


def parse_threshold(value: Any) -> int:
    """路径置信度下限 -> milli，要求 0 <= c <= 1 且最多三位小数。"""
    return _to_milli(value, "置信度下限", allow_zero=True)


def format_milli(milli: int) -> str:
    """单条边置信度的精确十进制字符串，如 700 -> '0.7'，1000 -> '1'。"""
    return format_product(milli, 1)


def format_product(numerator: int, hops: int) -> str:
    """路径置信度 numerator / 1000**hops 的精确十进制字符串，去掉末尾多余的 0。"""
    if numerator == 0:
        return "0"
    digits = str(numerator)
    frac_len = 3 * hops
    if len(digits) <= frac_len:
        digits = "0" * (frac_len - len(digits) + 1) + digits
    point = len(digits) - frac_len
    int_part = digits[:point]
    frac_part = digits[point:].rstrip("0")
    if frac_part:
        return f"{int_part}.{frac_part}"
    return int_part
