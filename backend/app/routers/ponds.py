from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from ..database import get_db
from ..models import Pond
from ..schemas import PondCreate, PondUpdate, PondResponse
from ..validation import validate_or_raise
from ..effective import current_query

router = APIRouter(
    prefix="/api/ponds",
    tags=["塘口管理"]
)


def _resolve_current(db: Session, pond_id: int) -> Pond:
    """按ID取塘口；若该版本已被更正替代，沿链返回最新有效版本。"""
    pond = db.query(Pond).filter(Pond.id == pond_id).first()
    if not pond:
        raise HTTPException(status_code=404, detail="塘口不存在")
    seen = set()
    while pond.superseded_by_id and pond.superseded_by_id not in seen:
        seen.add(pond.id)
        pond = db.query(Pond).filter(Pond.id == pond.superseded_by_id).first()
        if pond is None:
            raise HTTPException(status_code=404, detail="塘口不存在")
    return pond


@router.post("/", response_model=PondResponse)
def create_pond(pond: PondCreate, db: Session = Depends(get_db)):
    validate_or_raise("Pond", pond.dict())
    db_pond = current_query(db, Pond).filter(Pond.name == pond.name).first()
    if db_pond:
        raise HTTPException(status_code=400, detail="塘口名称已存在")
    new_pond = Pond(**pond.dict())
    db.add(new_pond)
    db.commit()
    db.refresh(new_pond)
    return new_pond

@router.get("/", response_model=List[PondResponse])
def get_ponds(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    ponds = current_query(db, Pond).offset(skip).limit(limit).all()
    return ponds

@router.get("/{pond_id}/", response_model=PondResponse)
def get_pond(pond_id: int, db: Session = Depends(get_db)):
    return _resolve_current(db, pond_id)

@router.put("/{pond_id}/", response_model=PondResponse)
def update_pond(pond_id: int, pond: PondUpdate, db: Session = Depends(get_db)):
    db_pond = _resolve_current(db, pond_id)

    update_data = pond.dict(exclude_unset=True)
    validate_or_raise("Pond", update_data, partial=True)

    if "name" in update_data:
        clash = current_query(db, Pond).filter(
            Pond.name == update_data["name"], Pond.id != db_pond.id
        ).first()
        if clash:
            raise HTTPException(status_code=400, detail="塘口名称已存在")

    for key, value in update_data.items():
        setattr(db_pond, key, value)

    db.commit()
    db.refresh(db_pond)
    return db_pond

@router.delete("/{pond_id}/")
def delete_pond(pond_id: int, db: Session = Depends(get_db)):
    db_pond = db.query(Pond).filter(Pond.id == pond_id).first()
    if not db_pond:
        raise HTTPException(status_code=404, detail="塘口不存在")

    db.delete(db_pond)
    db.commit()
    return {"message": "塘口删除成功"}
