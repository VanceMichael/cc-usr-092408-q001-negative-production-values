"""数量类记录路由工厂：create / update / bulk / list / get / delete 一套口径。"""

from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..errors import NotFoundError
from ..services.entities import ENTITY_MODELS
from ..services.guards import ensure_batch_writable
from ..services.records import (
    bulk_create_records,
    create_record,
    get_effective,
    list_effective,
    update_record,
)


def build_record_router(
    *,
    entity: str,
    prefix: str,
    tag: str,
    create_schema: type,
    update_schema: type,
    response_schema: type,
    bulk_schema: type,
    not_found_message: str,
    derive: Optional[Callable] = None,
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=[tag])

    @router.post("/", response_model=response_schema, status_code=201)
    def _create(payload: create_schema, db: Session = Depends(get_db)):
        return create_record(db, entity, payload.model_dump(), derive=derive)

    @router.post("/bulk/", status_code=201)
    def _bulk(payload: bulk_schema, db: Session = Depends(get_db)):
        ids = bulk_create_records(
            db, entity, [item.model_dump() for item in payload.items], derive=derive
        )
        return {"created": len(ids), "ids": ids}

    @router.get("/", response_model=List[response_schema])
    def _list(
        skip: int = 0,
        limit: int = 100,
        batch_id: Optional[int] = None,
        cost_type: Optional[str] = None,
        db: Session = Depends(get_db),
    ):
        filters: Dict[str, Any] = {"batch_id": batch_id}
        if entity == "cost":
            filters["cost_type"] = cost_type
        records = list_effective(db, entity, filters)
        return records[skip:skip + limit]

    @router.get("/{record_id}/", response_model=response_schema)
    def _get(record_id: int, db: Session = Depends(get_db)):
        return get_effective(db, entity, record_id)

    @router.put("/{record_id}/", response_model=response_schema)
    def _update(record_id: int, payload: update_schema, db: Session = Depends(get_db)):
        return update_record(
            db, entity, record_id, payload.model_dump(exclude_unset=True), derive=derive
        )

    @router.delete("/{record_id}/")
    def _delete(record_id: int, db: Session = Depends(get_db)):
        from ..services.guards import bump_batch_version
        model = ENTITY_MODELS[entity]
        record = db.query(model).filter(model.id == record_id).first()
        if record is None:
            raise NotFoundError(not_found_message)
        batch_id = record.batch_id
        ensure_batch_writable(db, batch_id)
        db.delete(record)
        db.flush()
        bump_batch_version(db, batch_id)
        db.commit()
        return {"message": f"{not_found_message}删除成功"}

    return router
