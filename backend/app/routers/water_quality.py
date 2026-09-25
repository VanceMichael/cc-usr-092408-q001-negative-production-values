from ..schemas import (
    WaterQualityRecordCreate,
    WaterQualityRecordUpdate,
    WaterQualityRecordResponse,
    WaterQualityBulkImport,
)
from ._factory import build_record_router

router = build_record_router(
    entity="water_quality",
    prefix="/api/water-quality-records",
    tag="水质监测",
    create_schema=WaterQualityRecordCreate,
    update_schema=WaterQualityRecordUpdate,
    response_schema=WaterQualityRecordResponse,
    bulk_schema=WaterQualityBulkImport,
    not_found_message="水质监测记录不存在",
)
