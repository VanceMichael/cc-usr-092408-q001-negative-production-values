from ..schemas import (
    MedicationRecordCreate,
    MedicationRecordUpdate,
    MedicationRecordResponse,
    MedicationBulkImport,
)
from ._factory import build_record_router

router = build_record_router(
    entity="medication",
    prefix="/api/medication-records",
    tag="用药记录",
    create_schema=MedicationRecordCreate,
    update_schema=MedicationRecordUpdate,
    response_schema=MedicationRecordResponse,
    bulk_schema=MedicationBulkImport,
    not_found_message="用药记录不存在",
)
