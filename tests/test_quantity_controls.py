"""生产数据数量约束、复核工作流、有效版本与重启重放的集成测试。"""

import os
import sys
import tempfile
import threading
import unittest
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend"))

# 必须在导入 app 之前固定测试数据库
_TMP_DB = tempfile.NamedTemporaryFile(prefix="q001_test_", suffix=".db", delete=False)
_TMP_DB.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB.name}"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, inspect, text  # noqa: E402

from app.database import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Batch,
    Correction,
    DataReview,
    HarvestSale,
    Pond,
    ReversalFact,
    StockingRecord,
)
from app.review_workflow import register_reversal  # noqa: E402


class ApiTestBase(unittest.TestCase):
    def setUp(self):
        engine.dispose()
        p = Path(_TMP_DB.name)
        if p.exists():
            p.unlink()
        # 进入上下文触发 lifespan：建表/迁移 + 启动扫描
        self.client_cm = TestClient(app)
        self.client = self.client_cm.__enter__()

    def tearDown(self):
        self.client_cm.__exit__(None, None, None)
        engine.dispose()
        p = Path(_TMP_DB.name)
        if p.exists():
            p.unlink()

    def create_pond_batch(self, name="塘", area=10.0, batch_number="B1"):
        r = self.client.post("/api/ponds/", json={"name": name, "area": area, "water_depth": 1.5})
        self.assertEqual(r.status_code, 200, r.text)
        pond_id = r.json()["id"]
        r = self.client.post(
            "/api/batches/",
            json={"batch_number": batch_number, "pond_id": pond_id, "species": "虾",
                  "stocking_date": "2026-09-01"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        return pond_id, r.json()["id"]


class QuantityRuleTableTest(unittest.TestCase):
    def test_rule_categories(self):
        from app.validation import FIELD_RULES, NON_NEGATIVE, POSITIVE, POSITIVE_INT

        # 必须大于零
        self.assertEqual(FIELD_RULES["Pond"]["area"], POSITIVE)
        self.assertEqual(FIELD_RULES["Pond"]["water_depth"], POSITIVE)
        self.assertEqual(FIELD_RULES["StockingRecord"]["quantity"], POSITIVE_INT)
        self.assertEqual(FIELD_RULES["FeedingRecord"]["feed_quantity"], POSITIVE)
        self.assertEqual(FIELD_RULES["MedicationRecord"]["dosage"], POSITIVE)
        self.assertEqual(FIELD_RULES["CostRecord"]["amount"], POSITIVE)
        self.assertEqual(FIELD_RULES["HarvestSale"]["weight"], POSITIVE)
        self.assertEqual(FIELD_RULES["HarvestSale"]["unit_price"], POSITIVE)
        # 允许为零的测量值/可选值
        for f in ("water_temperature", "ph_value", "dissolved_oxygen", "ammonia_nitrogen",
                  "nitrite", "transparency"):
            self.assertEqual(FIELD_RULES["WaterQualityRecord"][f], NON_NEGATIVE)
        self.assertEqual(FIELD_RULES["StockingRecord"]["total_weight"], NON_NEGATIVE)
        self.assertEqual(FIELD_RULES["CostRecord"]["quantity"], NON_NEGATIVE)
        self.assertEqual(FIELD_RULES["HarvestSale"]["total_amount"], NON_NEGATIVE)

    def test_nan_infinity_rejected(self):
        from app.validation import validate_fields
        errs = validate_fields("Pond", {"area": float("nan"), "water_depth": float("inf")})
        self.assertEqual({e["field"] for e in errs}, {"area", "water_depth"})


class CreateValidationTest(ApiTestBase):
    def setUp(self):
        super().setUp()
        self.pond_id, self.batch_id = self.create_pond_batch()

    def test_pond_negative_and_zero(self):
        for area in (-10, 0):
            r = self.client.post("/api/ponds/", json={"name": f"塘{area}", "area": area, "water_depth": 1.5})
            self.assertEqual(r.status_code, 422)
            body = r.json()
            self.assertEqual(body["code"], "quantity_constraint")
            self.assertEqual(body["errors"][0]["field"], "area")
            self.assertIn("rule", body["errors"][0])

    def test_stocking_negative_and_zero(self):
        r = self.client.post("/api/stocking-records/",
                             json={"batch_id": self.batch_id, "species": "虾", "quantity": -500})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["errors"][0]["field"], "quantity")
        r = self.client.post("/api/stocking-records/",
                             json={"batch_id": self.batch_id, "species": "虾", "quantity": 0})
        self.assertEqual(r.status_code, 422)
        # 数量整数约束
        r = self.client.post("/api/stocking-records/",
                             json={"batch_id": self.batch_id, "species": "虾", "quantity": 1.5})
        self.assertEqual(r.status_code, 422)

    def test_feeding_negative(self):
        r = self.client.post("/api/feeding-records/",
                             json={"batch_id": self.batch_id, "feeding_date": "2026-09-02",
                                   "feed_type": "料", "feed_quantity": -20})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["errors"][0]["field"], "feed_quantity")

    def test_water_quality_zero_allowed_negative_rejected(self):
        ok = self.client.post("/api/water-quality-records/",
                              json={"batch_id": self.batch_id, "record_date": "2026-09-02",
                                    "ph_value": 0, "dissolved_oxygen": 0})
        self.assertEqual(ok.status_code, 200, ok.text)
        bad = self.client.post("/api/water-quality-records/",
                               json={"batch_id": self.batch_id, "record_date": "2026-09-02",
                                     "ph_value": -0.1})
        self.assertEqual(bad.status_code, 422)

    def test_medication_dosage(self):
        self.assertEqual(
            self.client.post("/api/medication-records/",
                             json={"batch_id": self.batch_id, "medication_date": "2026-09-02",
                                   "drug_name": "药", "dosage": -1}).status_code,
            422)
        # 剂量可选缺省，但给出时必须为正
        self.assertEqual(
            self.client.post("/api/medication-records/",
                             json={"batch_id": self.batch_id, "medication_date": "2026-09-02",
                                   "drug_name": "药", "dosage": 0}).status_code,
            422)
        self.assertEqual(
            self.client.post("/api/medication-records/",
                             json={"batch_id": self.batch_id, "medication_date": "2026-09-02",
                                   "drug_name": "药"}).status_code,
            200)

    def test_cost_and_sale(self):
        self.assertEqual(
            self.client.post("/api/cost-records/",
                             json={"batch_id": self.batch_id, "cost_date": "2026-09-02",
                                   "cost_type": "feed", "amount": -100}).status_code,
            422)
        ok = self.client.post("/api/cost-records/",
                              json={"batch_id": self.batch_id, "cost_date": "2026-09-02",
                                    "cost_type": "feed", "amount": 100, "quantity": 0})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(
            self.client.post("/api/harvest-sales/",
                             json={"batch_id": self.batch_id, "sale_date": "2026-09-20",
                                   "weight": 0, "unit_price": 40}).status_code,
            422)
        sale = self.client.post("/api/harvest-sales/",
                                json={"batch_id": self.batch_id, "sale_date": "2026-09-20",
                                      "weight": 50, "unit_price": 40})
        self.assertEqual(sale.status_code, 200)
        self.assertEqual(sale.json()["total_amount"], 2000.0)

    def test_type_error_uses_same_envelope(self):
        r = self.client.post("/api/stocking-records/",
                             json={"batch_id": self.batch_id, "species": "虾", "quantity": "abc"})
        self.assertEqual(r.status_code, 422)
        body = r.json()
        self.assertEqual(body["code"], "request_validation")
        self.assertTrue(body["errors"][0]["field"].endswith("quantity"))


class UpdateValidationTest(ApiTestBase):
    def test_update_negative_rejected_then_ok(self):
        pond_id, batch_id = self.create_pond_batch("另塘", batch_number="B2")
        r = self.client.post("/api/feeding-records/",
                             json={"batch_id": batch_id, "feeding_date": "2026-09-02",
                                   "feed_type": "料", "feed_quantity": 10})
        rid = r.json()["id"]
        bad = self.client.put(f"/api/feeding-records/{rid}/", json={"feed_quantity": -3})
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(bad.json()["errors"][0]["field"], "feed_quantity")
        ok = self.client.put(f"/api/feeding-records/{rid}/", json={"feed_quantity": 12})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["feed_quantity"], 12.0)

    def test_update_pond_to_negative_rejected(self):
        pond_id, _ = self.create_pond_batch("面积塘", batch_number="UP2")
        r = self.client.put(f"/api/ponds/{pond_id}/", json={"area": -1})
        self.assertEqual(r.status_code, 422)

    def test_required_field_cannot_be_nulled_on_update(self):
        _, batch_id = self.create_pond_batch("置空塘", batch_number="UP3")
        r = self.client.post("/api/feeding-records/",
                             json={"batch_id": batch_id, "feeding_date": "2026-09-02",
                                   "feed_type": "料", "feed_quantity": 10})
        rid = r.json()["id"]
        bad = self.client.put(f"/api/feeding-records/{rid}/", json={"feed_quantity": None})
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(bad.json()["errors"][0]["field"], "feed_quantity")
        # 可选字段可以清空
        med = self.client.post("/api/medication-records/",
                               json={"batch_id": batch_id, "medication_date": "2026-09-02",
                                     "drug_name": "药", "dosage": 2})
        self.assertEqual(
            self.client.put(f"/api/medication-records/{med.json()['id']}/",
                            json={"dosage": None}).status_code,
            200)


class BatchImportTest(ApiTestBase):
    def test_any_bad_row_aborts_whole_batch(self):
        _, batch_id = self.create_pond_batch("导入塘", batch_number="BI")
        before = self.client.get("/api/feeding-records/", params={"batch_id": batch_id}).json()
        r = self.client.post("/api/feeding-records/import/", json=[
            {"batch_id": batch_id, "feeding_date": "2026-09-03", "feed_type": "料", "feed_quantity": 10},
            {"batch_id": batch_id, "feeding_date": "2026-09-04", "feed_type": "料", "feed_quantity": -5},
        ])
        self.assertEqual(r.status_code, 422)
        body = r.json()
        self.assertEqual(body["code"], "batch_import_invalid")
        self.assertEqual(body["errors"][0]["index"], 1)
        self.assertEqual(body["errors"][0]["errors"][0]["field"], "feed_quantity")
        after = self.client.get("/api/feeding-records/", params={"batch_id": batch_id}).json()
        self.assertEqual(len(after), len(before))

    def test_all_valid_imports(self):
        _, batch_id = self.create_pond_batch("导入塘2", batch_number="BI2")
        r = self.client.post("/api/cost-records/import/", json=[
            {"batch_id": batch_id, "cost_date": "2026-09-03", "cost_type": "feed", "amount": 10},
            {"batch_id": batch_id, "cost_date": "2026-09-04", "cost_type": "labor", "amount": 20},
        ])
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["created_count"], 2)

    def test_import_into_closed_batch_rejected(self):
        _, batch_id = self.create_pond_batch("导入塘3", batch_number="BI3")
        self.client.post(f"/api/batches/{batch_id}/close/", json={})
        r = self.client.post("/api/feeding-records/import/", json=[
            {"batch_id": batch_id, "feeding_date": "2026-09-03", "feed_type": "料", "feed_quantity": 10},
        ])
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["detail"]["code"], "batch_signed")


class SignedBatchFreezeTest(ApiTestBase):
    def test_closed_batch_only_allows_reversal(self):
        _, batch_id = self.create_pond_batch("冻结塘", batch_number="FR")
        r = self.client.post("/api/feeding-records/",
                             json={"batch_id": batch_id, "feeding_date": "2026-09-02",
                                   "feed_type": "料", "feed_quantity": 10})
        feed_id = r.json()["id"]
        self.client.post(f"/api/batches/{batch_id}/close/", json={"closed_by": "甲"})

        self.assertEqual(
            self.client.post("/api/feeding-records/",
                             json={"batch_id": batch_id, "feeding_date": "2026-09-05",
                                   "feed_type": "料", "feed_quantity": 1}).status_code,
            409)
        self.assertEqual(
            self.client.put(f"/api/feeding-records/{feed_id}/", json={"feed_quantity": 9}).status_code,
            409)
        self.assertEqual(self.client.delete(f"/api/feeding-records/{feed_id}/").status_code, 409)
        # 重复关闭
        self.assertEqual(self.client.post(f"/api/batches/{batch_id}/close/", json={}).status_code, 409)


class ReviewWorkflowTest(ApiTestBase):
    def _seed_anomaly(self, signed=False):
        """直接构造一条异常投苗（-500尾），并按需先签署批次。"""
        _, batch_id = self.create_pond_batch("复核塘", batch_number="RV")
        if signed:
            self.client.post(f"/api/batches/{batch_id}/close/", json={})
        db = SessionLocal()
        try:
            row = StockingRecord(batch_id=batch_id, species="虾", quantity=-500)
            db.add(row)
            db.commit()
            db.refresh(row)
            return batch_id, row.id
        finally:
            db.close()

    def test_scan_creates_review_with_source_and_impact(self):
        batch_id, row_id = self._seed_anomaly()
        r = self.client.post("/api/reviews/scan/", json={"source": "manual_scan"})
        self.assertEqual(r.status_code, 200, r.text)
        reviews = self.client.get("/api/reviews/", params={"batch_id": batch_id}).json()
        self.assertEqual(len(reviews), 1)
        rev = reviews[0]
        self.assertEqual(rev["entity_type"], "StockingRecord")
        self.assertEqual(rev["entity_id"], row_id)
        self.assertEqual(rev["field_name"], "quantity")
        self.assertEqual(rev["raw_value"], "-500")
        self.assertFalse(rev["entered_settlement"])
        self.assertEqual(rev["status"], "open")
        # 再扫一次幂等，不产生新记录
        self.client.post("/api/reviews/scan/", json={})
        reviews2 = self.client.get("/api/reviews/", params={"batch_id": batch_id}).json()
        self.assertEqual(len(reviews2), 1)

    def test_correction_propose_invalid_payload_rejected(self):
        batch_id, _ = self._seed_anomaly()
        self.client.post("/api/reviews/scan/", json={})
        rev = self.client.get("/api/reviews/", params={"batch_id": batch_id}).json()[0]
        r = self.client.post(f"/api/reviews/{rev['id']}/corrections/",
                             json={"payload": {"quantity": -1}, "idempotency_key": "k1"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["detail"]["code"], "invalid_correction")
        r = self.client.post(f"/api/reviews/{rev['id']}/corrections/",
                             json={"payload": {"bogus": 1}, "idempotency_key": "k2"})
        self.assertEqual(r.status_code, 409)

    def test_approve_correction_versions_and_shares_effective_view(self):
        batch_id, old_id = self._seed_anomaly()
        self.client.post("/api/reviews/scan/", json={})
        rev = self.client.get("/api/reviews/", params={"batch_id": batch_id}).json()[0]
        r = self.client.post(f"/api/reviews/{rev['id']}/corrections/",
                             json={"payload": {"quantity": 5000}, "idempotency_key": "corr-1"})
        self.assertEqual(r.status_code, 201, r.text)
        cid = r.json()["id"]
        r = self.client.post(f"/api/corrections/{cid}/approve/", json={"approved_by": "主管"})
        self.assertEqual(r.status_code, 200, r.text)
        approved = r.json()
        self.assertEqual(approved["status"], "approved")
        new_id = approved["new_entity_id"]
        self.assertNotEqual(new_id, old_id)

        # 列表/详情只返回新版本，旧行保留留痕
        listing = self.client.get("/api/stocking-records/", params={"batch_id": batch_id}).json()
        self.assertEqual(len(listing), 1)
        self.assertEqual(listing[0]["id"], new_id)
        self.assertEqual(listing[0]["quantity"], 5000)
        self.assertEqual(listing[0]["version"], 2)
        detail = self.client.get(f"/api/stocking-records/{old_id}/").json()
        self.assertEqual(detail["id"], new_id)  # 旧ID沿版本链解析到新版本

        db = SessionLocal()
        try:
            old = db.query(StockingRecord).filter(StockingRecord.id == old_id).first()
            self.assertIsNotNone(old)  # 旧行未删除
            self.assertEqual(old.superseded_by_id, new_id)
            self.assertEqual(old.void_reason, "correction")
        finally:
            db.close()

        # 复核单状态闭环
        rev_after = self.client.get(f"/api/reviews/{rev['id']}/").json()
        self.assertEqual(rev_after["status"], "corrected")
        # 周期分析与追溯共享同一有效版本
        cyc = self.client.get(f"/api/analysis/cycle/{batch_id}/").json()
        tr = self.client.get(f"/api/analysis/traceability/{batch_id}/").json()
        self.assertEqual(cyc["initial_quantity"], 5000)
        self.assertEqual(tr["stocking_records"][0]["quantity"], 5000)
        self.assertEqual(tr["data_version"], cyc["data_version"])

    def test_double_approve_is_idempotent(self):
        batch_id, _ = self._seed_anomaly()
        self.client.post("/api/reviews/scan/", json={})
        rev = self.client.get("/api/reviews/", params={"batch_id": batch_id}).json()[0]
        self.client.post(f"/api/reviews/{rev['id']}/corrections/",
                         json={"payload": {"quantity": 5000}, "idempotency_key": "k"})
        cid = self.client.get("/api/corrections/").json()[0]["id"]
        a = self.client.post(f"/api/corrections/{cid}/approve/", json={})
        b = self.client.post(f"/api/corrections/{cid}/approve/", json={})
        self.assertEqual(a.json()["new_entity_id"], b.json()["new_entity_id"])
        db = SessionLocal()
        try:
            self.assertEqual(db.query(StockingRecord).count(), 2)  # 旧+新，只有一个新版本
        finally:
            db.close()

    def test_pond_anomaly_corrected_in_place_keeps_batches_linked(self):
        """塘口异常更正为原地升版本：批次外键不悬空，亩产使用更正后面积。"""
        # 直接造一个负面积塘口 + 批次 + 销售
        db = SessionLocal()
        try:
            pond = Pond(name="负亩原地塘", area=-10, water_depth=1.5)
            db.add(pond); db.commit(); db.refresh(pond)
            batch = Batch(batch_number="PV", pond_id=pond.id, species="虾",
                          stocking_date=date(2026, 9, 1))
            db.add(batch); db.commit()
            batch_id, pond_id = batch.id, pond.id
        finally:
            db.close()
        self.client.post("/api/reviews/scan/", json={})
        reviews = self.client.get("/api/reviews/", params={"entity_type": "Pond"}).json()
        rev = [r for r in reviews if r["entity_id"] == pond_id][0]
        r = self.client.post(f"/api/reviews/{rev['id']}/corrections/",
                             json={"payload": {"area": 25}, "idempotency_key": "pond-corr"})
        cid = r.json()["id"]
        r = self.client.post(f"/api/corrections/{cid}/approve/", json={})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["new_entity_id"], pond_id)  # 原地，不换ID
        detail = self.client.get(f"/api/ponds/{pond_id}/").json()
        self.assertEqual(detail["area"], 25.0)
        self.assertEqual(detail["version"], 2)
        # 批次仍挂在同一塘口
        batch_resp = self.client.get(f"/api/batches/{batch_id}/").json()
        self.assertEqual(batch_resp["pond_id"], pond_id)
        # 加销售后分析使用更正面积算亩产
        self.client.post("/api/harvest-sales/",
                         json={"batch_id": batch_id, "sale_date": "2026-09-20",
                               "weight": 500, "unit_price": 20})
        cyc = self.client.get(f"/api/analysis/cycle/{batch_id}/?refresh=true").json()
        self.assertEqual(cyc["area"], 25.0)
        self.assertEqual(cyc["yield_per_mu"], 20.0)

    def test_signed_batch_anomaly_must_reverse(self):
        batch_id, row_id = self._seed_anomaly(signed=True)
        self.client.post("/api/reviews/scan/", json={})
        rev = self.client.get("/api/reviews/", params={"batch_id": batch_id}).json()[0]
        self.assertTrue(rev["entered_settlement"])
        # 更正被拒绝
        r = self.client.post(f"/api/reviews/{rev['id']}/corrections/",
                             json={"payload": {"quantity": 100}, "idempotency_key": "k"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["detail"]["code"], "already_settled")
        # 冲正恢复到零
        r = self.client.post("/api/reversals/",
                             json={"review_id": rev["id"], "idempotency_key": "rev-1", "reason": "恢复"})
        self.assertEqual(r.status_code, 201, r.text)
        fact = r.json()
        self.assertEqual(fact["signed_value"], -500.0)
        self.assertEqual(fact["reverse_value"], 500.0)
        self.assertEqual(fact["net_value"], 0.0)
        # 分析与追溯读净额，负数不再穿透
        cyc = self.client.get(f"/api/analysis/cycle/{batch_id}/?refresh=true").json()
        self.assertEqual(cyc["initial_quantity"], 0)
        tr = self.client.get(f"/api/analysis/traceability/{batch_id}/").json()
        self.assertEqual(tr["stocking_records"][0]["quantity"], 0)
        self.assertEqual(tr["data_version"], cyc["data_version"])
        # 同键重试幂等、异键拒绝
        again = self.client.post("/api/reversals/",
                                 json={"review_id": rev["id"], "idempotency_key": "rev-1"})
        self.assertEqual(again.json()["id"], fact["id"])
        dup = self.client.post("/api/reversals/",
                               json={"review_id": rev["id"], "idempotency_key": "rev-2"})
        self.assertEqual(dup.status_code, 409)
        self.assertEqual(dup.json()["detail"]["code"], "reversal_exists")
        # 复核状态闭环
        self.assertEqual(self.client.get(f"/api/reviews/{rev['id']}/").json()["status"], "reversed")

    def test_reversal_on_open_batch_rejected(self):
        batch_id, _ = self._seed_anomaly(signed=False)
        self.client.post("/api/reviews/scan/", json={})
        rev = self.client.get("/api/reviews/", params={"batch_id": batch_id}).json()[0]
        r = self.client.post("/api/reversals/",
                             json={"review_id": rev["id"], "idempotency_key": "rev-x"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["detail"]["code"], "batch_not_signed")

    def test_close_racing_correction_blocks_approval(self):
        """拟单后批次被关闭：批准必须转为冲突，不能再写更正版本。"""
        batch_id, _ = self._seed_anomaly()
        self.client.post("/api/reviews/scan/", json={})
        rev = self.client.get("/api/reviews/", params={"batch_id": batch_id}).json()[0]
        self.client.post(f"/api/reviews/{rev['id']}/corrections/",
                         json={"payload": {"quantity": 100}, "idempotency_key": "race"})
        cid = self.client.get("/api/corrections/").json()[0]["id"]
        self.client.post(f"/api/batches/{batch_id}/close/", json={})
        r = self.client.post(f"/api/corrections/{cid}/approve/", json={})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["detail"]["code"], "batch_signed_concurrently")
        db = SessionLocal()
        try:
            self.assertEqual(db.query(Correction).filter_by(id=cid).first().status, "conflict")
            self.assertTrue(db.query(DataReview).filter_by(id=rev["id"]).first().entered_settlement)
        finally:
            db.close()


class ReversalConcurrencyTest(ApiTestBase):
    def test_concurrent_distinct_keys_do_not_double_reverse(self):
        _, batch_id = self.create_pond_batch("并发塘", batch_number="CC")
        self.client.post("/api/harvest-sales/",
                         json={"batch_id": batch_id, "sale_date": "2026-09-20",
                               "weight": 50, "unit_price": 40})
        self.client.post(f"/api/batches/{batch_id}/close/", json={})
        db = SessionLocal()
        try:
            sale = db.query(HarvestSale).filter(HarvestSale.batch_id == batch_id).first()
            sale_id = sale.id
        finally:
            db.close()

        results = []
        barrier = threading.Barrier(2)

        def worker(key):
            session = SessionLocal()
            barrier.wait()
            try:
                fact = register_reversal(
                    session,
                    entity_type="HarvestSale", entity_id=sale_id, field_name="total_amount",
                    idempotency_key=key, reason="并发冲正",
                )
                results.append(("ok", fact.id))
            except Exception as exc:  # noqa: BLE001
                results.append(("err", getattr(exc, "code", str(exc))))
            finally:
                session.close()

        t1 = threading.Thread(target=worker, args=("ck1",))
        t2 = threading.Thread(target=worker, args=("ck2",))
        t1.start(); t2.start(); t1.join(); t2.join()

        statuses = sorted(s for s, _ in results)
        self.assertEqual(statuses, ["err", "ok"], results)
        db = SessionLocal()
        try:
            count = db.query(ReversalFact).filter_by(source_entity_id=sale_id).count()
            self.assertEqual(count, 1)
        finally:
            db.close()


class LegacyMigrationTest(unittest.TestCase):
    """对没有版本列的旧库做启动迁移。"""

    def test_add_columns_idempotent(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        legacy_url = f"sqlite:///{tmp.name}"
        eng = create_engine(legacy_url)
        with eng.begin() as c:
            c.execute(text(
                "CREATE TABLE ponds (id INTEGER PRIMARY KEY, name VARCHAR(100), area FLOAT, "
                "water_depth FLOAT, species VARCHAR(100), status VARCHAR(20), "
                "created_at DATETIME, updated_at DATETIME)"))
            c.execute(text(
                "CREATE TABLE batches (id INTEGER PRIMARY KEY, batch_number VARCHAR(50), pond_id INTEGER, "
                "species VARCHAR(100), stocking_date DATE, estimated_harvest_date DATE, "
                "actual_harvest_date DATE, status VARCHAR(20), created_at DATETIME, updated_at DATETIME)"))
        eng.dispose()

        # 用独立引擎指向旧库执行 bootstrap
        import app.database as dbmod
        from sqlalchemy.orm import sessionmaker
        saved_engine = dbmod.engine
        saved_session = dbmod.SessionLocal
        probe = create_engine(legacy_url)
        dbmod.engine = probe
        dbmod.SessionLocal = sessionmaker(bind=probe)
        try:
            dbmod.bootstrap()
            dbmod.bootstrap()  # 第二次必须幂等
            with probe.begin() as c:
                cols = {r[1] for r in c.execute(text("PRAGMA table_info(ponds)")).fetchall()}
            self.assertTrue({"version", "superseded_by_id", "void_reason"} <= cols)
            self.assertIn("data_reviews", inspect(probe).get_table_names())
        finally:
            dbmod.engine = saved_engine
            dbmod.SessionLocal = saved_session
            probe.dispose()
            Path(tmp.name).unlink(missing_ok=True)


class RestartReplayTest(ApiTestBase):
    def test_reviews_and_snapshot_replay_after_restart(self):
        _, batch_id = self.create_pond_batch("重放塘", batch_number="RR")
        self.client.post("/api/stocking-records/",
                         json={"batch_id": batch_id, "species": "虾", "quantity": 1000})
        self.client.post("/api/feeding-records/",
                         json={"batch_id": batch_id, "feeding_date": "2026-09-02",
                               "feed_type": "料", "feed_quantity": 100})
        self.client.post("/api/cost-records/",
                         json={"batch_id": batch_id, "cost_date": "2026-09-02",
                               "cost_type": "feed", "amount": 300})
        self.client.post("/api/harvest-sales/",
                         json={"batch_id": batch_id, "sale_date": "2026-09-20",
                               "weight": 200, "unit_price": 20})
        self.client.post(f"/api/batches/{batch_id}/close/", json={})

        sale_id = self.client.get("/api/harvest-sales/", params={"batch_id": batch_id}).json()[0]["id"]
        self.client.post("/api/reversals/",
                         json={"entity_type": "HarvestSale", "entity_id": sale_id,
                               "field_name": "total_amount", "idempotency_key": "rev-rr"})
        cyc1 = self.client.get(f"/api/analysis/cycle/{batch_id}/?refresh=true").json()
        self.assertEqual(cyc1["total_revenue"], 0)
        version_before = cyc1["data_version"]

        # 模拟服务重启：退出再进入 lifespan，扫描与快照都从磁盘重放
        self.client_cm.__exit__(None, None, None)
        self.client_cm = TestClient(app)
        self.client = self.client_cm.__enter__()

        # 复核/冲正记录仍在；启动扫描不产生重复复核
        reversals = self.client.get("/api/reversals/", params={"batch_id": batch_id}).json()
        self.assertEqual(len(reversals), 1)
        cyc2 = self.client.get(f"/api/analysis/cycle/{batch_id}/").json()  # 无 refresh：重放快照
        self.assertEqual(cyc2["total_revenue"], 0)
        self.assertEqual(cyc2["data_version"], version_before)
        tr = self.client.get(f"/api/analysis/traceability/{batch_id}/").json()
        self.assertEqual(tr["data_version"], version_before)


if __name__ == "__main__":
    unittest.main()
