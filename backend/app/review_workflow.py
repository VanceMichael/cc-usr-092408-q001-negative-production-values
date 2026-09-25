"""异常数据复核工作流。

规则总览：

* 旧库中的异常值**不删除**：启动/手动扫描为每个“实体行 + 异常字段”生成
  一条 :class:`DataReview`，记录来源、原始值、违反规则与影响（发现时批次
  是否已签署进入结算）；
* 未进入结算（批次未签署）的异常，走“更正”：先拟单、再批准，批准后以
  **新版本行**替代旧版本，旧行保留留痕；
* 已签署（已关闭）批次只能用**冲正事实**恢复：登记一笔与签署值符号相反
  的 :class:`ReversalFact`，把净额恢复到零；
* 更正、批次关闭与另一笔更正/冲正并发时，进程写锁 + 数据库唯一约束
  （幂等键、复核事实键）共同保证不重复冲正、不重复批准。
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from .database import write_lock
from .effective import ENTITY_MODELS, current_query
from .models import (
    Batch,
    Correction,
    DataReview,
    Pond,
    ReversalFact,
)
from .validation import FIELD_RULES, validate_fields


class WorkflowError(Exception):
    """工作流业务错误（HTTP 409）。"""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _batch_signed(db: Session, entity_type: str, row: Any) -> bool:
    """判断事实行所属批次是否已签署（关闭进入结算）。"""
    if entity_type == "Pond":
        # 塘口被多个批次共用；塘口异常本身不绑定单一结算批次。
        return False
    batch = db.query(Batch).filter(Batch.id == row.batch_id).first()
    return bool(batch and batch.status == "closed")


def _clone_version(row: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    """以旧版本行为模板构造新版本的列值（不含主键与版本指针）。"""
    skip = {"id", "version", "superseded_by_id", "void_reason", "created_at"}
    data = {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
        if column.name not in skip
    }
    data.update(payload)
    data["version"] = row.version + 1
    data["superseded_by_id"] = None
    data["void_reason"] = None
    return data


# ---------------------------------------------------------------------------
# 扫描：旧库异常值 -> 复核记录
# ---------------------------------------------------------------------------

def scan_anomalies(db: Session, source: str = "manual_scan") -> List[DataReview]:
    """扫描全部当前版本行，为违反数量约束的字段建立复核记录。

    幂等：同一 (实体类型, 行ID, 字段) 只保留一条 open 复核记录；服务重启
    后重复扫描不会产生重复记录，复核进度可继续。
    """
    found: List[DataReview] = []
    with write_lock:
        existing = {
            (r.entity_type, r.entity_id, r.field_name)
            for r in db.query(DataReview).all()
        }
        for entity_type, model in ENTITY_MODELS.items():
            rows = current_query(db, model).all()
            for row in rows:
                values = {
                    field: getattr(row, field, None)
                    for field in model.__table__.columns.keys()
                }
                errors = validate_fields(entity_type, values, partial=True)
                for err in errors:
                    key = (entity_type, row.id, err["field"])
                    if key in existing:
                        continue
                    raw = getattr(row, err["field"], None)
                    review = DataReview(
                        source=source,
                        entity_type=entity_type,
                        entity_id=row.id,
                        batch_id=getattr(row, "batch_id", None),
                        field_name=err["field"],
                        raw_value=str(raw),
                        rule=err["rule"],
                        message=err["message"],
                        entered_settlement=_batch_signed(db, entity_type, row),
                        status="open",
                    )
                    db.add(review)
                    existing.add(key)
                    found.append(review)
        db.commit()
        for review in found:
            db.refresh(review)
    return found


# ---------------------------------------------------------------------------
# 更正：仅未进入结算的异常可批准
# ---------------------------------------------------------------------------

def propose_correction(
    db: Session,
    review_id: int,
    payload: Dict[str, Any],
    idempotency_key: str,
    proposed_by: Optional[str] = None,
    reason: Optional[str] = None,
) -> Correction:
    review = db.query(DataReview).filter(DataReview.id == review_id).first()
    if not review:
        raise WorkflowError("review_not_found", "复核记录不存在")

    # 幂等键：同一请求重试返回原更正单
    existing = (
        db.query(Correction)
        .filter(Correction.idempotency_key == idempotency_key)
        .first()
    )
    if existing:
        return existing

    if review.status != "open":
        raise WorkflowError("review_not_open", f"复核记录状态为 {review.status}，不能再更正")
    if review.entered_settlement:
        raise WorkflowError(
            "already_settled",
            "异常在发现时已进入结算（批次已签署），只能冲正不能更正",
        )

    model = ENTITY_MODELS.get(review.entity_type)
    if model is None:
        raise WorkflowError("unknown_entity", f"未知实体类型 {review.entity_type}")
    allowed_columns = set(model.__table__.columns.keys())
    unknown = sorted(set(payload) - allowed_columns)
    if unknown:
        raise WorkflowError("invalid_correction", f"存在非法字段: {', '.join(unknown)}")
    errors = validate_fields(review.entity_type, payload, partial=True)
    if errors:
        raise WorkflowError("invalid_correction", json.dumps(errors, ensure_ascii=False))

    correction = Correction(
        review_id=review.id,
        entity_type=review.entity_type,
        old_entity_id=review.entity_id,
        idempotency_key=idempotency_key,
        status="proposed",
        payload_json=json.dumps(payload, ensure_ascii=False),
        proposed_by=proposed_by,
        reason=reason,
    )
    db.add(correction)
    try:
        db.commit()
    except Exception:
        db.rollback()
        # 并发下幂等键唯一约束冲突：返回已存在的更正单
        existing = (
            db.query(Correction)
            .filter(Correction.idempotency_key == idempotency_key)
            .first()
        )
        if existing:
            return existing
        raise
    db.refresh(correction)
    return correction


def approve_correction(db: Session, correction_id: int, approved_by: Optional[str] = None) -> Correction:
    with write_lock:
        correction = db.query(Correction).filter(Correction.id == correction_id).first()
        if not correction:
            raise WorkflowError("correction_not_found", "更正单不存在")
        if correction.status == "approved":
            # 重复批准是幂等的，不产生第二个新版本
            return correction

        review = db.query(DataReview).filter(DataReview.id == correction.review_id).first()
        if not review or review.status != "open":
            raise WorkflowError("review_not_open", "复核记录已处理，不能重复更正")

        model = ENTITY_MODELS[correction.entity_type]
        old_row = db.query(model).filter(model.id == correction.old_entity_id).first()
        if not old_row or old_row.superseded_by_id is not None:
            correction.status = "conflict"
            db.commit()
            raise WorkflowError("fact_superseded", "待更正事实已被其他版本替代")

        # 并发窗口：拟单后批次被关闭签署 -> 只能冲正
        if correction.entity_type != "Pond":
            batch = db.query(Batch).filter(Batch.id == old_row.batch_id).first()
            if batch and batch.status == "closed":
                review.entered_settlement = True
                correction.status = "conflict"
                db.commit()
                raise WorkflowError(
                    "batch_signed_concurrently",
                    "批次已在更正批准前关闭签署，该事实只能冲正",
                )

        payload = json.loads(correction.payload_json)
        if correction.entity_type == "Pond":
            # 塘口被批次外键引用：采用原地升版本，避免行替换导致批次悬挂
            if "name" in payload:
                clash = (
                    db.query(Pond)
                    .filter(Pond.name == payload["name"], Pond.id != old_row.id)
                    .first()
                )
                if clash:
                    correction.status = "conflict"
                    db.commit()
                    raise WorkflowError("pond_name_exists", "塘口名称已存在，更正冲突")
            for key, value in payload.items():
                setattr(old_row, key, value)
            old_row.version += 1
            new_entity_id = old_row.id
        else:
            # 若拟单要求改挂批次，目标批次必须存在且未签署
            if "batch_id" in payload and payload["batch_id"] != old_row.batch_id:
                target = db.query(Batch).filter(Batch.id == payload["batch_id"]).first()
                if target is None:
                    raise WorkflowError("batch_not_found", "目标批次不存在")
                if target.status == "closed":
                    raise WorkflowError("target_batch_signed", "目标批次已签署，不能并入")
            new_data = _clone_version(old_row, payload)
            new_row = model(**new_data)
            db.add(new_row)
            db.flush()  # 取 new_row.id

            old_row.superseded_by_id = new_row.id
            old_row.void_reason = "correction"
            new_entity_id = new_row.id

        correction.new_entity_id = new_entity_id
        correction.status = "approved"
        correction.approved_by = approved_by
        correction.applied_at = datetime.utcnow()

        review.status = "corrected"
        review.resolution_note = correction.reason
        review.reviewed_by = approved_by
        review.resolved_at = datetime.utcnow()

        db.commit()
        db.refresh(correction)
    return correction


def reject_correction(db: Session, correction_id: int, approved_by: Optional[str] = None) -> Correction:
    with write_lock:
        correction = db.query(Correction).filter(Correction.id == correction_id).first()
        if not correction:
            raise WorkflowError("correction_not_found", "更正单不存在")
        if correction.status != "proposed":
            raise WorkflowError("correction_not_pending", f"更正单状态为 {correction.status}")
        correction.status = "rejected"
        correction.approved_by = approved_by
        correction.applied_at = datetime.utcnow()

        review = db.query(DataReview).filter(DataReview.id == correction.review_id).first()
        if review and review.status == "open":
            review.status = "ignored"
            review.reviewed_by = approved_by
            review.resolved_at = datetime.utcnow()
            review.resolution_note = "更正被驳回"
        db.commit()
        db.refresh(correction)
    return correction


# ---------------------------------------------------------------------------
# 冲正：已签署批次只能用冲正事实恢复
# ---------------------------------------------------------------------------

def register_reversal(
    db: Session,
    *,
    review_id: Optional[int] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    field_name: Optional[str] = None,
    idempotency_key: str,
    reason: Optional[str] = None,
    created_by: Optional[str] = None,
) -> ReversalFact:
    with write_lock:
        # 同一幂等键重试：直接返回原冲正事实，绝不重复冲正
        existing = (
            db.query(ReversalFact)
            .filter(ReversalFact.idempotency_key == idempotency_key)
            .first()
        )
        if existing:
            return existing

        review: Optional[DataReview] = None
        if review_id is not None:
            review = db.query(DataReview).filter(DataReview.id == review_id).first()
            if not review:
                raise WorkflowError("review_not_found", "复核记录不存在")
            entity_type = review.entity_type
            entity_id = review.entity_id
            field_name = review.field_name

        if not entity_type or entity_id is None or not field_name:
            raise WorkflowError("missing_target", "缺少冲正目标(实体类型/行ID/字段)")

        model = ENTITY_MODELS.get(entity_type)
        if model is None:
            raise WorkflowError("unknown_entity", f"未知实体类型 {entity_type}")

        source_row = db.query(model).filter(model.id == entity_id).first()
        if not source_row:
            raise WorkflowError("fact_not_found", "被冲正的事实不存在")
        if not hasattr(source_row, field_name):
            raise WorkflowError("unknown_field", f"实体 {entity_type} 没有字段 {field_name}")
        if field_name not in FIELD_RULES.get(entity_type, {}):
            raise WorkflowError("field_not_reversible", f"字段 {field_name} 不是数量字段，不能冲正")

        batch_id = getattr(source_row, "batch_id", None)
        batch = db.query(Batch).filter(Batch.id == batch_id).first() if batch_id is not None else None
        if batch is None or batch.status != "closed":
            raise WorkflowError(
                "batch_not_signed",
                "只有已签署(关闭)批次的事实才允许冲正；未签署请走更正",
            )

        # 不同幂等键对同一签署事实的重复冲正必须拒绝
        duplicate = (
            db.query(ReversalFact)
            .filter(
                ReversalFact.entity_type == entity_type,
                ReversalFact.source_entity_id == entity_id,
                ReversalFact.field_name == field_name,
            )
            .first()
        )
        if duplicate:
            raise WorkflowError(
                "reversal_exists",
                f"该签署事实的 {field_name} 已存在冲正(幂等键 {duplicate.idempotency_key})",
            )

        signed_value = float(getattr(source_row, field_name) or 0)
        reverse_value = -signed_value  # 恢复到零
        net_value = signed_value + reverse_value

        fact = ReversalFact(
            review_id=review.id if review else None,
            batch_id=batch_id,
            entity_type=entity_type,
            source_entity_id=entity_id,
            field_name=field_name,
            signed_value=signed_value,
            reverse_value=reverse_value,
            net_value=net_value,
            reason=reason,
            idempotency_key=idempotency_key,
            created_by=created_by,
        )
        db.add(fact)

        if review and review.status == "open":
            review.status = "reversed"
            review.resolution_note = reason
            review.reviewed_by = created_by
            review.resolved_at = datetime.utcnow()

        try:
            db.commit()
        except Exception:
            db.rollback()
            existing = (
                db.query(ReversalFact)
                .filter(ReversalFact.idempotency_key == idempotency_key)
                .first()
            )
            if existing:
                return existing
            # 数据库级 (实体,源行,字段) 唯一约束兜底：并发下只能有一笔冲正
            dup = (
                db.query(ReversalFact)
                .filter(
                    ReversalFact.entity_type == entity_type,
                    ReversalFact.source_entity_id == entity_id,
                    ReversalFact.field_name == field_name,
                )
                .first()
            )
            if dup:
                raise WorkflowError(
                    "reversal_exists",
                    f"该签署事实的 {field_name} 已存在冲正(幂等键 {dup.idempotency_key})",
                )
            raise
        db.refresh(fact)
    return fact


# ---------------------------------------------------------------------------
# 批次关闭（签署）
# ---------------------------------------------------------------------------

def close_batch(db: Session, batch_id: int, closed_by: Optional[str] = None) -> Batch:
    with write_lock:
        batch = db.query(Batch).filter(Batch.id == batch_id).first()
        if not batch:
            raise WorkflowError("batch_not_found", "批次不存在")
        if batch.status == "closed":
            raise WorkflowError("batch_already_closed", "批次已关闭签署，不能重复关闭")
        batch.status = "closed"
        batch.signed_at = datetime.utcnow()
        batch.closed_by = closed_by
        # 关闭时尚未处理的复核记录，其影响确定为“已进入结算”，只能冲正
        db.query(DataReview).filter(
            DataReview.batch_id == batch_id,
            DataReview.status == "open",
        ).update({DataReview.entered_settlement: True}, synchronize_session=False)
        db.commit()
        db.refresh(batch)
    return batch
