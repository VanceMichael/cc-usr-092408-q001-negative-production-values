from sqlalchemy import Column, Integer, String, Float, Date, DateTime, ForeignKey, Text, Boolean, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime
from .database import Base


class Pond(Base):
    __tablename__ = "ponds"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, index=True, nullable=False)
    area = Column(Float, nullable=False, comment="面积(亩)")
    water_depth = Column(Float, nullable=False, comment="水深(米)")
    species = Column(String(100), comment="养殖品种")
    status = Column(String(20), default="active", comment="状态: active, inactive")
    # 有效版本控制：更正不删旧行，而是写入新版本并把旧行指向新行。
    version = Column(Integer, nullable=False, default=1, comment="版本号，从1开始")
    superseded_by_id = Column(Integer, ForeignKey("ponds.id"), nullable=True, comment="被哪个新版本替代")
    void_reason = Column(String(100), nullable=True, comment="版本失效原因: correction/reversal")
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
    # 签署时间：批次关闭即签署，签署后其事实只能用冲正恢复。
    signed_at = Column(DateTime, nullable=True, comment="批次签署(关闭)时间")
    closed_by = Column(String(100), nullable=True, comment="关闭操作人")
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

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    species = Column(String(100), nullable=False, comment="品种")
    quantity = Column(Integer, nullable=False, comment="数量(尾)")
    source = Column(String(200), comment="来源")
    batch_number = Column(String(50), comment="苗种批次号")
    weight_per_unit = Column(Float, comment="单重(克/尾)")
    total_weight = Column(Float, comment="总重量(公斤)")
    notes = Column(Text, comment="备注")
    version = Column(Integer, nullable=False, default=1)
    superseded_by_id = Column(Integer, ForeignKey("stocking_records.id"), nullable=True)
    void_reason = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("Batch", back_populates="stocking_records")


class FeedingRecord(Base):
    __tablename__ = "feeding_records"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    feeding_date = Column(Date, nullable=False, comment="投喂日期")
    feed_type = Column(String(100), nullable=False, comment="饲料类型")
    feed_quantity = Column(Float, nullable=False, comment="投喂量(公斤)")
    feeding_time = Column(String(20), comment="投喂时间")
    weather = Column(String(50), comment="天气情况")
    water_temperature = Column(Float, comment="水温(℃)")
    notes = Column(Text, comment="备注")
    version = Column(Integer, nullable=False, default=1)
    superseded_by_id = Column(Integer, ForeignKey("feeding_records.id"), nullable=True)
    void_reason = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("Batch", back_populates="feeding_records")


class WaterQualityRecord(Base):
    __tablename__ = "water_quality_records"

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
    version = Column(Integer, nullable=False, default=1)
    superseded_by_id = Column(Integer, ForeignKey("water_quality_records.id"), nullable=True)
    void_reason = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("Batch", back_populates="water_quality_records")


class MedicationRecord(Base):
    __tablename__ = "medication_records"

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
    version = Column(Integer, nullable=False, default=1)
    superseded_by_id = Column(Integer, ForeignKey("medication_records.id"), nullable=True)
    void_reason = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("Batch", back_populates="medication_records")


class CostRecord(Base):
    __tablename__ = "cost_records"

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
    version = Column(Integer, nullable=False, default=1)
    superseded_by_id = Column(Integer, ForeignKey("cost_records.id"), nullable=True)
    void_reason = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("Batch", back_populates="cost_records")


class HarvestSale(Base):
    __tablename__ = "harvest_sales"

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
    version = Column(Integer, nullable=False, default=1)
    superseded_by_id = Column(Integer, ForeignKey("harvest_sales.id"), nullable=True)
    void_reason = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("Batch", back_populates="harvest_sales")


class DataReview(Base):
    """异常值复核记录：按来源与影响形成，旧库异常值不直接删除。"""
    __tablename__ = "data_reviews"

    id = Column(Integer, primary_key=True, index=True)
    source = Column(String(30), nullable=False, comment="来源: startup_scan/manual_scan/manual")
    entity_type = Column(String(50), nullable=False, comment="实体类型(模型名)")
    entity_id = Column(Integer, nullable=False, comment="异常事实所在行(版本)")
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=True, comment="影响的批次")
    field_name = Column(String(50), nullable=False, comment="异常字段")
    raw_value = Column(String(100), nullable=False, comment="原始异常值(原样留痕)")
    rule = Column(String(30), nullable=False, comment="违反的约束规则")
    message = Column(String(200), nullable=False, comment="问题描述")
    entered_settlement = Column(Boolean, nullable=False, default=False, comment="发现时是否已进入结算(批次已签署)")
    status = Column(String(30), nullable=False, default="open",
                    comment="open/corrected/reversed/ignored")
    resolution_note = Column(Text, nullable=True)
    discovered_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)
    reviewed_by = Column(String(100), nullable=True)

    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", "field_name", name="uq_review_fact_field"),
    )


class Correction(Base):
    """更正单：仅未进入结算的异常可经批准后以新版本更正。"""
    __tablename__ = "corrections"

    id = Column(Integer, primary_key=True, index=True)
    review_id = Column(Integer, ForeignKey("data_reviews.id"), nullable=False)
    entity_type = Column(String(50), nullable=False)
    old_entity_id = Column(Integer, nullable=False, comment="被替代的旧版本行")
    new_entity_id = Column(Integer, nullable=True, comment="批准后产生的新版本行")
    idempotency_key = Column(String(100), unique=True, nullable=False)
    status = Column(String(20), nullable=False, default="proposed",
                    comment="proposed/approved/rejected/conflict")
    reason = Column(Text, nullable=True)
    payload_json = Column(Text, nullable=False, comment="拟更正的字段值JSON")
    proposed_by = Column(String(100), nullable=True)
    approved_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    applied_at = Column(DateTime, nullable=True)


class ReversalFact(Base):
    """冲正事实：已签署批次只能登记符号相反的冲正分录把净效果恢复为零。

    有效值 = 原始签署值(signed_value) + 冲正值(reverse_value)；
    冲正后净额 net_value 落库，详情/追溯/周期分析统一读取净额。
    """
    __tablename__ = "reversal_facts"

    id = Column(Integer, primary_key=True, index=True)
    review_id = Column(Integer, ForeignKey("data_reviews.id"), nullable=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=True)
    entity_type = Column(String(50), nullable=False)
    source_entity_id = Column(Integer, nullable=False, comment="被冲正的签署事实行")
    field_name = Column(String(50), nullable=False, comment="冲正字段")
    signed_value = Column(Float, nullable=False, comment="签署时的原始值")
    reverse_value = Column(Float, nullable=False, comment="冲正值(与原始值符号相反)")
    net_value = Column(Float, nullable=False, comment="冲正后净额")
    reason = Column(Text, nullable=True)
    idempotency_key = Column(String(100), unique=True, nullable=False)
    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("entity_type", "source_entity_id", "field_name",
                         name="uq_reversal_fact_field"),
    )


class AnalysisSnapshot(Base):
    """周期分析结果快照：按数据版本持久化，重启后可重放。"""
    __tablename__ = "analysis_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False, unique=True)
    data_version = Column(String(64), nullable=False, comment="所依据有效数据的版本指纹")
    payload_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
