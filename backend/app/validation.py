"""生产数据数量约束的统一业务校验与错误信封。

约束分三类：

* ``positive``      —— 必须大于零（塘口面积/水深、投苗数量与重量、投喂量、
  用药剂量、成本金额、销售重量与单价等业务事实）。
* ``non_negative``  —— 允许为零（水质检测读数、成本数量与单价等可选测量值）。
* ``reversible``    —— 业务写入仍须 >= 0；已签署（关闭）批次只允许通过冲正
  事实把净效果恢复到零，冲正分录本身允许为负，由工作流服务独占使用。

创建、修改与批量导入共用同一份规则与同一个错误信封，避免接口各自返回
不同形态的错误。
"""

import math
from typing import Any, Dict, Iterable, List, Mapping, Optional

# 规则常量
POSITIVE = "positive"          # 必须 > 0
NON_NEGATIVE = "non_negative"  # 必须 >= 0（允许为零）
POSITIVE_INT = "positive_int"  # 必须为 > 0 的整数

# 每个实体的字段规则。键即 ORM 模型名（models.py 中的类名）。
FIELD_RULES: Dict[str, Dict[str, str]] = {
    "Pond": {
        "area": POSITIVE,
        "water_depth": POSITIVE,
    },
    "StockingRecord": {
        "quantity": POSITIVE_INT,
        "weight_per_unit": NON_NEGATIVE,
        "total_weight": NON_NEGATIVE,
    },
    "FeedingRecord": {
        "feed_quantity": POSITIVE,
        "water_temperature": NON_NEGATIVE,
    },
    "WaterQualityRecord": {
        # 检测读数均为可选测量值：允许为零、允许缺省，但不允许为负。
        "water_temperature": NON_NEGATIVE,
        "ph_value": NON_NEGATIVE,
        "dissolved_oxygen": NON_NEGATIVE,
        "ammonia_nitrogen": NON_NEGATIVE,
        "nitrite": NON_NEGATIVE,
        "transparency": NON_NEGATIVE,
    },
    "MedicationRecord": {
        "dosage": POSITIVE,
    },
    "CostRecord": {
        "amount": POSITIVE,
        "quantity": NON_NEGATIVE,
        "unit_price": NON_NEGATIVE,
    },
    "HarvestSale": {
        "weight": POSITIVE,
        "unit_price": POSITIVE,
        "total_amount": NON_NEGATIVE,
    },
}

# 规则的中文说明，写入复核记录与错误响应。
RULE_DESCRIPTIONS = {
    POSITIVE: "必须大于0",
    POSITIVE_INT: "必须为大于0的整数",
    NON_NEGATIVE: "必须大于等于0（允许为零）",
}

# 创建时必填（不允许为 None）的字段。其余受约束字段为可选测量值，
# 缺省跳过校验，但一旦提供就必须满足其规则。
REQUIRED_FIELDS = {
    ("Pond", "area"),
    ("Pond", "water_depth"),
    ("StockingRecord", "quantity"),
    ("FeedingRecord", "feed_quantity"),
    ("CostRecord", "amount"),
    ("HarvestSale", "weight"),
    ("HarvestSale", "unit_price"),
}


class QuantityViolation(Exception):
    """业务数量约束违例。携带稳定的字段级错误列表。"""

    def __init__(self, errors: List[Dict[str, str]]):
        self.errors = errors
        super().__init__("; ".join(f"{e['field']}: {e['message']}" for e in errors))


class BatchValidationError(Exception):
    """批量导入中存在非法行。任何一行失败，整批都不得落库。"""

    def __init__(self, row_errors: List[Dict[str, Any]]):
        self.row_errors = row_errors
        super().__init__(f"{len(row_errors)} 行数据未通过校验")


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    return False


def validate_fields(
    model_name: str,
    values: Mapping[str, Any],
    *,
    partial: bool = False,
) -> List[Dict[str, str]]:
    """按实体规则校验给定字段，返回字段错误列表（空列表表示通过）。

    :param partial: 修改/部分导入场景，值为 ``None`` 的字段视为“未提供”，
        跳过校验；创建场景下必填字段的缺省由 schema 层负责。
    """
    rules = FIELD_RULES.get(model_name, {})
    errors: List[Dict[str, str]] = []
    for field, rule in rules.items():
        required = (model_name, field) in REQUIRED_FIELDS
        if field not in values:
            # 创建/导入场景下必填字段缺行也要报；其余靠 schema 层兜底。
            if not partial and required:
                errors.append({
                    "field": field,
                    "rule": rule,
                    "message": f"{field} 为必填项",
                })
            continue
        value = values[field]
        if value is None:
            if required:
                # 必填字段即便在修改场景也不允许显式置空
                errors.append({
                    "field": field,
                    "rule": rule,
                    "message": f"{field} 不能为空",
                })
            continue
        if not _is_finite_number(value):
            errors.append({
                "field": field,
                "rule": rule,
                "message": f"{field} 必须是有限数值",
            })
            continue
        if rule == POSITIVE_INT and (not isinstance(value, int) or value <= 0):
            errors.append({
                "field": field,
                "rule": rule,
                "message": f"{field} {RULE_DESCRIPTIONS[POSITIVE_INT]}",
            })
        elif rule == POSITIVE and value <= 0:
            errors.append({
                "field": field,
                "rule": rule,
                "message": f"{field} {RULE_DESCRIPTIONS[POSITIVE]}",
            })
        elif rule == NON_NEGATIVE and value < 0:
            errors.append({
                "field": field,
                "rule": rule,
                "message": f"{field} {RULE_DESCRIPTIONS[NON_NEGATIVE]}",
            })
    return errors


def validate_or_raise(model_name: str, values: Mapping[str, Any], *, partial: bool = False) -> None:
    """校验失败时抛出 :class:`QuantityViolation`。"""
    errors = validate_fields(model_name, values, partial=partial)
    if errors:
        raise QuantityViolation(errors)


def validate_batch(
    model_name: str,
    rows: Iterable[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """批量导入校验：逐行返回错误，全部通过时返回空列表。

    每项形如 ``{"index": 行号, "errors": [字段错误...]}``，供批量接口
    一次性返回全部问题；任何一行失败都不得落库。
    """
    row_errors: List[Dict[str, Any]] = []
    for index, row in enumerate(rows):
        errors = validate_fields(model_name, row, partial=False)
        if errors:
            row_errors.append({"index": index, "errors": errors})
    return row_errors


def error_envelope(code: str, message: str, errors: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
    """构造稳定的失败响应体。"""
    body: Dict[str, Any] = {"code": code, "message": message}
    if errors is not None:
        body["errors"] = errors
    return body
