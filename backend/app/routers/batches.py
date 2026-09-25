from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from ..database import get_db
from ..models import Batch, Pond
from ..schemas import BatchCreate, BatchUpdate, BatchResponse, BatchCloseRequest
from ..review_workflow import WorkflowError, close_batch

router = APIRouter(
    prefix="/api/batches",
    tags=["批次管理"]
)

@router.post("/", response_model=BatchResponse)
def create_batch(batch: BatchCreate, db: Session = Depends(get_db)):
    db_pond = db.query(Pond).filter(Pond.id == batch.pond_id).first()
    if not db_pond:
        raise HTTPException(status_code=404, detail="塘口不存在")

    db_batch = db.query(Batch).filter(Batch.batch_number == batch.batch_number).first()
    if db_batch:
        raise HTTPException(status_code=400, detail="批次号已存在")

    new_batch = Batch(**batch.dict())
    db.add(new_batch)
    db.commit()
    db.refresh(new_batch)
    return new_batch

@router.get("/", response_model=List[BatchResponse])
def get_batches(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    batches = db.query(Batch).offset(skip).limit(limit).all()
    return batches

@router.get("/{batch_id}/", response_model=BatchResponse)
def get_batch(batch_id: int, db: Session = Depends(get_db)):
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    return batch

@router.get("/by-number/{batch_number}/", response_model=BatchResponse)
def get_batch_by_number(batch_number: str, db: Session = Depends(get_db)):
    batch = db.query(Batch).filter(Batch.batch_number == batch_number).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    return batch

@router.put("/{batch_id}/", response_model=BatchResponse)
def update_batch(batch_id: int, batch: BatchUpdate, db: Session = Depends(get_db)):
    db_batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not db_batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    if db_batch.status == "closed" and batch.status != "closed":
        raise HTTPException(
            status_code=409,
            detail={"code": "batch_signed", "message": "批次已关闭签署，不能修改"},
        )

    update_data = batch.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_batch, key, value)

    db.commit()
    db.refresh(db_batch)
    return db_batch

@router.post("/{batch_id}/close/", response_model=BatchResponse)
def close_signed_batch(batch_id: int, payload: BatchCloseRequest = None, db: Session = Depends(get_db)):
    """关闭即签署：签署后批次事实冻结，只能冲正。"""
    try:
        return close_batch(db, batch_id, closed_by=payload.closed_by if payload else None)
    except WorkflowError as exc:
        raise HTTPException(
            status_code=404 if exc.code == "batch_not_found" else 409,
            detail={"code": exc.code, "message": exc.message},
        )

@router.delete("/{batch_id}/")
def delete_batch(batch_id: int, db: Session = Depends(get_db)):
    db_batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not db_batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    if db_batch.status == "closed":
        raise HTTPException(
            status_code=409,
            detail={"code": "batch_signed", "message": "批次已关闭签署，不能删除，只能冲正"},
        )

    db.delete(db_batch)
    db.commit()
    return {"message": "批次删除成功"}
