from pydantic import BaseModel
from typing import Optional, List, Dict
from datetime import date, datetime


class ORMModel(BaseModel):
    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# 塘口
# ---------------------------------------------------------------------------
class PondBase(BaseModel):
    name: str
    area: float
    water_depth: float
    species: Optional[str] = None
    status: Optional[str] = "active"

class PondCreate(PondBase):
    pass

class PondUpdate(BaseModel):
    name: Optional[str] = None
    area: Optional[float] = None
    water_depth: Optional[float] = None
    species: Optional[str] = None
    status: Optional[str] = None

class PondResponse(PondBase, ORMModel):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# 批次
# ---------------------------------------------------------------------------
class BatchBase(BaseModel):
    batch_number: str
    pond_id: int
    species: str
    stocking_date: date
    estimated_harvest_date: Optional[date] = None
    actual_harvest_date: Optional[date] = None
    status: Optional[str] = "active"

class BatchCreate(BatchBase):
    pass

class BatchUpdate(BaseModel):
    batch_number: Optional[str] = None
    pond_id: Optional[int] = None
    species: Optional[str] = None
    stocking_date: Optional[date] = None
    estimated_harvest_date: Optional[date] = None
    actual_harvest_date: Optional[date] = None
    status: Optional[str] = None

class BatchResponse(BatchBase, ORMModel):
    id: int
    signed_at: Optional[datetime] = None
    freeze_version: int = 0
    created_at: datetime
    updated_at: Optional[datetime] = None

class BatchSignRequest(BaseModel):
    actual_harvest_date: Optional[date] = None
    note: Optional[str] = None


# ---------------------------------------------------------------------------
# 投苗
# ---------------------------------------------------------------------------
class StockingRecordBase(BaseModel):
    batch_id: int
    species: str
    quantity: int
    source: Optional[str] = None
    batch_number: Optional[str] = None
    weight_per_unit: Optional[float] = None
    total_weight: Optional[float] = None
    notes: Optional[str] = None

class StockingRecordCreate(StockingRecordBase):
    pass

class StockingRecordUpdate(BaseModel):
    batch_id: Optional[int] = None
    species: Optional[str] = None
    quantity: Optional[int] = None
    source: Optional[str] = None
    batch_number: Optional[str] = None
    weight_per_unit: Optional[float] = None
    total_weight: Optional[float] = None
    notes: Optional[str] = None

class StockingRecordResponse(StockingRecordBase, ORMModel):
    id: int
    created_at: datetime


# ---------------------------------------------------------------------------
# 投喂
# ---------------------------------------------------------------------------
class FeedingRecordBase(BaseModel):
    batch_id: int
    feeding_date: date
    feed_type: str
    feed_quantity: float
    feeding_time: Optional[str] = None
    weather: Optional[str] = None
    water_temperature: Optional[float] = None
    notes: Optional[str] = None

class FeedingRecordCreate(FeedingRecordBase):
    pass

class FeedingRecordUpdate(BaseModel):
    batch_id: Optional[int] = None
    feeding_date: Optional[date] = None
    feed_type: Optional[str] = None
    feed_quantity: Optional[float] = None
    feeding_time: Optional[str] = None
    weather: Optional[str] = None
    water_temperature: Optional[float] = None
    notes: Optional[str] = None

class FeedingRecordResponse(FeedingRecordBase, ORMModel):
    id: int
    created_at: datetime


# ---------------------------------------------------------------------------
# 水质
# ---------------------------------------------------------------------------
class WaterQualityRecordBase(BaseModel):
    batch_id: int
    record_date: date
    record_time: Optional[str] = None
    water_temperature: Optional[float] = None
    ph_value: Optional[float] = None
    dissolved_oxygen: Optional[float] = None
    ammonia_nitrogen: Optional[float] = None
    nitrite: Optional[float] = None
    transparency: Optional[float] = None
    notes: Optional[str] = None

class WaterQualityRecordCreate(WaterQualityRecordBase):
    pass

class WaterQualityRecordUpdate(BaseModel):
    batch_id: Optional[int] = None
    record_date: Optional[date] = None
    record_time: Optional[str] = None
    water_temperature: Optional[float] = None
    ph_value: Optional[float] = None
    dissolved_oxygen: Optional[float] = None
    ammonia_nitrogen: Optional[float] = None
    nitrite: Optional[float] = None
    transparency: Optional[float] = None
    notes: Optional[str] = None

class WaterQualityRecordResponse(WaterQualityRecordBase, ORMModel):
    id: int
    created_at: datetime


# ---------------------------------------------------------------------------
# 用药
# ---------------------------------------------------------------------------
class MedicationRecordBase(BaseModel):
    batch_id: int
    medication_date: date
    drug_name: str
    drug_type: Optional[str] = None
    dosage: Optional[float] = None
    dosage_unit: Optional[str] = "kg"
    administration_method: Optional[str] = None
    purpose: Optional[str] = None
    manufacturer: Optional[str] = None
    batch_number: Optional[str] = None
    notes: Optional[str] = None

class MedicationRecordCreate(MedicationRecordBase):
    pass

class MedicationRecordUpdate(BaseModel):
    batch_id: Optional[int] = None
    medication_date: Optional[date] = None
    drug_name: Optional[str] = None
    drug_type: Optional[str] = None
    dosage: Optional[float] = None
    dosage_unit: Optional[str] = None
    administration_method: Optional[str] = None
    purpose: Optional[str] = None
    manufacturer: Optional[str] = None
    batch_number: Optional[str] = None
    notes: Optional[str] = None

class MedicationRecordResponse(MedicationRecordBase, ORMModel):
    id: int
    created_at: datetime


# ---------------------------------------------------------------------------
# 成本
# ---------------------------------------------------------------------------
class CostRecordBase(BaseModel):
    batch_id: int
    cost_date: date
    cost_type: str
    amount: float
    description: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    unit_price: Optional[float] = None
    notes: Optional[str] = None

class CostRecordCreate(CostRecordBase):
    pass

class CostRecordUpdate(BaseModel):
    batch_id: Optional[int] = None
    cost_date: Optional[date] = None
    cost_type: Optional[str] = None
    amount: Optional[float] = None
    description: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    unit_price: Optional[float] = None
    notes: Optional[str] = None

class CostRecordResponse(CostRecordBase, ORMModel):
    id: int
    created_at: datetime


# ---------------------------------------------------------------------------
# 销售
# ---------------------------------------------------------------------------
class HarvestSaleBase(BaseModel):
    batch_id: int
    sale_date: date
    weight: float
    unit_price: float
    total_amount: Optional[float] = None
    buyer: Optional[str] = None
    batch_number: Optional[str] = None
    quality_grade: Optional[str] = None
    notes: Optional[str] = None

class HarvestSaleCreate(HarvestSaleBase):
    pass

class HarvestSaleUpdate(BaseModel):
    batch_id: Optional[int] = None
    sale_date: Optional[date] = None
    weight: Optional[float] = None
    unit_price: Optional[float] = None
    total_amount: Optional[float] = None
    buyer: Optional[str] = None
    batch_number: Optional[str] = None
    quality_grade: Optional[str] = None
    notes: Optional[str] = None

class HarvestSaleResponse(HarvestSaleBase, ORMModel):
    id: int
    created_at: datetime


# ---------------------------------------------------------------------------
# 批量导入（原子：任一记录字段校验失败则整批拒绝，字段路径 items[i].field）
# ---------------------------------------------------------------------------
class PondBulkImport(BaseModel):
    items: List[PondCreate]

class StockingBulkImport(BaseModel):
    items: List[StockingRecordCreate]

class FeedingBulkImport(BaseModel):
    items: List[FeedingRecordCreate]

class WaterQualityBulkImport(BaseModel):
    items: List[WaterQualityRecordCreate]

class MedicationBulkImport(BaseModel):
    items: List[MedicationRecordCreate]

class CostBulkImport(BaseModel):
    items: List[CostRecordCreate]

class HarvestBulkImport(BaseModel):
    items: List[HarvestSaleCreate]

class BulkImportResponse(BaseModel):
    created: int
    ids: List[int]


# ---------------------------------------------------------------------------
# 周期分析与追溯
# ---------------------------------------------------------------------------
class CostSummaryItem(BaseModel):
    type: str
    amount: float

class FeedingSummaryItem(BaseModel):
    feed_type: str
    total_quantity: float
    feeding_count: int

class CultureCycleAnalysis(BaseModel):
    batch_number: str
    pond_name: str
    species: str
    stocking_date: date
    harvest_date: Optional[date] = None
    days_cultured: Optional[int] = None
    initial_quantity: int
    harvest_weight: float
    survival_rate: float
    feed_total: float
    feed_conversion_ratio: float
    area: float
    yield_per_mu: float
    total_cost: float
    total_revenue: float
    profit: float
    cost_summary: Optional[dict] = None
    feeding_summary: Optional[dict] = None
    # 周期分析依据的“有效版本”号；与详情/追溯读取口径一致。
    data_version: int = 0
    replayed: bool = False

class StockingRecordTrace(BaseModel):
    species: str
    quantity: int
    source: Optional[str] = None
    batch_number: Optional[str] = None
    stocking_date: Optional[date] = None

class FeedingRecordTrace(BaseModel):
    feeding_date: date
    feed_type: str
    quantity: float
    unit: Optional[str] = None

class WaterQualityRecordTrace(BaseModel):
    record_date: date
    water_temperature: Optional[float] = None
    ph_value: Optional[float] = None
    dissolved_oxygen: Optional[float] = None

class MedicationRecordTrace(BaseModel):
    medication_date: date
    medication_name: str
    dosage: Optional[float] = None
    unit: Optional[str] = None

class CostRecordTrace(BaseModel):
    cost_date: date
    cost_type: str
    amount: float
    description: Optional[str] = None

class HarvestSaleTrace(BaseModel):
    sale_date: date
    weight: float
    unit_price: float
    total_amount: Optional[float] = None
    buyer: Optional[str] = None

class BatchInfo(BaseModel):
    batch_number: str
    species: str
    stocking_date: date
    harvest_date: Optional[date] = None
    status: str
    pond_id: Optional[int] = None
    data_version: int = 0

class PondInfo(BaseModel):
    name: Optional[str] = None
    area: Optional[float] = None
    water_depth: Optional[float] = None

class BatchTraceability(BaseModel):
    batch: BatchInfo
    pond_info: PondInfo
    stocking_records: List[StockingRecordTrace] = []
    feeding_records: List[FeedingRecordTrace] = []
    water_quality_records: List[WaterQualityRecordTrace] = []
    medication_records: List[MedicationRecordTrace] = []
    cost_records: List[CostRecordTrace] = []
    harvest_sales: List[HarvestSaleTrace] = []


# ---------------------------------------------------------------------------
# 复核 / 更正 / 冲正
# ---------------------------------------------------------------------------
class ReviewResponse(ORMModel):
    id: int
    entity: str
    record_id: int
    batch_id: Optional[int] = None
    field: str
    bad_value: Optional[float] = None
    rule_code: str
    source: str
    impact: str = ""
    entered_settlement: bool = False
    status: str
    dedup_key: str
    decision_note: Optional[str] = None
    requested_value: Optional[float] = None
    correction_id: Optional[int] = None
    created_at: datetime
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None

class ReviewDecisionRequest(BaseModel):
    approve: bool
    note: Optional[str] = None
    reviewed_by: Optional[str] = None

class ResolutionRequest(BaseModel):
    # 未签署批次的更正目标值；已签署批次该值被忽略，强制冲正归零。
    requested_value: Optional[float] = None
    idempotency_key: Optional[str] = None
    reason: Optional[str] = None
    approved_by: Optional[str] = None

class CorrectionResponse(ORMModel):
    id: int
    review_id: int
    entity: str
    record_id: int
    batch_id: Optional[int] = None
    field: str
    before_value: float
    after_value: float
    kind: str
    status: str
    batch_signed: bool
    idempotency_key: str
    reason: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    applied_at: Optional[datetime] = None
    freeze_version: Optional[int] = None
    created_at: datetime

class ReviewSummary(BaseModel):
    total: int
    open: int
    approved: int
    rejected: int
    corrected: int
    reversed: int
    entered_settlement: int
    by_entity: Dict[str, int]
    by_source: Dict[str, int]

class RescanResponse(BaseModel):
    scanned_records: int
    new_reviews: int

class ManualReviewRequest(BaseModel):
    entity: str
    record_id: int
    field: str
    rule_code: str = "manual_report"
    # 不传则按所属批次签署状态自动判定
    entered_settlement: Optional[bool] = None
