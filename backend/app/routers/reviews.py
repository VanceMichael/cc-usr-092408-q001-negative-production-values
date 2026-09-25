from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional

from ..database import get_db
from ..models import Correction, DataReview, ReversalFact
from ..review_workflow import (
    WorkflowError,
    approve_correction,
    propose_correction,
    register_reversal,
    reject_correction,
    scan_anomalies,
)
from ..schemas import (
    CorrectionAction,
    CorrectionCreate,
    CorrectionResponse,
    ReviewResponse,
    ReversalCreate,
    ReversalResponse,
    ScanRequest,
    ScanResult,
)

router = APIRouter(
    prefix="/api/reviews",
    tags=["异常数据复核"],
)

corrections_router = APIRouter(
    prefix="/api/corrections",
    tags=["异常数据复核"],
)

reversals_router = APIRouter(
    prefix="/api/reversals",
    tags=["异常数据复核"],
)


def _raise_workflow(exc: WorkflowError):
    raise HTTPException(status_code=409, detail={"code": exc.code, "message": exc.message})


@router.post("/scan/", response_model=ScanResult)
def scan_reviews(payload: Optional[ScanRequest] = None, db: Session = Depends(get_db)):
    source = payload.source if payload and payload.source else "manual_scan"
    reviews = scan_anomalies(db, source=source)
    return {"created_count": len(reviews), "reviews": reviews}


@router.get("/", response_model=List[ReviewResponse])
def list_reviews(
    status: Optional[str] = None,
    entity_type: Optional[str] = None,
    batch_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    query = db.query(DataReview)
    if status:
        query = query.filter(DataReview.status == status)
    if entity_type:
        query = query.filter(DataReview.entity_type == entity_type)
    if batch_id is not None:
        query = query.filter(DataReview.batch_id == batch_id)
    return query.order_by(DataReview.id).all()


@router.get("/{review_id}/", response_model=ReviewResponse)
def get_review(review_id: int, db: Session = Depends(get_db)):
    review = db.query(DataReview).filter(DataReview.id == review_id).first()
    if not review:
        raise HTTPException(status_code=404, detail="复核记录不存在")
    return review


@router.post("/{review_id}/corrections/", response_model=CorrectionResponse, status_code=201)
def create_correction(review_id: int, payload: CorrectionCreate, db: Session = Depends(get_db)):
    try:
        return propose_correction(
            db,
            review_id=review_id,
            payload=payload.payload,
            idempotency_key=payload.idempotency_key,
            proposed_by=payload.proposed_by,
            reason=payload.reason,
        )
    except WorkflowError as exc:
        _raise_workflow(exc)


@corrections_router.get("/", response_model=List[CorrectionResponse])
def list_corrections(review_id: Optional[int] = None, db: Session = Depends(get_db)):
    query = db.query(Correction)
    if review_id is not None:
        query = query.filter(Correction.review_id == review_id)
    return query.order_by(Correction.id).all()


@corrections_router.post("/{correction_id}/approve/", response_model=CorrectionResponse)
def approve_correction_endpoint(
    correction_id: int, payload: Optional[CorrectionAction] = None, db: Session = Depends(get_db)
):
    approved_by = payload.approved_by if payload else None
    try:
        return approve_correction(db, correction_id, approved_by=approved_by)
    except WorkflowError as exc:
        code_status = 404 if exc.code == "correction_not_found" else 409
        raise HTTPException(status_code=code_status, detail={"code": exc.code, "message": exc.message})


@corrections_router.post("/{correction_id}/reject/", response_model=CorrectionResponse)
def reject_correction_endpoint(
    correction_id: int, payload: Optional[CorrectionAction] = None, db: Session = Depends(get_db)
):
    approved_by = payload.approved_by if payload else None
    try:
        return reject_correction(db, correction_id, approved_by=approved_by)
    except WorkflowError as exc:
        code_status = 404 if exc.code == "correction_not_found" else 409
        raise HTTPException(status_code=code_status, detail={"code": exc.code, "message": exc.message})


@reversals_router.post("/", response_model=ReversalResponse, status_code=201)
def create_reversal(payload: ReversalCreate, db: Session = Depends(get_db)):
    try:
        return register_reversal(
            db,
            review_id=payload.review_id,
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            field_name=payload.field_name,
            idempotency_key=payload.idempotency_key,
            reason=payload.reason,
            created_by=payload.created_by,
        )
    except WorkflowError as exc:
        code_status = 404 if exc.code in ("review_not_found", "fact_not_found") else 409
        raise HTTPException(status_code=code_status, detail={"code": exc.code, "message": exc.message})


@reversals_router.get("/", response_model=List[ReversalResponse])
def list_reversals(batch_id: Optional[int] = None, db: Session = Depends(get_db)):
    query = db.query(ReversalFact)
    if batch_id is not None:
        query = query.filter(ReversalFact.batch_id == batch_id)
    return query.order_by(ReversalFact.id).all()
