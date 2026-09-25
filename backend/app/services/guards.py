"""批次冻结写保护、签署与更正释放。"""

from datetime import date, datetime
from typing import Iterable, Optional

from sqlalchemy import update
from sqlalchemy.orm import Session

from ..errors import StateConflictError
from ..models import Batch, RecordCorrection


def get_batch(db: Session, batch_id: int) -> Optional[Batch]:
    return db.query(Batch).filter(Batch.id == batch_id).first()


def sign_batch(
    db: Session,
    batch: Batch,
    actual_harvest_date: Optional[date] = None,
) -> Batch:
    """签署（月末结算关闭）批次。

    条件 UPDATE 推进 freeze_version：若与一笔并发的更正落账竞争，版本比对会让
    其中一方失败，调用方刷新重试，保证“更正 vs 关闭”不会双双成功。
    """
    expected_version = batch.freeze_version
    if actual_harvest_date is not None:
        batch.actual_harvest_date = actual_harvest_date
    batch.status = "closed"
    result = db.execute(
        update(Batch)
        .where(Batch.id == batch.id, Batch.freeze_version == expected_version)
        .values(signed_at=datetime.utcnow(), status="closed",
                actual_harvest_date=batch.actual_harvest_date,
                freeze_version=expected_version + 1)
    )
    if (result.rowcount or 0) != 1:
        db.rollback()
        raise StateConflictError(
            "批次在签署过程中被并发更正，请刷新后重试",
            fields=[{"field": "freeze_version", "code": "concurrent_modification",
                     "message": "数据版本已变化"}],
        )
    db.commit()
    db.refresh(batch)
    return batch


def ensure_batch_writable(db: Session, batch_id: Optional[int]) -> Batch:
    """返回批次；若批次已签署（结算冻结）则拒绝直接写入。"""
    if batch_id is None:
        raise StateConflictError("缺少批次归属，无法写入记录")
    batch = get_batch(db, batch_id)
    if batch is None:
        from ..errors import NotFoundError

        raise NotFoundError("批次不存在")
    if batch.signed_at is not None:
        raise StateConflictError(
            "批次已签署冻结，事实记录不能直接修改，请通过复核冲正处理",
            fields=[{"field": "batch_id", "code": "batch_signed",
                     "message": f"批次 {batch.batch_number} 已签署"}],
        )
    return batch


def release_field_corrections(
    db: Session,
    entity: str,
    record_id: int,
    fields: Iterable[str],
) -> int:
    """正常修改覆盖某字段时，解除该字段上未签署批次的已生效更正。

    底表在更正时就已写成更正值，因此新修改直接覆盖底表即可；旧更正流水标记
    cancelled 保留痕迹（冲正流水属于已签署批次，修改路径已被冻结拦截，不会动）。
    """
    field_list = list(fields)
    if not field_list:
        return 0
    result = db.execute(
        update(RecordCorrection)
        .where(
            RecordCorrection.entity == entity,
            RecordCorrection.record_id == record_id,
            RecordCorrection.field.in_(field_list),
            RecordCorrection.status == "applied",
            RecordCorrection.batch_signed.is_(False),
        )
        .values(status="cancelled")
    )
    return result.rowcount or 0


def bump_batch_version(db: Session, batch_id: Optional[int]) -> None:
    """事实数据发生直接写入后推进版本，使旧快照失效、触发下次重算。"""
    if batch_id is None:
        return
    db.execute(
        update(Batch)
        .where(Batch.id == batch_id)
        .values(freeze_version=Batch.freeze_version + 1)
    )


def bump_pond_batches_version(db: Session, pond_id: int) -> None:
    """塘口事实（面积/水深）变化会影响名下批次的亩产，统一推进其版本。"""
    db.execute(
        update(Batch)
        .where(Batch.pond_id == pond_id)
        .values(freeze_version=Batch.freeze_version + 1)
    )


def signed_batch_on_pond(db: Session, pond_id: int) -> Optional[Batch]:
    """塘口名下是否存在已签署批次（已结算口径冻结，塘口数量事实不得再改写）。"""
    return (
        db.query(Batch)
        .filter(Batch.pond_id == pond_id, Batch.signed_at.isnot(None))
        .first()
    )