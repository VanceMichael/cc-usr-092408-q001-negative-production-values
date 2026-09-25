"""周期分析计算与快照重放。

计算只通过 :mod:`services.entities` 的有效版本口径读数，因此详情、追溯与周期
分析共享同一份更正/冲正后的数据。结果按批次冻结版本存入 ``analysis_snapshots``：
服务重启后版本未变直接重放快照，版本变化（发生更正/冲正/签署）才重算。
"""

import json
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from ..models import (
    AnalysisSnapshot,
    Batch,
    CostRecord,
    FeedingRecord,
    HarvestSale,
    Pond,
    StockingRecord,
)
from .entities import applied_corrections_for_records, effective_attr, effective_copy


def _sum_field(db: Session, model, entity: str, batch_id: int, field: str) -> float:
    records = db.query(model).filter(model.batch_id == batch_id).all()
    corrections = applied_corrections_for_records(db, records, entity)
    total = 0.0
    for record in records:
        total += float(effective_attr(record, field, corrections.get((entity, record.id, field))) or 0.0)
    return total


def _effective_pond(db: Session, pond: Optional[Pond]):
    if pond is None:
        return None
    corrections = applied_corrections_for_records(db, [pond], "pond")
    # 游离副本：有效值不回写底表。
    return effective_copy(pond, "pond", corrections)


def compute_cycle(db: Session, batch: Batch) -> Dict[str, Any]:
    pond = db.query(Pond).filter(Pond.id == batch.pond_id).first()
    pond = _effective_pond(db, pond)

    initial_quantity = _sum_field(db, StockingRecord, "stocking", batch.id, "quantity")
    harvest_weight = _sum_field(db, HarvestSale, "harvest", batch.id, "weight")
    feed_total = _sum_field(db, FeedingRecord, "feeding", batch.id, "feed_quantity")
    total_cost = _sum_field(db, CostRecord, "cost", batch.id, "amount")

    harvest_sales = db.query(HarvestSale).filter(HarvestSale.batch_id == batch.id).all()
    sale_corrections = applied_corrections_for_records(db, harvest_sales, "harvest")
    total_revenue = 0.0
    for sale in harvest_sales:
        total_amount = effective_attr(
            sale, "total_amount", sale_corrections.get(("harvest", sale.id, "total_amount"))
        )
        weight = effective_attr(
            sale, "weight", sale_corrections.get(("harvest", sale.id, "weight"))
        )
        unit_price = effective_attr(
            sale, "unit_price", sale_corrections.get(("harvest", sale.id, "unit_price"))
        )
        if total_amount is not None:
            total_revenue += float(total_amount)
        else:
            total_revenue += float(weight or 0.0) * float(unit_price or 0.0)

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
    yield_per_mu = harvest_weight / area if pond and area > 0 else 0.0
    profit = total_revenue - total_cost

    # 成本分类（有效值口径）
    cost_records = db.query(CostRecord).filter(CostRecord.batch_id == batch.id).all()
    cost_corrections = applied_corrections_for_records(db, cost_records, "cost")
    cost_breakdown: Dict[str, float] = {}
    for record in cost_records:
        amount = float(
            effective_attr(record, "amount", cost_corrections.get(("cost", record.id, "amount")))
            or 0.0
        )
        cost_breakdown[record.cost_type] = cost_breakdown.get(record.cost_type, 0.0) + amount
    known_types = ["feed", "medicine", "labor", "electricity"]
    other_cost = sum(v for k, v in cost_breakdown.items() if k not in known_types)
    cost_summary = {
        "feed_cost": cost_breakdown.get("feed", 0.0),
        "medicine_cost": cost_breakdown.get("medicine", 0.0),
        "labor_cost": cost_breakdown.get("labor", 0.0),
        "electricity_cost": cost_breakdown.get("electricity", 0.0),
        "other_cost": other_cost,
        "total_cost": total_cost,
    }

    feeding_count = (
        db.query(FeedingRecord).filter(FeedingRecord.batch_id == batch.id).count()
    )
    avg_daily_feed = (
        feed_total / days_cultured if days_cultured and days_cultured > 0 else 0.0
    )
    feeding_summary = {
        "total_feed_weight": feed_total,
        "feeding_count": feeding_count,
        "avg_daily_feed": avg_daily_feed,
    }

    return {
        "batch_number": batch.batch_number,
        "pond_name": pond.name if pond else "未知",
        "species": batch.species,
        "stocking_date": batch.stocking_date.isoformat(),
        "harvest_date": harvest_date.isoformat() if harvest_date else None,
        "days_cultured": days_cultured,
        "initial_quantity": int(initial_quantity),
        "harvest_weight": round(harvest_weight, 4),
        "survival_rate": round(survival_rate, 2),
        "feed_total": round(feed_total, 4),
        "feed_conversion_ratio": round(feed_conversion_ratio, 2),
        "area": area,
        "yield_per_mu": round(yield_per_mu, 2),
        "total_cost": round(total_cost, 2),
        "total_revenue": round(total_revenue, 2),
        "profit": round(profit, 2),
        "cost_summary": cost_summary,
        "feeding_summary": feeding_summary,
        "data_version": batch.freeze_version,
    }


def get_cycle_snapshot(db: Session, batch: Batch, force_refresh: bool = False) -> Dict[str, Any]:
    """取有效版本一致的周期分析；优先重放持久化快照。"""
    snapshot = (
        db.query(AnalysisSnapshot)
        .filter(AnalysisSnapshot.batch_id == batch.id)
        .first()
    )
    if (
        not force_refresh
        and snapshot is not None
        and snapshot.version == batch.freeze_version
    ):
        return json.loads(snapshot.payload_json)

    payload = compute_cycle(db, batch)
    if snapshot is None:
        snapshot = AnalysisSnapshot(
            batch_id=batch.id,
            version=batch.freeze_version,
            payload_json=json.dumps(payload, ensure_ascii=False),
        )
        db.add(snapshot)
    else:
        snapshot.version = batch.freeze_version
        snapshot.payload_json = json.dumps(payload, ensure_ascii=False)
    db.commit()
    return payload


def effective_records(db: Session, model, entity: str, batch_id: int) -> List[Any]:
    """追溯/详情共用：返回已应用有效值的游离副本（不写库）。"""
    records = db.query(model).filter(model.batch_id == batch_id).all()
    corrections = applied_corrections_for_records(db, records, entity)
    return [effective_copy(record, entity, corrections) for record in records]
