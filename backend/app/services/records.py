"""数量类记录的统一写入路径，供各路由在创建/修改/批量导入时复用。

固定顺序：业务数量校验 → 批次存在且未签署 → 释放同字段旧更正 → 落库 →
推进批次数据版本（使周期分析快照失效）。任何一步失败都抛 :class:`FieldError`，
由全局处理器渲染为稳定字段错误。
"""

from typing import Any, Dict, List, Type

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..errors import ConflictError, NotFoundError, ValidationError
from ..models import Batch
from ..validation import FIELD_RULES, validate_values
from .entities import ENTITY_MODELS, applied_corrections_for_records, effective_copy
from .guards import bump_batch_version, ensure_batch_writable, release_field_corrections


def validate_batch_ref(db: Session, batch_id: Any, prefix: str, errors: List[Dict[str, str]]) -> bool:
    """批量导入预检查：批次存在且未签署，错误累积而非立即抛出。"""
    if not isinstance(batch_id, int) or isinstance(batch_id, bool):
        errors.append({
            "field": f"{prefix}batch_id",
            "code": "not_an_integer",
            "message": "批次ID必须是整数",
        })
        return False
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if batch is None:
        errors.append({
            "field": f"{prefix}batch_id",
            "code": "not_found",
            "message": "批次不存在",
        })
        return False
    if batch.signed_at is not None:
        errors.append({
            "field": f"{prefix}batch_id",
            "code": "batch_signed",
            "message": f"批次 {batch.batch_number} 已签署冻结，不能写入",
        })
        return False
    return True


def create_record(db: Session, entity: str, data: Dict[str, Any],
                  derive=None) -> Any:
    validate_values(entity, data)
    ensure_batch_writable(db, data.get("batch_id"))
    if derive is not None:
        data = derive(data, None)
    record = ENTITY_MODELS[entity](**data)
    db.add(record)
    _flush_or_convert(db)
    bump_batch_version(db, data["batch_id"])
    db.commit()
    db.refresh(record)
    return record


def bulk_create_records(db: Session, entity: str, items: List[Dict[str, Any]],
                        derive=None) -> List[int]:
    """原子批量导入：聚合全部条目错误，任一失败整批拒绝。"""
    if not items:
        raise ValidationError(
            "导入内容为空",
            fields=[{"field": "items", "code": "empty", "message": "items 不能为空"}],
        )
    if derive is not None:
        items = [derive(dict(item), None) for item in items]
    errors: List[Dict[str, str]] = []
    for index, item in enumerate(items):
        prefix = f"items[{index}]."
        errors.extend(validate_values(entity, item, prefix=prefix, raise_on_error=False))
        validate_batch_ref(db, item.get("batch_id"), prefix, errors)
    if errors:
        raise ValidationError("批量导入存在未通过校验的数据", fields=errors)

    model: Type[Any] = ENTITY_MODELS[entity]
    records = [model(**item) for item in items]
    db.add_all(records)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ConflictError(
            "批量导入被数据库约束拒绝",
            fields=[{"field": "items", "code": "constraint_violation",
                     "message": "存在违反数量约束的数据"}],
            code="constraint_violation",
        )
    affected_batches = {item["batch_id"] for item in items}
    for batch_id in affected_batches:
        bump_batch_version(db, batch_id)
    db.commit()
    return [r.id for r in records]


def update_record(db: Session, entity: str, record_id: int, data: Dict[str, Any],
                  derive=None) -> Any:
    model = ENTITY_MODELS[entity]
    record = db.query(model).filter(model.id == record_id).first()
    if record is None:
        raise NotFoundError("记录不存在")

    validate_values(entity, data)

    target_batch_id = data.get("batch_id", record.batch_id)
    ensure_batch_writable(db, target_batch_id)
    # 跨批次移动：原批次的聚合结果也会变，两侧快照都要失效。
    original_batch_id = record.batch_id

    # 覆盖式修改前释放这些字段上未签署批次的已生效更正。
    changed_fields = [f for f in FIELD_RULES.get(entity, {}) if f in data]
    release_field_corrections(db, entity, record_id, changed_fields)

    if derive is not None:
        data = derive(data, record)

    for key, value in data.items():
        setattr(record, key, value)

    _flush_or_convert(db)
    bump_batch_version(db, target_batch_id)
    if original_batch_id is not None and original_batch_id != target_batch_id:
        bump_batch_version(db, original_batch_id)
    db.commit()
    db.refresh(record)
    return record


def _flush_or_convert(db: Session) -> None:
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        # 新库 CHECK 兜底（或其它唯一约束）：同样落成稳定字段错误。
        raise ConflictError(
            "数据被数据库约束拒绝",
            fields=[{"field": "_root", "code": "constraint_violation",
                     "message": "存在违反数量约束或唯一性的数据"}],
            code="constraint_violation",
        )


def get_effective(db: Session, entity: str, record_id: int) -> Any:
    """详情：返回应用有效版本（更正/冲正）后的游离副本。"""
    model = ENTITY_MODELS[entity]
    record = db.query(model).filter(model.id == record_id).first()
    if record is None:
        raise NotFoundError("记录不存在")
    corrections = applied_corrections_for_records(db, [record], entity)
    return effective_copy(record, entity, corrections)


def list_effective(db: Session, entity: str, filters=None) -> List[Any]:
    """列表：统一按有效版本返回游离副本。"""
    model = ENTITY_MODELS[entity]
    query = db.query(model)
    for column, value in (filters or {}).items():
        if value is not None:
            query = query.filter(getattr(model, column) == value)
    records = query.order_by(model.id).all()
    corrections = applied_corrections_for_records(db, records, entity)
    return [effective_copy(r, entity, corrections) for r in records]
