from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from ..database import get_db
from ..models import HarvestSale, Batch
from ..schemas import HarvestSaleCreate, HarvestSaleUpdate, HarvestSaleResponse, ImportResult
from ..crud import apply_create, apply_update, get_batch_or_404
from ..validation import BatchValidationError
from ..effective import current_query

router = APIRouter(
    prefix="/api/harvest-sales",
    tags=["出塘销售"]
)

MODEL_NAME = "HarvestSale"


def _resolve_current(db: Session, sale_id: int) -> HarvestSale:
    row = db.query(HarvestSale).filter(HarvestSale.id == sale_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="出塘销售记录不存在")
    seen = set()
    while row.superseded_by_id and row.superseded_by_id not in seen:
        seen.add(row.id)
        row = db.query(HarvestSale).filter(HarvestSale.id == row.superseded_by_id).first()
        if row is None:
            raise HTTPException(status_code=404, detail="出塘销售记录不存在")
    return row


@router.post("/", response_model=HarvestSaleResponse)
def create_harvest_sale(sale: HarvestSaleCreate, db: Session = Depends(get_db)):
    get_batch_or_404(db, sale.batch_id)
    data = sale.dict()
    if data.get("total_amount") is None:
        data["total_amount"] = data["weight"] * data["unit_price"]
    return apply_create(db, HarvestSale, MODEL_NAME, data)

@router.post("/import/", response_model=ImportResult[HarvestSaleResponse])
def import_harvest_sales(records: List[HarvestSaleCreate], db: Session = Depends(get_db)):
    rows_data: List[dict] = []
    for r in records:
        data = r.dict()
        if data.get("total_amount") is None:
            data["total_amount"] = data["weight"] * data["unit_price"]
        rows_data.append(data)
    # 复用统一批量导入；空列表仍按非法批处理
    from ..crud import apply_batch_import
    if not rows_data:
        raise BatchValidationError([])
    rows = apply_batch_import(db, HarvestSale, MODEL_NAME, rows_data)
    return {"created_count": len(rows), "ids": [r.id for r in rows], "records": rows}

@router.get("/", response_model=List[HarvestSaleResponse])
def get_harvest_sales(skip: int = 0, limit: int = 100, batch_id: int = None, db: Session = Depends(get_db)):
    query = current_query(db, HarvestSale)
    if batch_id:
        query = query.filter(HarvestSale.batch_id == batch_id)
    sales = query.offset(skip).limit(limit).all()
    return sales

@router.get("/{sale_id}/", response_model=HarvestSaleResponse)
def get_harvest_sale(sale_id: int, db: Session = Depends(get_db)):
    return _resolve_current(db, sale_id)

@router.put("/{sale_id}/", response_model=HarvestSaleResponse)
def update_harvest_sale(sale_id: int, sale: HarvestSaleUpdate, db: Session = Depends(get_db)):
    db_sale = _resolve_current(db, sale_id)
    get_batch_or_404(db, db_sale.batch_id)

    update_data = sale.dict(exclude_unset=True)
    if "weight" in update_data or "unit_price" in update_data:
        weight = update_data.get("weight", db_sale.weight)
        unit_price = update_data.get("unit_price", db_sale.unit_price)
        update_data["total_amount"] = weight * unit_price

    return apply_update(db, HarvestSale, MODEL_NAME, db_sale, update_data)

@router.delete("/{sale_id}/")
def delete_harvest_sale(sale_id: int, db: Session = Depends(get_db)):
    db_sale = db.query(HarvestSale).filter(HarvestSale.id == sale_id).first()
    if not db_sale:
        raise HTTPException(status_code=404, detail="出塘销售记录不存在")
    batch = db.query(Batch).filter(Batch.id == db_sale.batch_id).first()
    if batch and batch.status == "closed":
        raise HTTPException(
            status_code=409,
            detail={"code": "batch_signed", "message": "批次已关闭签署，只能冲正不能删除"},
        )

    db.delete(db_sale)
    db.commit()
    return {"message": "出塘销售记录删除成功"}
