from ..schemas import (
    FeedingRecordCreate,
    FeedingRecordUpdate,
    FeedingRecordResponse,
    FeedingBulkImport,
)
from ._factory import build_record_router

router = build_record_router(
    entity="feeding",
    prefix="/api/feeding-records",
    tag="投喂记录",
    create_schema=FeedingRecordCreate,
    update_schema=FeedingRecordUpdate,
    response_schema=FeedingRecordResponse,
    bulk_schema=FeedingBulkImport,
    not_found_message="投喂记录不存在",
)
