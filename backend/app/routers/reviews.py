"""异常数据复核接口：查询、审批、更正（未结算）/冲正（已签署）、手动上报、重扫。"""

from typing import List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..errors import ConflictError, NotFoundError
from ..models import DataReview
from ..schemas import (
    ReviewResponse,
    ReviewDecisionRequest,
    ResolutionRequest,
    CorrectionResponse,
    ReviewSummary,
    RescanResponse,
    ManualReviewRequest,
)
from ..services.entities import ENTITY_MODELS, batch_id_of
from ..services.reviews import (
    apply_resolution,
    decide_review,
    list_reviews,
    register_review,
    scan_legacy_anomalies,
)
from ..validation import FIELD_RULES

router = APIRouter(
    prefix="/api/data-reviews",
    tags=["数据复核"]
)


@router.get("/", response_model=List[ReviewResponse])
def get_reviews(
    status: Optional[str] = None,
    entity: Optional[str] = None,
    source: Optional[str] = None,
    batch_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    return list_reviews(db, status=status, entity=entity, source=source, batch_id=batch_id)


@router.get("/summary/", response_model=ReviewSummary)
def get_review_summary(db: Session = Depends(get_db)):
    reviews = db.query(DataReview).all()
    by_entity: dict = {}
    by_source: dict = {}
    counts = {"open": 0, "approved": 0, "rejected": 0, "corrected": 0, "reversed": 0,
              "entered_settlement": 0}
    for review in reviews:
        counts[review.status] = counts.get(review.status, 0) + 1
        by_entity[review.entity] = by_entity.get(review.entity, 0) + 1
        by_source[review.source] = by_source.get(review.source, 0) + 1
        if review.entered_settlement:
            counts["entered_settlement"] += 1
    return ReviewSummary(
        total=len(reviews),
        open=counts["open"],
        approved=counts["approved"],
        rejected=counts["rejected"],
        corrected=counts["corrected"],
        reversed=counts["reversed"],
        entered_settlement=counts["entered_settlement"],
        by_entity=by_entity,
        by_source=by_source,
    )


@router.get("/{review_id}/", response_model=ReviewResponse)
def get_review(review_id: int, db: Session = Depends(get_db)):
    review = db.query(DataReview).filter(DataReview.id == review_id).first()
    if not review:
        from ..errors import NotFoundError
        raise NotFoundError("复核记录不存在")
    return review


@router.post("/{review_id}/decision/", response_model=ReviewResponse)
def review_decision(
    review_id: int,
    payload: ReviewDecisionRequest,
    db: Session = Depends(get_db),
):
    """批准 / 驳回一条复核。"""
    return decide_review(
        db,
        review_id,
        approve=payload.approve,
        note=payload.note,
        reviewed_by=payload.reviewed_by,
    )


@router.post("/{review_id}/resolve/", response_model=CorrectionResponse)
def review_resolve(
    review_id: int,
    payload: ResolutionRequest,
    db: Session = Depends(get_db),
):
    """对已批准复核落账：未签署批次更正为 requested_value；已签署批次强制冲正归零。

    同一 idempotency_key 重放、或并发的另一笔更正/批次关闭，返回 409
    duplicate_reversal，不会产生重复冲正。
    """
    return apply_resolution(
        db,
        review_id,
        requested_value=payload.requested_value,
        idempotency_key=payload.idempotency_key,
        reason=payload.reason,
        approved_by=payload.approved_by,
    )


@router.post("/rescan/", response_model=RescanResponse)
def rescan(db: Session = Depends(get_db)):
    """手动重新扫描全库异常（重启时也会自动执行一次，幂等）。"""
    counts = scan_legacy_anomalies(db)
    return RescanResponse(
        scanned_records=counts["scanned_records"],
        new_reviews=counts["new_reviews"],
    )


@router.post("/", response_model=ReviewResponse, status_code=201)
def report_review(payload: ManualReviewRequest, db: Session = Depends(get_db)):
    """人工上报异常（来源 manual）；同一实体/字段/规则去重。

    是否已进入结算默认按所属批次签署状态自动判定，也可显式覆盖。
    """
    from ..services.guards import get_batch

    if payload.entity not in ENTITY_MODELS:
        from ..errors import ValidationError
        raise ValidationError(
            "未知的实体类型",
            fields=[{"field": "entity", "code": "invalid", "message": "entity 不合法"}],
        )
    if payload.field not in FIELD_RULES.get(payload.entity, {}):
        from ..errors import ValidationError
        raise ValidationError(
            "该字段不受数量约束或不存在",
            fields=[{"field": "field", "code": "invalid", "message": "field 不受约束"}],
        )
    record = (
        db.query(ENTITY_MODELS[payload.entity])
        .filter(ENTITY_MODELS[payload.entity].id == payload.record_id)
        .first()
    )
    if record is None:
        raise NotFoundError("对应业务记录不存在")

    if payload.entered_settlement is None:
        batch_id = batch_id_of(payload.entity, record)
        batch = get_batch(db, batch_id) if batch_id is not None else None
        entered = bool(batch and batch.signed_at is not None)
    else:
        entered = payload.entered_settlement

    review = register_review(
        db,
        entity=payload.entity,
        record_id=payload.record_id,
        field=payload.field,
        rule_code=payload.rule_code,
        bad_value=float(getattr(record, payload.field)),
        source="manual",
        entered_settlement=entered,
        flush=True,
    )
    if review is None:
        raise ConflictError(
            "该异常已存在复核记录",
            fields=[{"field": "_root", "code": "duplicate",
                     "message": "同实体/字段/规则的复核已存在"}],
            code="duplicate",
        )
    db.commit()
    db.refresh(review)
    return review
