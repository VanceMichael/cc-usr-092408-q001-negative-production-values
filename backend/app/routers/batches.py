from fastapi import APIRouter, Depends
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from typing import List

from ..database import get_db
from ..errors import ConflictError, NotFoundError, StateConflictError
from ..models import Batch, Pond
from ..schemas import (
    BatchCreate,
    BatchUpdate,
    BatchResponse,
    BatchSignRequest,
)
from ..services.guards import sign_batch

router = APIRouter(
    prefix="/api/batches",
    tags=["批次管理"]
)


@router.post("/", response_model=BatchResponse, status_code=201)
def create_batch(batch: BatchCreate, db: Session = Depends(get_db)):
    db_pond = db.query(Pond).filter(Pond.id == batch.pond_id).first()
    if not db_pond:
        raise NotFoundError("塘口不存在")

    new_batch = Batch(**batch.model_dump())
    db.add(new_batch)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ConflictError(
            "批次号已存在",
            fields=[{"field": "batch_number", "code": "duplicate",
                     "message": "批次号已存在"}],
            code="duplicate",
        )
    db.commit()
    db.refresh(new_batch)
    return new_batch


@router.get("/", response_model=List[BatchResponse])
def get_batches(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(Batch).order_by(Batch.id).offset(skip).limit(limit).all()


@router.get("/{batch_id}/", response_model=BatchResponse)
def get_batch(batch_id: int, db: Session = Depends(get_db)):
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise NotFoundError("批次不存在")
    return batch


@router.get("/by-number/{batch_number}/", response_model=BatchResponse)
def get_batch_by_number(batch_number: str, db: Session = Depends(get_db)):
    batch = db.query(Batch).filter(Batch.batch_number == batch_number).first()
    if not batch:
        raise NotFoundError("批次不存在")
    return batch


@router.put("/{batch_id}/", response_model=BatchResponse)
def update_batch(batch_id: int, batch: BatchUpdate, db: Session = Depends(get_db)):
    db_batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not db_batch:
        raise NotFoundError("批次不存在")
    if db_batch.signed_at is not None:
        raise StateConflictError(
            "批次已签署冻结，不能直接修改",
            fields=[{"field": "batch_id", "code": "batch_signed",
                     "message": f"批次 {db_batch.batch_number} 已签署"}],
        )

    update_data = batch.model_dump(exclude_unset=True)
    if "pond_id" in update_data:
        pond = db.query(Pond).filter(Pond.id == update_data["pond_id"]).first()
        if not pond:
            raise NotFoundError("塘口不存在")
    for key, value in update_data.items():
        setattr(db_batch, key, value)

    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ConflictError(
            "批次号已存在",
            fields=[{"field": "batch_number", "code": "duplicate",
                     "message": "批次号已存在"}],
            code="duplicate",
        )
    db.commit()
    db.refresh(db_batch)
    return db_batch


@router.post("/{batch_id}/sign/", response_model=BatchResponse)
def sign_batch_route(batch_id: int, payload: BatchSignRequest, db: Session = Depends(get_db)):
    """月末结算签署：批次冻结，此后事实记录只能经复核冲正。"""
    db_batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not db_batch:
        raise NotFoundError("批次不存在")
    if db_batch.signed_at is not None:
        raise StateConflictError(
            "批次已签署，不能重复签署",
            fields=[{"field": "batch_id", "code": "already_signed",
                     "message": f"批次 {db_batch.batch_number} 已签署"}],
        )
    return sign_batch(db, db_batch, payload.actual_harvest_date)


@router.delete("/{batch_id}/")
def delete_batch(batch_id: int, db: Session = Depends(get_db)):
    db_batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not db_batch:
        raise NotFoundError("批次不存在")
    if db_batch.signed_at is not None:
        raise StateConflictError(
            "批次已签署冻结，不能删除",
            fields=[{"field": "batch_id", "code": "batch_signed",
                     "message": f"批次 {db_batch.batch_number} 已签署"}],
        )
    db.delete(db_batch)
    db.commit()
    return {"message": "批次删除成功"}
