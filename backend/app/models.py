from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Date,
    DateTime,
    ForeignKey,
    Text,
    CheckConstraint,
    UniqueConstraint,
    Boolean,
)
from sqlalchemy.orm import relationship
from datetime import datetime
from .database import Base

# 约束分层：
# - 必须大于零的字段建 > 0 的 CHECK；
# - 允许为零 / 可冲正归零的字段建 >= 0 的 CHECK（负数在数据库层也无法落库，
#   “归零”只能由复核服务以冲正事实写入）；
# - 这些 CHECK 只对新建库生效；旧库由启动扫描 + 复核流程处理，不直接删数据。


class Pond(Base):
    __tablename__ = "ponds"
    __table_args__ = (
        CheckConstraint("area > 0", name="ck_ponds_area_positive"),
        CheckConstraint("water_depth > 0", name="ck_ponds_depth_positive"),
    )

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, index=True, nullable=False)
    area = Column(Float, nullable=False, comment="面积(亩)")
    water_depth = Column(Float, nullable=False, comment="水深(米)")
    species = Column(String(100), comment="养殖品种")
    status = Column(String(20), default="active", comment="状态: active, inactive")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    batches = relationship("Batch", back_populates="pond")


class Batch(Base):
    __tablename__ = "batches"

    id = Column(Integer, primary_key=True, index=True)
    batch_number = Column(String(50), unique=True, index=True, nullable=False, comment="批次号")
    pond_id = Column(Integer, ForeignKey("ponds.id"), nullable=False)
    species = Column(String(100), nullable=False, comment="养殖品种")
    stocking_date = Column(Date, nullable=False, comment="放苗日期")
    estimated_harvest_date = Column(Date, comment="预计收获日期")
    actual_harvest_date = Column(Date, comment="实际收获日期")
    status = Column(String(20), default="active", comment="状态: active, harvested, closed")
    # 签署/关闭后批次进入冻结态：事实记录只能冲正、不能直接改写。
    signed_at = Column(DateTime, nullable=True, comment="批次签署(结算冻结)时间")
    freeze_version = Column(Integer, nullable=False, default=0, comment="冻结版本号，每次签署/冲正递增")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    pond = relationship("Pond", back_populates="batches")
    stocking_records = relationship("StockingRecord", back_populates="batch")
    feeding_records = relationship("FeedingRecord", back_populates="batch")
    water_quality_records = relationship("WaterQualityRecord", back_populates="batch")
    medication_records = relationship("MedicationRecord", back_populates="batch")
    cost_records = relationship("CostRecord", back_populates="batch")
    harvest_sales = relationship("HarvestSale", back_populates="batch")


class StockingRecord(Base):
    __tablename__ = "stocking_records"
    __table_args__ = (
        # quantity 可经冲正归零，故只拦负数；单重永远必须 > 0。
        CheckConstraint("quantity >= 0", name="ck_stocking_quantity_nonneg"),
        CheckConstraint("weight_per_unit IS NULL OR weight_per_unit > 0",
                        name="ck_stocking_wpu_positive"),
        CheckConstraint("total_weight IS NULL OR total_weight >= 0",
                        name="ck_stocking_total_weight_nonneg"),
    )

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    species = Column(String(100), nullable=False, comment="品种")
    quantity = Column(Integer, nullable=False, comment="数量(尾)")
    source = Column(String(200), comment="来源")
    batch_number = Column(String(50), comment="苗种批次号")
    weight_per_unit = Column(Float, comment="单重(克/尾)")
    total_weight = Column(Float, comment="总重量(公斤)")
    notes = Column(Text, comment="备注")
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("Batch", back_populates="stocking_records")


class FeedingRecord(Base):
    __tablename__ = "feeding_records"
    __table_args__ = (
        CheckConstraint("feed_quantity >= 0", name="ck_feeding_quantity_nonneg"),
        CheckConstraint("water_temperature IS NULL OR (water_temperature >= 0 AND water_temperature <= 50)",
                        name="ck_feeding_water_temp_range"),
    )

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    feeding_date = Column(Date, nullable=False, comment="投喂日期")
    feed_type = Column(String(100), nullable=False, comment="饲料类型")
    feed_quantity = Column(Float, nullable=False, comment="投喂量(公斤)")
    feeding_time = Column(String(20), comment="投喂时间")
    weather = Column(String(50), comment="天气情况")
    water_temperature = Column(Float, comment="水温(℃)")
    notes = Column(Text, comment="备注")
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("Batch", back_populates="feeding_records")


class WaterQualityRecord(Base):
    __tablename__ = "water_quality_records"
    __table_args__ = (
        CheckConstraint("water_temperature IS NULL OR (water_temperature >= 0 AND water_temperature <= 50)",
                        name="ck_wq_temperature_range"),
        CheckConstraint("ph_value IS NULL OR (ph_value >= 0 AND ph_value <= 14)",
                        name="ck_wq_ph_range"),
        CheckConstraint("dissolved_oxygen IS NULL OR dissolved_oxygen >= 0",
                        name="ck_wq_do_nonneg"),
        CheckConstraint("ammonia_nitrogen IS NULL OR ammonia_nitrogen >= 0",
                        name="ck_wq_ammonia_nonneg"),
        CheckConstraint("nitrite IS NULL OR nitrite >= 0",
                        name="ck_wq_nitrite_nonneg"),
        CheckConstraint("transparency IS NULL OR transparency >= 0",
                        name="ck_wq_transparency_nonneg"),
    )

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    record_date = Column(Date, nullable=False, comment="检测日期")
    record_time = Column(String(20), comment="检测时间")
    water_temperature = Column(Float, comment="水温(℃)")
    ph_value = Column(Float, comment="pH值")
    dissolved_oxygen = Column(Float, comment="溶解氧(mg/L)")
    ammonia_nitrogen = Column(Float, comment="氨氮(mg/L)")
    nitrite = Column(Float, comment="亚硝酸盐(mg/L)")
    transparency = Column(Float, comment="透明度(cm)")
    notes = Column(Text, comment="备注")
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("Batch", back_populates="water_quality_records")


class MedicationRecord(Base):
    __tablename__ = "medication_records"
    __table_args__ = (
        CheckConstraint("dosage IS NULL OR dosage >= 0", name="ck_medication_dosage_nonneg"),
    )

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    medication_date = Column(Date, nullable=False, comment="用药日期")
    drug_name = Column(String(200), nullable=False, comment="药品名称")
    drug_type = Column(String(50), comment="药品类型")
    dosage = Column(Float, comment="用量")
    dosage_unit = Column(String(20), default="kg", comment="用量单位")
    administration_method = Column(String(100), comment="施用方法")
    purpose = Column(String(200), comment="用途")
    manufacturer = Column(String(200), comment="生产厂家")
    batch_number = Column(String(50), comment="药品批次号")
    notes = Column(Text, comment="备注")
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("Batch", back_populates="medication_records")


class CostRecord(Base):
    __tablename__ = "cost_records"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_cost_amount_nonneg"),
        CheckConstraint("quantity IS NULL OR quantity >= 0", name="ck_cost_quantity_nonneg"),
        CheckConstraint("unit_price IS NULL OR unit_price > 0", name="ck_cost_unit_price_positive"),
    )

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    cost_date = Column(Date, nullable=False, comment="费用日期")
    cost_type = Column(String(50), nullable=False, comment="费用类型: feed, medicine, labor, electricity, other")
    amount = Column(Float, nullable=False, comment="金额(元)")
    description = Column(String(500), comment="费用描述")
    quantity = Column(Float, comment="数量")
    unit = Column(String(20), comment="单位")
    unit_price = Column(Float, comment="单价")
    notes = Column(Text, comment="备注")
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("Batch", back_populates="cost_records")


class HarvestSale(Base):
    __tablename__ = "harvest_sales"
    __table_args__ = (
        CheckConstraint("weight >= 0", name="ck_harvest_weight_nonneg"),
        CheckConstraint("unit_price > 0", name="ck_harvest_price_positive"),
        CheckConstraint("total_amount IS NULL OR total_amount >= 0",
                        name="ck_harvest_total_nonneg"),
    )

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    sale_date = Column(Date, nullable=False, comment="销售日期")
    weight = Column(Float, nullable=False, comment="重量(公斤)")
    unit_price = Column(Float, nullable=False, comment="单价(元/公斤)")
    total_amount = Column(Float, comment="总金额(元)")
    buyer = Column(String(200), comment="买家")
    batch_number = Column(String(50), comment="追溯批次号")
    quality_grade = Column(String(50), comment="质量等级")
    notes = Column(Text, comment="备注")
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("Batch", back_populates="harvest_sales")


class DataReview(Base):
    """异常生产数据的复核记录（只登记、不删除旧值）。

    来源 source：
      - legacy_scan：服务启动时从旧库扫描出的历史异常值；
      - write_guard：写入路径在数据库底层（CHECK/IntegrityError）拦下的异常；
      - manual：人工上报。
    状态 status：open → approved/rejected；approved 后随更正/冲正进入
    corrected/reversed。
    """

    __tablename__ = "data_reviews"
    __table_args__ = (
        UniqueConstraint("dedup_key", name="uq_review_dedup_key"),
    )

    id = Column(Integer, primary_key=True, index=True)
    entity = Column(String(30), nullable=False, index=True, comment="实体: pond/stocking/...")
    record_id = Column(Integer, nullable=False, index=True, comment="对应业务表主键")
    batch_id = Column(Integer, nullable=True, index=True, comment="所属批次(塘口为空)")
    field = Column(String(50), nullable=False, comment="异常字段")
    bad_value = Column(Float, nullable=True, comment="异常值快照")
    rule_code = Column(String(30), nullable=False, comment="违反的规则码")
    source = Column(String(30), nullable=False, comment="来源: legacy_scan/write_guard/manual")
    impact = Column(String(200), nullable=False, default="", comment="波及的周期指标(逗号分隔)")
    entered_settlement = Column(Boolean, nullable=False, default=False, comment="是否已进入月末结算/批次已签署")
    status = Column(String(20), nullable=False, default="open",
                    comment="open/approved/rejected/corrected/reversed")
    dedup_key = Column(String(200), nullable=False, comment="去重键: entity:record_id:field:rule")
    decision_note = Column(Text, nullable=True, comment="批准/驳回说明")
    requested_value = Column(Float, nullable=True, comment="更正申请值")
    correction_id = Column(Integer, nullable=True, comment="生效的更正/冲正流水ID")
    created_at = Column(DateTime, default=datetime.utcnow)
    reviewed_at = Column(DateTime, nullable=True)
    reviewed_by = Column(String(100), nullable=True)


class RecordCorrection(Base):
    """更正 / 冲正事实流水（append-only，不删除、不覆盖）。

    kind：
      - correction：未签署批次，经批准把值更正为正确值；
      - reversal：已签署批次，只能补一笔反向事实把有效贡献冲平为 0。
    status：pending（已批准待落账）→ applied / cancelled。
    并发防护：
      - review_id 唯一 → 一条复核最多一笔生效流水（更正与更正/关闭互斥）；
      - idempotency_key 唯一 → 同一申请重复提交不产生两笔冲正；
      - status 条件 UPDATE 认领 → 两个并发执行者只有一个能 applied。
    """

    __tablename__ = "record_corrections"
    __table_args__ = (
        # 一条复核对同一字段最多一笔生效流水；冲正主字段时可联动派生字段。
        UniqueConstraint("review_id", "field", name="uq_correction_review_field"),
        UniqueConstraint("idempotency_key", name="uq_correction_idempotency"),
    )

    id = Column(Integer, primary_key=True, index=True)
    review_id = Column(Integer, ForeignKey("data_reviews.id"), nullable=False)
    entity = Column(String(30), nullable=False, index=True)
    record_id = Column(Integer, nullable=False, index=True)
    batch_id = Column(Integer, nullable=True, index=True)
    field = Column(String(50), nullable=False)
    before_value = Column(Float, nullable=False, comment="落账前的原始值")
    after_value = Column(Float, nullable=False, comment="更正目标值；冲正为0")
    kind = Column(String(20), nullable=False, comment="correction/reversal")
    status = Column(String(20), nullable=False, default="pending",
                    comment="pending/applied/cancelled")
    batch_signed = Column(Boolean, nullable=False, default=False, comment="落账时批次是否已签署")
    idempotency_key = Column(String(200), nullable=False)
    reason = Column(Text, nullable=True)
    approved_by = Column(String(100), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    applied_at = Column(DateTime, nullable=True)
    # 冲正所关联的批次冻结版本；落账成功后批次 freeze_version 推进到该值。
    freeze_version = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class AnalysisSnapshot(Base):
    """周期分析结果按“数据有效版本”持久化，重启后直接重放。"""

    __tablename__ = "analysis_snapshots"
    __table_args__ = (
        UniqueConstraint("batch_id", name="uq_snapshot_batch"),
    )

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False, comment="生成时批次 freeze_version")
    payload_json = Column(Text, nullable=False, comment="周期分析结果 JSON")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
