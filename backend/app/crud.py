"""业务路由共享的创建/修改/批量导入逻辑。

所有实体走同一套数量校验与签署冻结规则：

* 创建、修改、批量导入调用 :func:`validate_or_raise` /
  :func:`validate_batch`，错误经统一信封返回；
* 已签署（关闭）批次冻结普通增改删，只接受冲正事实；
* 批量导入先整批校验 + 批次存在性检查，任何一行失败都不写入。
"""

from typing import Any, Dict, List, Type

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .models import Batch
from .validation import (
    BatchValidationError,
    validate_batch,
    validate_or_raise,
)


def get_batch_or_404(db: Session, batch_id: int) -> Batch:
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    return batch


def ensure_batch_open(db: Session, batch_id: int) -> Batch:
    """未签署批次才允许普通写入；已关闭批次只能冲正。"""
    batch = get_batch_or_404(db, batch_id)
    if batch.status == "closed":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "batch_signed",
                "message": "批次已关闭签署，记录被冻结，只能登记冲正事实",
            },
        )
    return batch


def apply_create(db: Session, model: Type, model_name: str, data: Dict[str, Any]) -> Any:
    """创建单行：数量校验 -> 冻结检查 -> 落库。返回新行。"""
    validate_or_raise(model_name, data)
    if "batch_id" in model.__table__.columns.keys():
        ensure_batch_open(db, data["batch_id"])
    row = model(**data)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def apply_update(
    db: Session,
    model: Type,
    model_name: str,
    row: Any,
    update_data: Dict[str, Any],
) -> Any:
    """修改行：对合并后的最终值做完整校验（partial 语义只用于未提供字段）。

    修改批次归属时，目标批次同样必须未签署。
    """
    if not update_data:
        return row
    # 部分校验：仅校验本次提交的字段
    validate_or_raise(model_name, update_data, partial=True)

    target_batch_id = update_data.get("batch_id", row.batch_id if hasattr(row, "batch_id") else None)
    if target_batch_id is not None and "batch_id" in model.__table__.columns.keys():
        ensure_batch_open(db, target_batch_id)
    elif hasattr(row, "batch_id") and row.batch_id is not None:
        ensure_batch_open(db, row.batch_id)

    for key, value in update_data.items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row


def apply_batch_import(
    db: Session,
    model: Type,
    model_name: str,
    rows_data: List[Dict[str, Any]],
) -> List[Any]:
    """批量导入：任一行业务校验或批次检查失败则整批不落库。"""
    if not rows_data:
        raise BatchValidationError([])

    row_errors = validate_batch(model_name, rows_data)
    if row_errors:
        raise BatchValidationError(row_errors)

    if "batch_id" in model.__table__.columns.keys():
        batch_ids = {data.get("batch_id") for data in rows_data}
        for batch_id in batch_ids:
            ensure_batch_open(db, batch_id)

    new_rows: List[Any] = []
    try:
        for data in rows_data:
            row = model(**data)
            db.add(row)
            new_rows.append(row)
        db.commit()
    except Exception:
        db.rollback()
        raise
    for row in new_rows:
        db.refresh(row)
    return new_rows
