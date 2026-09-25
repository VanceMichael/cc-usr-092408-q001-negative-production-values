from fastapi import APIRouter, Depends
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from typing import List

from ..database import get_db
from ..errors import ConflictError, NotFoundError
from ..models import Pond
from ..schemas import (
    PondCreate,
    PondUpdate,
    PondResponse,
    PondBulkImport,
    BulkImportResponse,
)
from ..validation import validate_values
from ..services.entities import applied_corrections_for_records, effective_copy
from ..services.guards import (
    bump_pond_batches_version,
    release_field_corrections,
    signed_batch_on_pond,
)
from ..services.records import _flush_or_convert

router = APIRouter(
    prefix="/api/ponds",
    tags=["塘口管理"]
)


@router.post("/", response_model=PondResponse, status_code=201)
def create_pond(pond: PondCreate, db: Session = Depends(get_db)):
    data = pond.model_dump()
    validate_values("pond", data)
    new_pond = Pond(**data)
    db.add(new_pond)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ConflictError(
            "塘口名称已存在",
            fields=[{"field": "name", "code": "duplicate", "message": "塘口名称已存在"}],
            code="duplicate",
        )
    db.commit()
    db.refresh(new_pond)
    return new_pond


@router.post("/bulk/", response_model=BulkImportResponse, status_code=201)
def bulk_import_ponds(payload: PondBulkImport, db: Session = Depends(get_db)):
    items = [item.model_dump() for item in payload.items]
    if not items:
        from ..errors import ValidationError
        raise ValidationError(
            "导入内容为空",
            fields=[{"field": "items", "code": "empty", "message": "items 不能为空"}],
        )
    errors = []
    names = set()
    for index, item in enumerate(items):
        prefix = f"items[{index}]."
        errors.extend(validate_values("pond", item, prefix=prefix, raise_on_error=False))
        name = item.get("name")
        if name in names:
            errors.append({"field": f"{prefix}name", "code": "duplicate",
                           "message": "批量内塘口名称重复"})
        names.add(name)
        exists = db.query(Pond).filter(Pond.name == name).first()
        if exists:
            errors.append({"field": f"{prefix}name", "code": "duplicate",
                           "message": "塘口名称已存在"})
    if errors:
        from ..errors import ValidationError
        raise ValidationError("批量导入存在未通过校验的数据", fields=errors)

    ponds = [Pond(**item) for item in items]
    db.add_all(ponds)
    db.flush()
    db.commit()
    return BulkImportResponse(created=len(ponds), ids=[p.id for p in ponds])


@router.get("/", response_model=List[PondResponse])
def get_ponds(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    ponds = db.query(Pond).order_by(Pond.id).offset(skip).limit(limit).all()
    corrections = applied_corrections_for_records(db, ponds, "pond")
    return [effective_copy(p, "pond", corrections) for p in ponds]


@router.get("/{pond_id}/", response_model=PondResponse)
def get_pond(pond_id: int, db: Session = Depends(get_db)):
    pond = db.query(Pond).filter(Pond.id == pond_id).first()
    if not pond:
        raise NotFoundError("塘口不存在")
    corrections = applied_corrections_for_records(db, [pond], "pond")
    return effective_copy(pond, "pond", corrections)


@router.put("/{pond_id}/", response_model=PondResponse)
def update_pond(pond_id: int, pond: PondUpdate, db: Session = Depends(get_db)):
    db_pond = db.query(Pond).filter(Pond.id == pond_id).first()
    if not db_pond:
        raise NotFoundError("塘口不存在")

    update_data = pond.model_dump(exclude_unset=True)
    validate_values("pond", update_data)

    changed = [f for f in ("area", "water_depth") if f in update_data]
    if changed and signed_batch_on_pond(db, pond_id) is not None:
        from ..errors import StateConflictError
        raise StateConflictError(
            "塘口名下存在已签署批次，面积/水深不能直接修改，请通过复核冲正处理",
            fields=[{"field": changed[0], "code": "batch_signed",
                     "message": "已结算口径冻结"}],
        )
    release_field_corrections(db, "pond", pond_id, changed)

    for key, value in update_data.items():
        setattr(db_pond, key, value)
    _flush_or_convert(db)
    # 塘口面积影响名下批次的亩产，统一推进其分析版本。
    bump_pond_batches_version(db, pond_id)
    db.commit()
    db.refresh(db_pond)
    return db_pond


@router.delete("/{pond_id}/")
def delete_pond(pond_id: int, db: Session = Depends(get_db)):
    db_pond = db.query(Pond).filter(Pond.id == pond_id).first()
    if not db_pond:
        raise NotFoundError("塘口不存在")
    try:
        db.delete(db_pond)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ConflictError(
            "塘口下仍有养殖批次，不能删除",
            fields=[{"field": "id", "code": "in_use",
                     "message": "请先处理塘口下的批次"}],
            code="in_use",
        )
    return {"message": "塘口删除成功"}
