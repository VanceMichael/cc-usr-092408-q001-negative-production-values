"""测试辅助：独立临时库、TestClient 依赖覆盖、旧库（无约束）构造。"""

import sys
import tempfile
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import (
    Column,
    Date,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.database import Base, get_db
from app.main import app
from app.migrations import ensure_schema
from app import models  # noqa: F401  确保全部表已注册到 metadata


def fresh_engine():
    tmp = Path(tempfile.mkdtemp()) / "test.db"
    engine = create_engine(
        f"sqlite:///{tmp}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    ensure_schema(engine)
    return engine, tmp


class ClientDB:
    """每个用例一个全新文件库；不走 lifespan（扫描由用例自行触发）。"""

    def __init__(self):
        self.engine, self.path = fresh_engine()
        self.Session = sessionmaker(bind=self.engine)
        app.dependency_overrides[get_db] = self._override
        self.client = TestClient(app)

    def _override(self):
        db = self.Session()
        try:
            yield db
        finally:
            db.close()

    def session(self):
        return self.Session()

    def reopen(self):
        """模拟服务重启：在同一数据库文件上重建引擎/会话/客户端。"""
        self.engine.dispose()
        app.dependency_overrides.pop(get_db, None)
        self.engine = create_engine(
            f"sqlite:///{self.path}",
            connect_args={"check_same_thread": False},
        )
        self.Session = sessionmaker(bind=self.engine)
        app.dependency_overrides[get_db] = self._override
        self.client = TestClient(app)
        return self.client

    def close(self):
        app.dependency_overrides.pop(get_db, None)
        self.engine.dispose()


# ---------------------------------------------------------------------------
# 旧库：按基线版本（仓库 HEAD 中的 models.py）建表，无 CHECK、无签署列、无复核
# 表，历史异常值可直接写入，真实模拟“月末结算前的旧库”。
# ---------------------------------------------------------------------------
def build_legacy_engine(path: str):
    engine = create_engine(f"sqlite:///{path}")
    legacy_base = declarative_base()

    baseline_src = (
        ROOT / "tests" / "fixtures" / "baseline_models.py"
    ).read_text(encoding="utf-8")
    baseline_src = baseline_src.replace("from .database import Base", "", 1)
    ns = {"Base": legacy_base}
    ns.update({
        "Column": Column, "Integer": Integer, "String": String, "Float": Float,
        "Date": Date, "ForeignKey": ForeignKey, "Text": Text,
        "relationship": relationship,
        # 基线 models.py 还用到了 DateTime
    })
    from sqlalchemy import DateTime
    ns["DateTime"] = DateTime
    exec(compile(baseline_src, "baseline_models.py", "exec"), ns)

    legacy_base.metadata.create_all(engine)
    LPond = ns["Pond"]
    LBatch = ns["Batch"]
    LStocking = ns["StockingRecord"]
    LFeeding = ns["FeedingRecord"]
    LCost = ns["CostRecord"]
    LHarvest = ns["HarvestSale"]

    db = sessionmaker(bind=engine)()
    pond = LPond(name="旧塘", area=-10, water_depth=2, species="草鱼")
    db.add(pond)
    db.commit()
    batch = LBatch(batch_number="OLD-1", pond_id=pond.id, species="草鱼",
                   stocking_date=date(2026, 3, 1))
    db.add(batch)
    db.commit()
    db.add_all([
        LStocking(batch_id=batch.id, species="草鱼", quantity=-500, total_weight=-25),
        LFeeding(batch_id=batch.id, feeding_date=date(2026, 3, 2),
                 feed_type="颗粒料", feed_quantity=-20),
        LCost(batch_id=batch.id, cost_date=date(2026, 3, 3),
              cost_type="feed", amount=-300),
        LHarvest(batch_id=batch.id, sale_date=date(2026, 8, 1),
                 weight=-400, unit_price=-12, total_amount=4800),
    ])
    db.commit()
    db.close()
    return engine
