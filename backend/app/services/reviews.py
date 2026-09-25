"""异常数据复核、批准更正与签署冲正的状态机。

业务规则：
1. 旧库异常值不删除：启动扫描逐表逐字段登记 DataReview（按 dedup_key 幂等），
   记录来源(legacy_scan)与影响指标(impact)，并依据批次签署状态判断是否已进入结算。
2. 未进入结算（批次未签署）的复核：批准后可用 *更正* 把值改为正确值。
3. 已签署批次只能 *冲正*：保留原事实，追加一笔反向流水，使该字段有效贡献归 0。
4. 并发防护：
   - 复核与流水一一对应（UNIQUE review_id / idempotency_key）；
   - 落账用 ``UPDATE ... WHERE status='pending'`` 条件认领，配合 SQLite 写锁，
     更正与批次关闭、或两笔更正并发时，只有一个能落账，重复冲正返回 409。
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..errors import (
    ConflictError,
    DuplicateReversalError,
    NotFoundError,
    StateConflictError,
    ValidationError,
)
from ..models import (
    Batch,
    DataReview,
    RecordCorrection,
)
from ..validation import (
    ENTITY_LABELS,
    FIELD_IMPACT,
    FIELD_RULES,
    check_field,
)
from .entities import ENTITY_MODELS, batch_id_of
from .guards import bump_pond_batches_version

REVIEW_STATUSES = {"open", "approved", "rejected", "corrected", "reversed"}
CORRECTION_STATUSES = {"pending", "applied", "cancelled"}


def make_dedup_key(entity: str, record_id: int, field: str, rule_code: str) -> str:
    return f"{entity}:{record_id}:{field}:{rule_code}"


def _impact_for(entity: str, field: str) -> str:
    return ",".join(FIELD_IMPACT.get(entity, {}).get(field, []))


def _batch_is_signed(db: Session, entity: str, obj: Any) -> bool:
    batch_id = batch_id_of(entity, obj)
    if batch_id is None:
        return False
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    return bool(batch and batch.signed_at is not None)


def register_review(
    db: Session,
    entity: str,
    record_id: int,
    field: str,
    rule_code: str,
    bad_value: Optional[float],
    source: str,
    entered_settlement: bool,
    flush: bool = True,
) -> Optional[DataReview]:
    """登记一条复核记录；同一去重键已存在则返回 None（幂等）。"""
    dedup_key = make_dedup_key(entity, record_id, field, rule_code)
    existing = db.query(DataReview).filter(DataReview.dedup_key == dedup_key).first()
    if existing is not None:
        return None
    obj = db.query(ENTITY_MODELS[entity]).filter(
        ENTITY_MODELS[entity].id == record_id
    ).first()
    batch_id = batch_id_of(entity, obj) if obj else None
    review = DataReview(
        entity=entity,
        record_id=record_id,
        batch_id=batch_id,
        field=field,
        bad_value=bad_value,
        rule_code=rule_code,
        source=source,
        impact=_impact_for(entity, field),
        entered_settlement=entered_settlement,
        status="open",
        dedup_key=dedup_key,
    )
    db.add(review)
    try:
        if flush:
            db.flush()
    except IntegrityError:
        # 并发登记同一异常（极少见，扫描本身在启动期串行执行）：
        # 让出整个事务后由调用方按已存在处理。
        db.rollback()
        return None
    return review


def scan_legacy_anomalies(db: Session) -> Dict[str, int]:
    """扫描旧库全部受约束字段，为历史异常值建立复核记录。

    幂等：重复执行（服务重启）不会产生重复复核，扫描进度天然持久化在库中。
    """
    counts = {"scanned_records": 0, "new_reviews": 0}
    for entity, model in ENTITY_MODELS.items():
        rules = FIELD_RULES.get(entity, {})
        if not rules:
            continue
        rows = db.query(model).all()
        counts["scanned_records"] += len(rows)
        for obj in rows:
            signed = _batch_is_signed(db, entity, obj)
            for field in rules:
                value = getattr(obj, field)
                if value is None:
                    continue
                rule_code = check_field(entity, field, value, allow_void=False)
                if rule_code is None:
                    continue
                review = register_review(
                    db,
                    entity=entity,
                    record_id=obj.id,
                    field=field,
                    rule_code=rule_code,
                    bad_value=float(value),
                    source="legacy_scan",
                    entered_settlement=signed,
                    flush=True,
                )
                if review is not None:
                    counts["new_reviews"] += 1
    db.commit()
    return counts


def _get_review(db: Session, review_id: int) -> DataReview:
    review = db.query(DataReview).filter(DataReview.id == review_id).first()
    if not review:
        raise NotFoundError("复核记录不存在")
    return review


def _get_record(db: Session, entity: str, record_id: int) -> Any:
    obj = db.query(ENTITY_MODELS[entity]).filter(
        ENTITY_MODELS[entity].id == record_id
    ).first()
    if not obj:
        raise NotFoundError(f"{ENTITY_LABELS.get(entity, entity)}记录不存在")
    return obj


def decide_review(
    db: Session,
    review_id: int,
    approve: bool,
    note: Optional[str] = None,
    reviewed_by: Optional[str] = None,
) -> DataReview:
    """批准 / 驳回。只有 open 复核可决策；已处置的返回冲突。"""
    review = _get_review(db, review_id)
    if review.status != "open":
        raise StateConflictError(
            f"复核记录当前状态为 {review.status}，不能重复审批",
            fields=[{"field": "status", "code": "invalid_state",
                     "message": f"当前状态 {review.status}"}],
        )
    review.status = "approved" if approve else "rejected"
    review.decision_note = note
    review.reviewed_at = datetime.utcnow()
    review.reviewed_by = reviewed_by
    db.commit()
    db.refresh(review)
    return review


def _claim_correction(db: Session, correction_id: int) -> bool:
    """条件 UPDATE 认领流水：pending → applied。返回是否抢到。"""
    result = db.execute(
        update(RecordCorrection)
        .where(
            RecordCorrection.id == correction_id,
            RecordCorrection.status == "pending",
        )
        .values(status="applied", applied_at=datetime.utcnow())
    )
    return (result.rowcount or 0) == 1


def _claim_batch_freeze(db: Session, batch_id: int, expected_version: int) -> bool:
    """条件推进批次冻结版本，作为“更正 vs 关闭/另一更正”并发的最后闸门。"""
    result = db.execute(
        update(Batch)
        .where(Batch.id == batch_id, Batch.freeze_version == expected_version)
        .values(freeze_version=expected_version + 1)
    )
    return (result.rowcount or 0) == 1


# 冲正主字段时必须联动归零的派生/同事实字段（已签署批次保持勾稽一致）。
# 冲正语义是“作废这笔事实的全部数量贡献”，因此整笔销售的量、价、额一并归零。
REVERSAL_CASCADE = {
    "harvest": {
        "weight": ["total_amount", "unit_price"],
        "unit_price": ["total_amount", "weight"],
        "total_amount": ["weight", "unit_price"],
    },
    "stocking": {
        "quantity": ["total_weight"],
    },
}


def _coerce_target(entity: str, field: str, value: float) -> Any:
    spec = FIELD_RULES.get(entity, {}).get(field)
    expected_type = spec[2] if spec else None
    if expected_type is int and float(value).is_integer():
        return int(value)
    return float(value)


def apply_resolution(
    db: Session,
    review_id: int,
    requested_value: Optional[float] = None,
    idempotency_key: Optional[str] = None,
    reason: Optional[str] = None,
    approved_by: Optional[str] = None,
) -> RecordCorrection:
    """对已批准复核落账：未签署 → 更正；已签署 → 冲正归零。

    冲正按 :data:`REVERSAL_CASCADE` 联动派生字段（销售重量↔总金额），保证
    亩产、收入等派生口径自洽。幂等：相同 idempotency_key 重放返回 409
    duplicate_reversal，绝不重复冲正。
    """
    review = _get_review(db, review_id)
    if review.status in ("corrected", "reversed"):
        raise DuplicateReversalError(
            "该复核已完成更正/冲正，禁止重复冲正",
            fields=[{"field": "review_id", "code": "already_resolved",
                     "message": f"复核 {review_id} 已处置"}],
        )
    if review.status != "approved":
        raise StateConflictError(
            "复核尚未批准，不能落账",
            fields=[{"field": "status", "code": "not_approved",
                     "message": f"当前状态 {review.status}"}],
        )

    obj = _get_record(db, review.entity, review.record_id)
    batch_id = batch_id_of(review.entity, obj)
    signed = _batch_is_signed(db, review.entity, obj)

    # 刷新“是否已进入结算”判定（批准时未签署、落账时可能刚被关闭）。
    review.entered_settlement = bool(review.entered_settlement or signed)

    # (field, target_value) —— 冲正时可含联动派生字段。
    targets: List[tuple] = []
    if signed:
        kind = "reversal"
        has_cascade = review.field in REVERSAL_CASCADE.get(review.entity, {})
        if not has_cascade and check_field(
            review.entity, review.field, 0.0, allow_void=True
        ) is not None:
            # 永远 > 0 且无“整笔事实”可作废的字段（塘口面积/水深、单重、成本单价）
            # 不能冲正归零；需通过解锁批次后更正的专门流程。
            raise ConflictError(
                "该字段不允许冲正归零，请按流程解锁批次后走更正",
                fields=[{"field": review.field, "code": "void_not_allowed",
                         "message": "必须大于零的字段不能单独冲正"}],
                code="void_not_allowed",
            )
        targets.append((review.field, 0.0))
        for cascade_field in REVERSAL_CASCADE.get(review.entity, {}).get(review.field, []):
            # 该派生/同事实字段若已被另一笔复核处置，则跳过、不重复冲正。
            already = (
                db.query(RecordCorrection)
                .filter(
                    RecordCorrection.entity == review.entity,
                    RecordCorrection.record_id == review.record_id,
                    RecordCorrection.field == cascade_field,
                    RecordCorrection.status == "applied",
                )
                .first()
            )
            if already is None:
                targets.append((cascade_field, 0.0))
    else:
        kind = "correction"
        # 塘口是主数据而非批次事实：即使名下批次已签署，仍允许经批准的更正，
        # 但全程留痕（更正流水）并推进名下批次版本、重算亩产；普通直接修改仍被拦截。
        if requested_value is None:
            raise ValidationError(
                "更正必须提供正确的目标值",
                fields=[{"field": "requested_value", "code": "required",
                         "message": "未提供更正目标值"}],
            )
        code = check_field(review.entity, review.field, requested_value, allow_void=False)
        if code is not None:
            raise ValidationError(
                "更正目标值仍不满足数量约束",
                fields=[{"field": "requested_value", "code": code,
                         "message": "目标值非法或为负/零"}],
            )
        targets.append((review.field, float(requested_value)))

    idem = idempotency_key or f"review-{review.id}"
    batch = (
        db.query(Batch).filter(Batch.id == batch_id).first()
        if batch_id is not None
        else None
    )
    expected_version = batch.freeze_version if batch else 0

    # 主字段若已有生效冲正/更正，拒绝重复落账（级联字段已在上方排除）。
    existing_applied = (
        db.query(RecordCorrection)
        .filter(
            RecordCorrection.entity == review.entity,
            RecordCorrection.record_id == review.record_id,
            RecordCorrection.field == review.field,
            RecordCorrection.status == "applied",
        )
        .first()
    )
    if existing_applied is not None:
        raise DuplicateReversalError(
            f"字段 {existing_applied.field} 已存在生效的更正/冲正，禁止重复冲正",
            fields=[{"field": existing_applied.field, "code": "already_resolved",
                     "message": "该字段已处置"}],
        )

    corrections: List[RecordCorrection] = []
    for index, (field, target_value) in enumerate(targets):
        before_value = float(getattr(obj, field) or 0.0)
        corrections.append(
            RecordCorrection(
                review_id=review.id,
                entity=review.entity,
                record_id=review.record_id,
                batch_id=batch_id,
                field=field,
                before_value=before_value,
                after_value=target_value,
                kind=kind,
                status="pending",
                batch_signed=signed,
                # 主字段保留原始幂等键；联动行加字段后缀。
                idempotency_key=idem if index == 0 else f"{idem}#{field}",
                reason=reason,
                approved_by=approved_by,
                approved_at=datetime.utcnow(),
                freeze_version=expected_version,
            )
        )
    db.add_all(corrections)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise DuplicateReversalError(
            "更正/冲正与另一笔并发操作冲突，请勿重复冲正",
            fields=[{"field": "review_id", "code": "concurrent_resolution",
                     "message": "已有进行中或已完成的处置流水"}],
        )

    # 条件认领 + 版本闸门：并发关闭/另一更正只有一个能成功。
    claimed_all = all(_claim_correction(db, corr.id) for corr in corrections)
    version_ok = True
    if batch_id is not None:
        version_ok = _claim_batch_freeze(db, batch_id, expected_version)

    if not claimed_all or not version_ok:
        db.rollback()
        raise DuplicateReversalError(
            "并发冲突：批次刚被关闭或存在另一笔更正，本次冲正未落账",
            fields=[{"field": "batch_id", "code": "concurrent_modification",
                     "message": "数据版本已变化，请刷新后重试"}],
            code="duplicate_reversal",
        )

    primary = corrections[0]
    cascade_fields = [f for f, _v in targets[1:]]
    cascade_corr_by_field = {corr.field: corr for corr in corrections[1:]}
    if kind == "correction":
        # 未签署：直接把事实值更正（复核记录保留原值）。
        setattr(obj, review.field, _coerce_target(review.entity, review.field, primary.after_value))
        review.status = "corrected"
    else:
        # 已签署：底表事实不动，有效值由流水归零呈现（含联动字段）。
        review.status = "reversed"
        # 整笔事实被冲正：联动字段自身悬而未决（open/approved）的复核一并结案，
        # 避免同一笔销售的量/价/额复核永远挂起；已有结论（驳回/已更正）的不动。
        if cascade_fields:
            siblings = (
                db.query(DataReview)
                .filter(
                    DataReview.entity == review.entity,
                    DataReview.record_id == review.record_id,
                    DataReview.field.in_(cascade_fields),
                    DataReview.status.in_(["open", "approved"]),
                )
                .all()
            )
            for sibling in siblings:
                sibling.status = "reversed"
                sibling.entered_settlement = True
                corr = cascade_corr_by_field.get(sibling.field)
                if corr is not None:
                    sibling.correction_id = corr.id
    review.correction_id = primary.id
    db.commit()
    if review.entity == "pond":
        # 塘口面积/水深更正是事实改写，名下批次的周期口径随之变化。
        bump_pond_batches_version(db, review.record_id)
        db.commit()
    db.refresh(primary)
    return primary


def list_reviews(
    db: Session,
    status: Optional[str] = None,
    entity: Optional[str] = None,
    source: Optional[str] = None,
    batch_id: Optional[int] = None,
) -> List[DataReview]:
    query = db.query(DataReview)
    if status:
        query = query.filter(DataReview.status == status)
    if entity:
        query = query.filter(DataReview.entity == entity)
    if source:
        query = query.filter(DataReview.source == source)
    if batch_id is not None:
        query = query.filter(DataReview.batch_id == batch_id)
    return query.order_by(DataReview.id).all()
