"""实体注册表：数量规则键与 ORM 模型的对应，以及有效版本的读取口径。

详情、追溯、周期分析三处统一通过 :func:`effective_attrs` 读取字段，保证它们
共享同一个“有效版本”：

- 未签署批次（或塘口）上经批准的更正 → 直接呈现更正值；
- 已签署批次上的冲正 → 被冲平的可归零字段呈现为 0（原事实保留在底表，
  流水里可查），其余字段维持原值；
- 尚未处置的旧库异常值仍按原值呈现，但已带有 open 复核记录，不会被静默吞掉。
"""

from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy.orm import Session

from ..models import (
    Pond,
    StockingRecord,
    FeedingRecord,
    WaterQualityRecord,
    MedicationRecord,
    CostRecord,
    HarvestSale,
    RecordCorrection,
)

ENTITY_MODELS: Dict[str, type] = {
    "pond": Pond,
    "stocking": StockingRecord,
    "feeding": FeedingRecord,
    "water_quality": WaterQualityRecord,
    "medication": MedicationRecord,
    "cost": CostRecord,
    "harvest": HarvestSale,
}

# 各实体记录所属的批次列（塘口没有批次）。
BATCH_ATTR = {
    "pond": None,
    "stocking": "batch_id",
    "feeding": "batch_id",
    "water_quality": "batch_id",
    "medication": "batch_id",
    "cost": "batch_id",
    "harvest": "batch_id",
}


def batch_id_of(entity: str, obj: Any) -> Optional[int]:
    attr = BATCH_ATTR[entity]
    return getattr(obj, attr) if attr else None


def load_applied_corrections(
    db: Session, entity: str, record_ids: Iterable[int]
) -> Dict[Tuple[str, int, str], RecordCorrection]:
    """批量加载若干记录上已落账的更正/冲正流水。"""
    ids = list(record_ids)
    if not ids:
        return {}
    rows = (
        db.query(RecordCorrection)
        .filter(
            RecordCorrection.entity == entity,
            RecordCorrection.record_id.in_(ids),
            RecordCorrection.status == "applied",
        )
        .all()
    )
    return {(r.entity, r.record_id, r.field): r for r in rows}


def applied_corrections_for_records(
    db: Session, records: List[Any], entity: str
) -> Dict[Tuple[str, int, str], RecordCorrection]:
    return load_applied_corrections(db, entity, [r.id for r in records])


def effective_attr(obj: Any, field: str, correction: Optional[RecordCorrection]) -> Any:
    """单个字段的有效值。"""
    if correction is not None and correction.status == "applied":
        return correction.after_value
    return getattr(obj, field)


def effective_attrs(
    obj: Any,
    entity: str,
    corrections: Dict[Tuple[str, int, str], RecordCorrection],
    fields: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """取一条记录在当前有效版本下的字段值。"""
    if fields is None:
        fields = [c.name for c in obj.__table__.columns]
    result: Dict[str, Any] = {}
    for field in fields:
        correction = corrections.get((entity, obj.id, field))
        result[field] = effective_attr(obj, field, correction)
    return result


def apply_effective_to_obj(obj: Any, entity: str, corrections: Dict[Tuple[str, int, str], RecordCorrection]) -> Any:
    """把有效值覆盖到 ORM 实例上（仅供 response_model 序列化前使用，不入库）。"""
    for (ent, _rid, field), correction in corrections.items():
        if ent == entity and _rid == obj.id and correction.status == "applied":
            setattr(obj, field, correction.after_value)
    return obj


def detached_copy(obj: Any) -> Any:
    """生成同类型的游离（transient）副本，setattr 不会被 session 落库。"""
    clone = obj.__class__()
    for column in obj.__table__.columns:
        setattr(clone, column.name, getattr(obj, column.name))
    return clone


def effective_copy(
    obj: Any,
    entity: str,
    corrections: Dict[Tuple[str, int, str], RecordCorrection],
) -> Any:
    """返回应用了有效版本值的游离副本，供详情/追溯/分析序列化使用。"""
    clone = detached_copy(obj)
    for (ent, rid, field), correction in corrections.items():
        if ent == entity and rid == obj.id and correction.status == "applied":
            setattr(clone, field, correction.after_value)
    return clone
