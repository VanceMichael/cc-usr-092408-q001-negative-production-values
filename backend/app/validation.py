"""生产数据数量约束的单一事实来源。

创建、修改、批量导入三条写入路径都调用 :func:`validate_values`，保证同一字段
在任何入口执行同一套业务校验。规则分三类：

``POSITIVE``
    必须大于零。塘口面积/水深、投苗数量与重量、单价等：零和负数都拒绝。

``NON_NEGATIVE``
    允许为零、拒绝负数。水质检测读数（溶解氧、氨氮、透明度……）允许 0。

``VOIDABLE``
    直接写入时与 ``POSITIVE`` 相同；只有复核服务以冲正/撤销事实落账时
    （``allow_void=True``）才允许取 0，用于把一笔已签署记录的有效贡献冲平。
"""

import math
from typing import Any, Dict, List, Optional, Tuple

from .errors import ValidationError

POSITIVE = "positive"
NON_NEGATIVE = "non_negative"
VOIDABLE = "voidable"

# 每条规则：(规则, 中文名称, 类型 int/float/None, 最小值, 最大值)
_Spec = Tuple[str, str, Optional[type], Optional[float], Optional[float]]

FIELD_RULES: Dict[str, Dict[str, _Spec]] = {
    "pond": {
        "area": (POSITIVE, "塘口面积(亩)", float, None, None),
        "water_depth": (POSITIVE, "水深(米)", float, None, None),
    },
    "stocking": {
        "quantity": (VOIDABLE, "投苗数量(尾)", int, None, None),
        "weight_per_unit": (POSITIVE, "单重(克/尾)", float, None, None),
        "total_weight": (VOIDABLE, "总重量(公斤)", float, None, None),
    },
    "feeding": {
        "feed_quantity": (VOIDABLE, "投喂量(公斤)", float, None, None),
        "water_temperature": (NON_NEGATIVE, "水温(℃)", float, None, 50),
    },
    "water_quality": {
        "water_temperature": (NON_NEGATIVE, "水温(℃)", float, None, 50),
        "ph_value": (NON_NEGATIVE, "pH值", float, 0, 14),
        "dissolved_oxygen": (NON_NEGATIVE, "溶解氧(mg/L)", float, None, None),
        "ammonia_nitrogen": (NON_NEGATIVE, "氨氮(mg/L)", float, None, None),
        "nitrite": (NON_NEGATIVE, "亚硝酸盐(mg/L)", float, None, None),
        "transparency": (NON_NEGATIVE, "透明度(cm)", float, None, None),
    },
    "medication": {
        "dosage": (VOIDABLE, "用药剂量", float, None, None),
    },
    "cost": {
        "amount": (VOIDABLE, "费用金额(元)", float, None, None),
        "quantity": (VOIDABLE, "费用数量", float, None, None),
        "unit_price": (POSITIVE, "单价", float, None, None),
    },
    "harvest": {
        "weight": (VOIDABLE, "销售重量(公斤)", float, None, None),
        "unit_price": (POSITIVE, "销售单价(元/公斤)", float, None, None),
        "total_amount": (VOIDABLE, "销售总金额(元)", float, None, None),
    },
}

# 字段异常会波及的周期指标，用于复核记录的“影响”分类。
FIELD_IMPACT: Dict[str, Dict[str, List[str]]] = {
    "pond": {
        "area": ["yield_per_mu"],
        "water_depth": ["pond_capacity"],
    },
    "stocking": {
        "quantity": ["survival_rate"],
        "weight_per_unit": ["survival_rate"],
        "total_weight": ["survival_rate"],
    },
    "feeding": {
        "feed_quantity": ["feed_conversion_ratio"],
    },
    "water_quality": {
        "water_temperature": ["traceability"],
        "ph_value": ["traceability"],
        "dissolved_oxygen": ["traceability"],
        "ammonia_nitrogen": ["traceability"],
        "nitrite": ["traceability"],
        "transparency": ["traceability"],
    },
    "medication": {
        "dosage": ["traceability"],
    },
    "cost": {
        "amount": ["total_cost", "profit"],
        "quantity": ["total_cost"],
        "unit_price": ["total_cost", "profit"],
    },
    "harvest": {
        "weight": ["yield_per_mu", "survival_rate"],
        "unit_price": ["total_revenue", "profit"],
        "total_amount": ["total_revenue", "profit"],
    },
}

# 实体中文名与对应 ORM 表名（启动扫描复用）。
ENTITY_LABELS = {
    "pond": "塘口",
    "stocking": "投苗记录",
    "feeding": "投喂记录",
    "water_quality": "水质检测记录",
    "medication": "用药记录",
    "cost": "成本记录",
    "harvest": "销售记录",
}


def _is_real_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return not (isinstance(value, float) and (math.isnan(value) or math.isinf(value)))


def check_field(entity: str, field: str, value: Any, allow_void: bool = False) -> Optional[str]:
    """返回违规码；合法返回 None。"""
    spec = FIELD_RULES[entity].get(field)
    if spec is None or value is None:
        return None
    rule, _label, expected_type, minimum, maximum = spec

    if not _is_real_number(value):
        return "not_a_number"
    if expected_type is int:
        if isinstance(value, int):
            pass
        elif isinstance(value, float) and float(value).is_integer():
            pass  # 复核请求以 float 承载的整数值（如 5000.0）视为合法整数
        else:
            return "not_an_integer"

    if minimum is not None and value < minimum:
        return "below_min"
    if maximum is not None and value > maximum:
        return "above_max"

    if rule == NON_NEGATIVE:
        if value < 0:
            return "negative_value"
        return None

    # POSITIVE / VOIDABLE：直接写入必须严格大于零
    if value < 0:
        return "negative_value"
    if value == 0 and not (rule == VOIDABLE and allow_void):
        return "zero_not_allowed"
    return None


_CODE_MESSAGES = {
    "not_a_number": "必须是有限数字",
    "not_an_integer": "必须是整数",
    "negative_value": "不允许为负数",
    "zero_not_allowed": "必须大于零，归零只能通过批准的冲正/撤销",
    "below_min": "低于允许的最小值",
    "above_max": "超出允许的最大值",
}


def validate_values(
    entity: str,
    values: Dict[str, Any],
    prefix: str = "",
    allow_void: bool = False,
    raise_on_error: bool = True,
) -> List[Dict[str, str]]:
    """校验一个写入载荷中受约束的字段。

    :param prefix: 批量导入时传 ``items[2].``，错误字段路径保持稳定。
    :returns: 字段错误列表 [{"field","code","message"}]；raise_on_error 时
              非空即抛 :class:`ValidationError`。
    """
    errors: List[Dict[str, str]] = []
    for field, (rule, label, *_rest) in FIELD_RULES.get(entity, {}).items():
        if field not in values or values[field] is None:
            continue
        code = check_field(entity, field, values[field], allow_void=allow_void)
        if code:
            message = _CODE_MESSAGES[code]
            if code in ("negative_value", "zero_not_allowed"):
                message = f"{label}{message}"
            errors.append(
                {
                    "field": f"{prefix}{field}",
                    "code": code,
                    "message": message,
                }
            )
    if errors and raise_on_error:
        raise ValidationError(
            f"{ENTITY_LABELS.get(entity, entity)}数据校验未通过", fields=errors
        )
    return errors
