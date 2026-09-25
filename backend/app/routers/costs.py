from ..schemas import (
    CostRecordCreate,
    CostRecordUpdate,
    CostRecordResponse,
    CostBulkImport,
)
from ._factory import build_record_router

router = build_record_router(
    entity="cost",
    prefix="/api/cost-records",
    tag="成本核算",
    create_schema=CostRecordCreate,
    update_schema=CostRecordUpdate,
    response_schema=CostRecordResponse,
    bulk_schema=CostBulkImport,
    not_found_message="成本记录不存在",
)
