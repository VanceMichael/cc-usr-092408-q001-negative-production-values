"""有效版本读取层。

详情、追溯与周期分析共享本模块给出的唯一“有效版本”，避免三处各自直查
原始表而读到已被替代/冲正的数据：

* 普通事实：``superseded_by_id IS NULL`` 的最新版本行有效，更正产生的旧
  版本不参与读取；
* 已签署（关闭）批次：原签署行保留留痕，其被冲正字段的有效值取
  :class:`ReversalFact` 冲正后的净额（原值 + 冲正值）。
"""

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from .models import (
    Batch,
    CostRecord,
    FeedingRecord,
    HarvestSale,
    MedicationRecord,
    Pond,
    ReversalFact,
    StockingRecord,
    WaterQualityRecord,
)

# 受版本控制的实体注册表：模型名 -> 模型类
ENTITY_MODELS: Dict[str, type] = {
    "Pond": Pond,
    "StockingRecord": StockingRecord,
    "FeedingRecord": FeedingRecord,
    "WaterQualityRecord": WaterQualityRecord,
    "MedicationRecord": MedicationRecord,
    "CostRecord": CostRecord,
    "HarvestSale": HarvestSale,
}

# 归属于批次的事实实体（Pond 除外）
BATCH_ENTITY_MODELS: Dict[str, type] = {
    name: model for name, model in ENTITY_MODELS.items() if name != "Pond"
}


def current_query(db: Session, model: type):
    """只含最新版本行（未被更正替代）的查询。"""
    return db.query(model).filter(model.superseded_by_id.is_(None))


def reversal_overrides(
    db: Session, batch_id: Optional[int] = None
) -> Dict[Tuple[str, int, str], float]:
    """返回冲正后净额映射：(实体类型, 原签署行ID, 字段) -> 净额。"""
    query = db.query(ReversalFact)
    if batch_id is not None:
        query = query.filter(ReversalFact.batch_id == batch_id)
    facts = query.all()
    grouped: Dict[Tuple[str, int, str], Dict[str, float]] = {}
    for fact in facts:
        key = (fact.entity_type, fact.source_entity_id, fact.field_name)
        item = grouped.setdefault(
            key, {"signed": fact.signed_value, "reverse": 0.0}
        )
        item["reverse"] += fact.reverse_value
    return {key: item["signed"] + item["reverse"] for key, item in grouped.items()}


def effective_value(row: Any, field: str, overrides: Dict[Tuple[str, int, str], float]):
    key = (type(row).__name__, row.id, field)
    if key in overrides:
        return overrides[key]
    return getattr(row, field)


def to_effective_dict(
    row: Any,
    overrides: Optional[Dict[Tuple[str, int, str], float]] = None,
) -> Dict[str, Any]:
    """把版本行序列化为响应字典，并套用冲正净额。"""
    data = {column.name: getattr(row, column.name) for column in row.__table__.columns}
    if overrides:
        for (entity_type, entity_id, field_name), net in overrides.items():
            if entity_type == type(row).__name__ and entity_id == row.id:
                data[field_name] = net
    return data


class EffectiveData:
    """单个批次的有效数据视图：详情/追溯/周期分析共用。"""

    def __init__(self, db: Session, batch: Batch):
        self.db = db
        self.batch = batch
        self.overrides = reversal_overrides(db, batch_id=batch.id)

    def rows(self, model: type) -> List[Any]:
        return (
            current_query(self.db, model)
            .filter(model.batch_id == self.batch.id)
            .order_by(model.id)
            .all()
        )

    def value(self, row: Any, field: str):
        return effective_value(row, field, self.overrides)

    def dicts(self, model: type) -> List[Dict[str, Any]]:
        return [to_effective_dict(row, self.overrides) for row in self.rows(model)]

    def sum(self, model: type, field: str) -> float:
        total = 0.0
        for row in self.rows(model):
            total += self.value(row, field) or 0
        return total

    def pond(self) -> Optional[Pond]:
        return current_query(self.db, Pond).filter(Pond.id == self.batch.pond_id).first()

    def fingerprint(self) -> str:
        """当前有效数据的版本指纹；任一行的版本/冲正变化都会改变。"""
        material: Dict[str, Any] = {"batch_id": self.batch.id, "entities": {}}

        pond = self.pond()
        material["pond"] = None if pond is None else [pond.id, pond.version]

        for name, model in BATCH_ENTITY_MODELS.items():
            rows = self.rows(model)
            material["entities"][name] = [
                [row.id, row.version, row.superseded_by_id, row.void_reason]
                for row in rows
            ]
        material["reversals"] = sorted(
            [key + (net,) for key, net in self.overrides.items()],
            key=lambda item: (item[0], item[1], item[2]),
        )
        payload = json.dumps(material, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
