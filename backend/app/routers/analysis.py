import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (
    AnalysisSnapshot,
    Batch,
    CostRecord,
    FeedingRecord,
    HarvestSale,
    StockingRecord,
    MedicationRecord,
    WaterQualityRecord,
)
from ..effective import EffectiveData
from ..schemas import (
    CultureCycleAnalysis,
    BatchTraceability,
    BatchInfo,
    PondInfo,
)

router = APIRouter(
    prefix="/api/analysis",
    tags=["养殖周期分析"],
)


def _build_cycle(db: Session, batch: Batch) -> CultureCycleAnalysis:
    """基于唯一有效版本计算周期分析。"""
    eff = EffectiveData(db, batch)
    pond = eff.pond()

    initial_quantity = eff.sum(StockingRecord, "quantity")
    harvest_weight = eff.sum(HarvestSale, "weight")
    feed_total = eff.sum(FeedingRecord, "feed_quantity")
    total_cost = eff.sum(CostRecord, "amount")
    total_revenue = eff.sum(HarvestSale, "total_amount")

    harvest_date = batch.actual_harvest_date
    days_cultured = None
    if harvest_date:
        days_cultured = (harvest_date - batch.stocking_date).days

    survival_rate = 0.0
    if initial_quantity > 0 and harvest_weight > 0:
        avg_weight_per_fish = 0.5
        estimated_survival = harvest_weight / avg_weight_per_fish
        survival_rate = (estimated_survival / initial_quantity) * 100

    feed_conversion_ratio = 0.0
    if harvest_weight > 0 and feed_total > 0:
        feed_conversion_ratio = feed_total / harvest_weight

    area = pond.area if pond else 0.0
    yield_per_mu = 0.0
    if area and area > 0:
        yield_per_mu = harvest_weight / area

    profit = total_revenue - total_cost

    # 成本分类汇总（有效版本 + 冲正净额）
    cost_rows = eff.rows(CostRecord)
    cost_breakdown: dict = {}
    for row in cost_rows:
        cost_breakdown[row.cost_type] = cost_breakdown.get(row.cost_type, 0.0) + (
            eff.value(row, "amount") or 0.0
        )

    known_types = ["feed", "medicine", "labor", "electricity"]
    other_cost = sum(
        amount for cost_type, amount in cost_breakdown.items()
        if cost_type not in known_types
    )
    cost_summary_dict = {
        "feed_cost": cost_breakdown.get("feed", 0.0),
        "medicine_cost": cost_breakdown.get("medicine", 0.0),
        "labor_cost": cost_breakdown.get("labor", 0.0),
        "electricity_cost": cost_breakdown.get("electricity", 0.0),
        "other_cost": other_cost,
        "total_cost": total_cost,
    }

    feeding_rows = eff.rows(FeedingRecord)
    feeding_count = len(feeding_rows)
    avg_daily_feed = 0.0
    if days_cultured and days_cultured > 0:
        avg_daily_feed = feed_total / days_cultured
    feeding_summary_result = {
        "total_feed_weight": feed_total,
        "feeding_count": feeding_count,
        "avg_daily_feed": avg_daily_feed,
    }

    return CultureCycleAnalysis(
        batch_number=batch.batch_number,
        pond_name=pond.name if pond else "未知",
        species=batch.species,
        stocking_date=batch.stocking_date,
        harvest_date=harvest_date,
        days_cultured=days_cultured,
        initial_quantity=initial_quantity,
        harvest_weight=harvest_weight,
        survival_rate=round(survival_rate, 2),
        feed_total=feed_total,
        feed_conversion_ratio=round(feed_conversion_ratio, 2),
        area=area,
        yield_per_mu=round(yield_per_mu, 2),
        total_cost=total_cost,
        total_revenue=total_revenue,
        profit=profit,
        data_version=eff.fingerprint(),
        cost_summary=cost_summary_dict,
        feeding_summary=feeding_summary_result,
    )


@router.get("/cycle/{batch_id}/", response_model=CultureCycleAnalysis)
def analyze_cycle(
    batch_id: int,
    refresh: bool = False,
    db: Session = Depends(get_db),
):
    """周期分析。

    结果按有效数据版本指纹持久化到 ``analysis_snapshots``：数据未变化时
    直接重放同一快照（服务重启后亦然）；数据版本变化（更正/冲正）后自动
    重算。``refresh=true`` 可强制重算。
    """
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")

    analysis = _build_cycle(db, batch)
    snapshot = db.query(AnalysisSnapshot).filter(AnalysisSnapshot.batch_id == batch_id).first()

    if not refresh and snapshot and snapshot.data_version == analysis.data_version:
        return CultureCycleAnalysis(**json.loads(snapshot.payload_json))

    payload = analysis.model_dump_json()
    if snapshot is None:
        snapshot = AnalysisSnapshot(
            batch_id=batch_id,
            data_version=analysis.data_version,
            payload_json=payload,
        )
        db.add(snapshot)
    else:
        snapshot.data_version = analysis.data_version
        snapshot.payload_json = payload
    db.commit()
    return analysis


@router.get("/traceability/{batch_id}/", response_model=BatchTraceability)
def batch_traceability(batch_id: int, db: Session = Depends(get_db)):
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")

    eff = EffectiveData(db, batch)
    pond = eff.pond()

    stocking = eff.dicts(StockingRecord)
    feeding = eff.dicts(FeedingRecord)
    water_quality = eff.dicts(WaterQualityRecord)
    medication = eff.dicts(MedicationRecord)
    costs = eff.dicts(CostRecord)
    sales = eff.dicts(HarvestSale)

    return BatchTraceability(
        batch=BatchInfo(
            batch_number=batch.batch_number,
            species=batch.species,
            stocking_date=batch.stocking_date,
            harvest_date=batch.actual_harvest_date,
            status=batch.status,
            pond_id=batch.pond_id,
        ),
        pond_info=PondInfo(
            name=pond.name if pond else None,
            area=pond.area if pond else None,
            water_depth=pond.water_depth if pond else None,
        ),
        data_version=eff.fingerprint(),
        stocking_records=[
            {
                "species": r["species"],
                "quantity": r["quantity"],
                "source": r["source"],
                "batch_number": r["batch_number"],
                "stocking_date": r["created_at"].date() if r.get("created_at") else None,
            }
            for r in stocking
        ],
        feeding_records=[
            {
                "feeding_date": r["feeding_date"],
                "feed_type": r["feed_type"],
                "quantity": r["feed_quantity"],
                "unit": "kg",
            }
            for r in feeding
        ],
        water_quality_records=[
            {
                "record_date": r["record_date"],
                "water_temperature": r["water_temperature"],
                "ph_value": r["ph_value"],
                "dissolved_oxygen": r["dissolved_oxygen"],
            }
            for r in water_quality
        ],
        medication_records=[
            {
                "medication_date": r["medication_date"],
                "medication_name": r["drug_name"],
                "dosage": r["dosage"],
                "unit": r["dosage_unit"],
            }
            for r in medication
        ],
        cost_records=[
            {
                "cost_date": r["cost_date"],
                "cost_type": r["cost_type"],
                "amount": r["amount"],
                "description": r["description"],
            }
            for r in costs
        ],
        harvest_sales=[
            {
                "sale_date": r["sale_date"],
                "weight": r["weight"],
                "unit_price": r["unit_price"],
                "total_amount": r["total_amount"],
                "buyer": r["buyer"],
            }
            for r in sales
        ],
    )


@router.get("/trace-by-number/{batch_number}/", response_model=BatchTraceability)
def trace_by_batch_number(batch_number: str, db: Session = Depends(get_db)):
    batch = db.query(Batch).filter(Batch.batch_number == batch_number).first()
    if not batch:
        raise HTTPException(status_code=404, detail=f"批次号 {batch_number} 不存在")
    return batch_traceability(batch.id, db)
