from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from ..database import get_db
from ..models import CostRecord, Batch
from ..schemas import CostRecordCreate, CostRecordUpdate, CostRecordResponse, ImportResult
from ..crud import apply_create, apply_update, apply_batch_import, get_batch_or_404
from ..effective import current_query

router = APIRouter(
    prefix="/api/cost-records",
    tags=["成本核算"]
)

MODEL_NAME = "CostRecord"


def _resolve_current(db: Session, record_id: int) -> CostRecord:
    row = db.query(CostRecord).filter(CostRecord.id == record_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="成本记录不存在")
    seen = set()
    while row.superseded_by_id and row.superseded_by_id not in seen:
        seen.add(row.id)
        row = db.query(CostRecord).filter(CostRecord.id == row.superseded_by_id).first()
        if row is None:
            raise HTTPException(status_code=404, detail="成本记录不存在")
    return row


@router.post("/", response_model=CostRecordResponse)
def create_cost_record(record: CostRecordCreate, db: Session = Depends(get_db)):
    get_batch_or_404(db, record.batch_id)
    return apply_create(db, CostRecord, MODEL_NAME, record.dict())

@router.post("/import/", response_model=ImportResult[CostRecordResponse])
def import_cost_records(records: List[CostRecordCreate], db: Session = Depends(get_db)):
    rows = apply_batch_import(db, CostRecord, MODEL_NAME, [r.dict() for r in records])
    return {"created_count": len(rows), "ids": [r.id for r in rows], "records": rows}

@router.get("/", response_model=List[CostRecordResponse])
def get_cost_records(skip: int = 0, limit: int = 100, batch_id: int = None, cost_type: str = None, db: Session = Depends(get_db)):
    query = current_query(db, CostRecord)
    if batch_id:
        query = query.filter(CostRecord.batch_id == batch_id)
    if cost_type:
        query = query.filter(CostRecord.cost_type == cost_type)
    records = query.offset(skip).limit(limit).all()
    return records

@router.get("/{record_id}/", response_model=CostRecordResponse)
def get_cost_record(record_id: int, db: Session = Depends(get_db)):
    return _resolve_current(db, record_id)

@router.put("/{record_id}/", response_model=CostRecordResponse)
def update_cost_record(record_id: int, record: CostRecordUpdate, db: Session = Depends(get_db)):
    db_record = _resolve_current(db, record_id)
    get_batch_or_404(db, db_record.batch_id)
    return apply_update(db, CostRecord, MODEL_NAME, db_record, record.dict(exclude_unset=True))

@router.delete("/{record_id}/")
def delete_cost_record(record_id: int, db: Session = Depends(get_db)):
    db_record = db.query(CostRecord).filter(CostRecord.id == record_id).first()
    if not db_record:
        raise HTTPException(status_code=404, detail="成本记录不存在")
    batch = db.query(Batch).filter(Batch.id == db_record.batch_id).first()
    if batch and batch.status == "closed":
        raise HTTPException(
            status_code=409,
            detail={"code": "batch_signed", "message": "批次已关闭签署，只能冲正不能删除"},
        )

    db.delete(db_record)
    db.commit()
    return {"message": "成本记录删除成功"}
