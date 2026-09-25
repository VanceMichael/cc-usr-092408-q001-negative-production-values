from typing import Any, Dict, Optional

from ..schemas import (
    HarvestSaleCreate,
    HarvestSaleUpdate,
    HarvestSaleResponse,
    HarvestBulkImport,
)
from ._factory import build_record_router


def _derive_total(data: Dict[str, Any], record: Optional[Any]) -> Dict[str, Any]:
    """按重量×单价派生总金额：未显式给总金额或重量/单价变化时重算。"""
    weight = data.get("weight")
    unit_price = data.get("unit_price")
    if weight is not None and unit_price is not None:
        data["total_amount"] = weight * unit_price
    elif weight is not None and record is not None:
        data["total_amount"] = weight * record.unit_price
    elif unit_price is not None and record is not None:
        data["total_amount"] = record.weight * unit_price
    return data


router = build_record_router(
    entity="harvest",
    prefix="/api/harvest-sales",
    tag="出塘销售",
    create_schema=HarvestSaleCreate,
    update_schema=HarvestSaleUpdate,
    response_schema=HarvestSaleResponse,
    bulk_schema=HarvestBulkImport,
    not_found_message="出塘销售记录不存在",
    derive=_derive_total,
)
