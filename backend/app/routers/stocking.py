from ..schemas import (
    StockingRecordCreate,
    StockingRecordUpdate,
    StockingRecordResponse,
    StockingBulkImport,
)
from ._factory import build_record_router

router = build_record_router(
    entity="stocking",
    prefix="/api/stocking-records",
    tag="投苗记录",
    create_schema=StockingRecordCreate,
    update_schema=StockingRecordUpdate,
    response_schema=StockingRecordResponse,
    bulk_schema=StockingBulkImport,
    not_found_message="投苗记录不存在",
)
