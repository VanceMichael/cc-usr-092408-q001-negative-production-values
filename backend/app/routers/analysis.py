from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..errors import NotFoundError
from ..models import (
    AnalysisSnapshot,
    Batch,
    Pond,
    StockingRecord,
    FeedingRecord,
    CostRecord,
    HarvestSale,
    WaterQualityRecord,
    MedicationRecord,
)
from ..schemas import (
    CultureCycleAnalysis,
    BatchTraceability,
    BatchInfo,
    PondInfo,
)
from ..services.analysis import effective_records, get_cycle_snapshot
from ..services.entities import applied_corrections_for_records, effective_copy

router = APIRouter(
    prefix="/api/analysis",
    tags=["养殖周期分析"]
)


@router.get("/cycle/{batch_id}/", response_model=CultureCycleAnalysis)
def analyze_cycle(
    batch_id: int,
    refresh: int = 0,
    db: Session = Depends(get_db),
):
    """周期分析。

    读数与详情/追溯共享同一有效版本；结果按批次 freeze_version 持久化，
    服务重启后版本未变直接重放快照（replayed=true）。
    """
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise NotFoundError("批次不存在")

    existing = (
        db.query(AnalysisSnapshot)
        .filter(AnalysisSnapshot.batch_id == batch.id)
        .first()
    )
    was_replay_ready = existing is not None and existing.version == batch.freeze_version

    payload = get_cycle_snapshot(db, batch, force_refresh=bool(refresh))
    payload["replayed"] = bool(was_replay_ready and not refresh)
    return payload


@router.get("/traceability/{batch_id}/", response_model=BatchTraceability)
def batch_traceability(batch_id: int, db: Session = Depends(get_db)):
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise NotFoundError("批次不存在")

    pond = db.query(Pond).filter(Pond.id == batch.pond_id).first()
    pond_effective = None
    if pond is not None:
        corrections = applied_corrections_for_records(db, [pond], "pond")
        pond_effective = effective_copy(pond, "pond", corrections)

    stocking_records = effective_records(db, StockingRecord, "stocking", batch.id)
    feeding_records = effective_records(db, FeedingRecord, "feeding", batch.id)
    water_quality_records = effective_records(db, WaterQualityRecord, "water_quality", batch.id)
    medication_records = effective_records(db, MedicationRecord, "medication", batch.id)
    cost_records = effective_records(db, CostRecord, "cost", batch.id)
    harvest_sales = effective_records(db, HarvestSale, "harvest", batch.id)

    return BatchTraceability(
        batch=BatchInfo(
            batch_number=batch.batch_number,
            species=batch.species,
            stocking_date=batch.stocking_date,
            harvest_date=batch.actual_harvest_date,
            status=batch.status,
            pond_id=batch.pond_id,
            data_version=batch.freeze_version,
        ),
        pond_info=PondInfo(
            name=pond_effective.name if pond_effective else None,
            area=pond_effective.area if pond_effective else None,
            water_depth=pond_effective.water_depth if pond_effective else None,
        ),
        stocking_records=[
            {
                "species": r.species,
                "quantity": r.quantity,
                "source": r.source,
                "batch_number": r.batch_number,
                "stocking_date": r.created_at.date() if r.created_at else None,
            }
            for r in stocking_records
        ],
        feeding_records=[
            {
                "feeding_date": r.feeding_date,
                "feed_type": r.feed_type,
                "quantity": r.feed_quantity,
                "unit": "kg",
            }
            for r in feeding_records
        ],
        water_quality_records=[
            {
                "record_date": r.record_date,
                "water_temperature": r.water_temperature,
                "ph_value": r.ph_value,
                "dissolved_oxygen": r.dissolved_oxygen,
            }
            for r in water_quality_records
        ],
        medication_records=[
            {
                "medication_date": r.medication_date,
                "medication_name": r.drug_name,
                "dosage": r.dosage,
                "unit": r.dosage_unit,
            }
            for r in medication_records
        ],
        cost_records=[
            {
                "cost_date": r.cost_date,
                "cost_type": r.cost_type,
                "amount": r.amount,
                "description": r.description,
            }
            for r in cost_records
        ],
        harvest_sales=[
            {
                "sale_date": r.sale_date,
                "weight": r.weight,
                "unit_price": r.unit_price,
                "total_amount": r.total_amount,
                "buyer": r.buyer,
            }
            for r in harvest_sales
        ],
    )


@router.get("/trace-by-number/{batch_number}/", response_model=BatchTraceability)
def trace_by_batch_number(batch_number: str, db: Session = Depends(get_db)):
    batch = db.query(Batch).filter(Batch.batch_number == batch_number).first()
    if not batch:
        raise NotFoundError(f"批次号 {batch_number} 不存在")
    return batch_traceability(batch.id, db)
