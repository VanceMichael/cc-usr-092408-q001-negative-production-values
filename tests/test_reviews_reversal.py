"""旧库异常值：迁移保留、扫描复核、未结算更正、已签署冲正与并发防护。"""

import threading
import unittest
from datetime import date

from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models import (
    HarvestSale,
    RecordCorrection,
    StockingRecord,
)
from app.services.reviews import scan_legacy_anomalies
from tests.support import build_legacy_engine


class LegacyReviewTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self.db_path = str(Path(tempfile.mkdtemp()) / "legacy.db")
        build_legacy_engine(self.db_path)
        self._configure_app(self.db_path)

    def _configure_app(self, path):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.database import Base
        from app.migrations import ensure_schema

        self.engine = create_engine(
            f"sqlite:///{path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=self.engine)
        ensure_schema(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        app.dependency_overrides[get_db] = self._override
        self.c = TestClient(app)

    def _override(self):
        db = self.Session()
        try:
            yield db
        finally:
            db.close()

    def tearDown(self):
        app.dependency_overrides.pop(get_db, None)
        self.engine.dispose()

    def _scan(self):
        db = self.Session()
        try:
            counts = scan_legacy_anomalies(db)
        finally:
            db.close()
        return counts

    def test_scan_registers_reviews_without_deleting(self):
        counts = self._scan()
        self.assertGreaterEqual(counts["new_reviews"], 6)
        # 重复扫描幂等
        again = self._scan()
        self.assertEqual(again["new_reviews"], 0)

        summary = self.c.get("/api/data-reviews/summary/").json()
        self.assertEqual(summary["by_source"]["legacy_scan"], summary["total"])
        self.assertGreaterEqual(summary["by_entity"]["harvest"], 2)

        # 旧异常行原样保留
        import sqlite3
        con = sqlite3.connect(self.db_path)
        self.assertEqual(
            con.execute("SELECT count(*) FROM ponds WHERE area < 0").fetchone()[0], 1)
        self.assertEqual(
            con.execute("SELECT count(*) FROM stocking_records WHERE quantity < 0").fetchone()[0], 1)
        con.close()

        # impact 标注了波及指标
        reviews = self.c.get("/api/data-reviews/?entity=cost").json()
        self.assertIn("profit", reviews[0]["impact"])

    def test_unsigned_correction_flow(self):
        self._scan()
        review = [
            r for r in self.c.get("/api/data-reviews/?entity=stocking").json()
            if r["field"] == "quantity"
        ][0]
        rid = review["id"]

        # 未批准不能落账
        r = self.c.post(f"/api/data-reviews/{rid}/resolve/",
                        json={"requested_value": 5000})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "state_conflict")

        self.assertEqual(
            self.c.post(f"/api/data-reviews/{rid}/decision/",
                        json={"approve": True, "reviewed_by": "主管"}).status_code,
            200)

        # 更正为负数仍被约束拒绝
        r = self.c.post(f"/api/data-reviews/{rid}/resolve/",
                        json={"requested_value": -1})
        self.assertEqual(r.status_code, 422)

        r = self.c.post(f"/api/data-reviews/{rid}/resolve/",
                        json={"requested_value": 5000, "idempotency_key": "fix-1"})
        self.assertEqual(r.status_code, 200, r.text)
        corr = r.json()
        self.assertEqual(corr["kind"], "correction")
        self.assertEqual(corr["after_value"], 5000.0)

        # 重复处置拒绝
        r = self.c.post(f"/api/data-reviews/{rid}/resolve/",
                        json={"requested_value": 6000})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "duplicate_reversal")

        # 详情与追溯呈现更正后的有效值
        self.assertEqual(
            self.c.get(f"/api/stocking-records/{review['record_id']}/").json()["quantity"],
            5000)
        trace = self.c.get("/api/analysis/traceability/1/").json()
        self.assertEqual(trace["stocking_records"][0]["quantity"], 5000)

        # 底表已被事实更正（未签署）
        db = self.Session()
        self.assertEqual(db.get(StockingRecord, review["record_id"]).quantity, 5000)
        db.close()

    def test_signed_batch_only_reversal(self):
        self._scan()
        sale_review = [
            r for r in self.c.get("/api/data-reviews/?entity=harvest").json()
            if r["field"] == "weight"
        ][0]
        rid = sale_review["id"]

        # 签署批次
        signed = self.c.post("/api/batches/1/sign/", json={}).json()
        self.assertEqual(signed["status"], "closed")
        self.assertGreaterEqual(signed["freeze_version"], 1)

        # 直接改事实被冻结拦截
        r = self.c.put(f"/api/harvest-sales/{sale_review['record_id']}/",
                       json={"weight": 9})
        self.assertEqual(r.status_code, 409)

        self.assertEqual(
            self.c.post(f"/api/data-reviews/{rid}/decision/",
                        json={"approve": True}).status_code, 200)

        # 传什么目标值都被忽略，冲正为 0，且联动 total_amount
        r = self.c.post(f"/api/data-reviews/{rid}/resolve/",
                        json={"requested_value": 999, "idempotency_key": "rev-1"})
        self.assertEqual(r.status_code, 200, r.text)
        corr = r.json()
        self.assertEqual(corr["kind"], "reversal")
        self.assertEqual(corr["after_value"], 0.0)
        self.assertTrue(corr["batch_signed"])

        # 底表事实不动
        db = self.Session()
        self.assertEqual(db.get(HarvestSale, sale_review["record_id"]).weight, -400.0)
        db.close()

        # 有效值归零（详情/追溯一致）
        self.assertEqual(
            self.c.get(f"/api/harvest-sales/{sale_review['record_id']}/").json()["weight"],
            0.0)
        trace = self.c.get("/api/analysis/traceability/1/").json()["harvest_sales"][0]
        self.assertEqual(trace["weight"], 0.0)
        self.assertEqual(trace["total_amount"], 0.0)

        # 同键重放不产生第二笔
        r = self.c.post(f"/api/data-reviews/{rid}/resolve/",
                        json={"requested_value": 999, "idempotency_key": "rev-1"})
        self.assertEqual(r.status_code, 409)
        db = self.Session()
        applied = db.query(RecordCorrection).filter_by(
            review_id=rid, status="applied").count()
        # 整笔销售冲正：主字段 weight + 联动 total_amount、unit_price
        self.assertEqual(applied, 3)
        # 级联字段自身的复核被一并结案
        open_for_sale = [
            r for r in self.c.get("/api/data-reviews/?entity=harvest").json()
            if r["record_id"] == sale_review["record_id"]
        ]
        self.assertTrue(all(r["status"] == "reversed" for r in open_for_sale))
        db.close()

    def test_reject_review(self):
        self._scan()
        rid = self.c.get("/api/data-reviews/?status=open").json()[0]["id"]
        r = self.c.post(f"/api/data-reviews/{rid}/decision/",
                        json={"approve": False, "note": "误报"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "rejected")
        # 驳回后不能再决策
        r = self.c.post(f"/api/data-reviews/{rid}/decision/",
                        json={"approve": True})
        self.assertEqual(r.status_code, 409)


class ConcurrentResolutionTest(unittest.TestCase):
    """并发：同一复核两笔冲正只有一笔成功；更正与签署竞争安全。"""

    def setUp(self):
        import tempfile
        from pathlib import Path
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.database import Base
        from app.migrations import ensure_schema

        path = str(Path(tempfile.mkdtemp()) / "c.db")
        self.engine = create_engine(
            f"sqlite:///{path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=self.engine)
        ensure_schema(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        app.dependency_overrides[get_db] = self._override
        self.c = TestClient(app)

        self.c.post("/api/ponds/", json={"name": "塘", "area": 10, "water_depth": 2})
        self.c.post("/api/batches/", json={
            "batch_number": "B", "pond_id": 1, "species": "鱼",
            "stocking_date": "2026-03-01"})
        db = self.Session()
        db.add(HarvestSale(batch_id=1, sale_date=date(2026, 8, 1),
                           weight=100, unit_price=10, total_amount=1000))
        db.commit()
        db.close()
        self.c.post("/api/batches/1/sign/", json={})
        r = self.c.post("/api/data-reviews/", json={
            "entity": "harvest", "record_id": 1, "field": "weight",
            "rule_code": "manual_recheck", "entered_settlement": True})
        self.rid = r.json()["id"]
        self.c.post(f"/api/data-reviews/{self.rid}/decision/", json={"approve": True})

    def _override(self):
        db = self.Session()
        try:
            yield db
        finally:
            db.close()

    def tearDown(self):
        app.dependency_overrides.pop(get_db, None)
        self.engine.dispose()

    def test_duplicate_concurrent_reversal(self):
        results = []
        barrier = threading.Barrier(2)

        def resolve(key):
            client = TestClient(app)
            barrier.wait()
            r = client.post(f"/api/data-reviews/{self.rid}/resolve/",
                            json={"idempotency_key": key})
            results.append(r.status_code)

        threads = [
            threading.Thread(target=resolve, args=("k1",)),
            threading.Thread(target=resolve, args=("k2",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sorted(results), [200, 409])
        db = self.Session()
        # 主字段只允许一笔 applied（联动的 total_amount 是另一字段，共 2 行）
        applied = db.query(RecordCorrection).filter_by(
            review_id=self.rid, field="weight", status="applied").count()
        self.assertEqual(applied, 1)
        db.close()


if __name__ == "__main__":
    unittest.main()
