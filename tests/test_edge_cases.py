"""边界场景：塘口冻结、整笔冲正入口、正常修改对旧更正的释放。"""

import unittest

from tests.support import ClientDB


class PondFreezeTest(unittest.TestCase):
    def setUp(self):
        self.ctx = ClientDB()
        self.c = self.ctx.client
        self.c.post("/api/ponds/", json={"name": "塘", "area": 20, "water_depth": 2})
        self.c.post("/api/batches/", json={
            "batch_number": "B", "pond_id": 1, "species": "鱼",
            "stocking_date": "2026-03-01"})

    def tearDown(self):
        self.ctx.close()

    def test_pond_area_locked_after_batch_signed(self):
        # 签署前可以改
        r = self.c.put("/api/ponds/1/", json={"area": 25})
        self.assertEqual(r.status_code, 200, r.text)

        self.assertEqual(self.c.post("/api/batches/1/sign/", json={}).status_code, 200)

        r = self.c.put("/api/ponds/1/", json={"area": 30})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "state_conflict")
        # 值未变
        self.assertEqual(self.c.get("/api/ponds/1/").json()["area"], 25)

        # 非数量字段（名称之外不影响结算）—— 名称修改同样放行数量以外字段
        r = self.c.put("/api/ponds/1/", json={"species": "鲤鱼"})
        self.assertEqual(r.status_code, 200, r.text)


class WholeSaleReversalTest(unittest.TestCase):
    def setUp(self):
        self.ctx = ClientDB()
        self.c = self.ctx.client
        self.c.post("/api/ponds/", json={"name": "塘", "area": 20, "water_depth": 2})
        self.c.post("/api/batches/", json={
            "batch_number": "B", "pond_id": 1, "species": "鱼",
            "stocking_date": "2026-03-01"})
        self.c.post("/api/harvest-sales/", json={
            "batch_id": 1, "sale_date": "2026-08-01",
            "weight": 100, "unit_price": 10})
        self.c.post("/api/batches/1/sign/", json={})

    def tearDown(self):
        self.ctx.close()

    def test_reversal_from_unit_price_voids_whole_sale(self):
        r = self.c.post("/api/data-reviews/", json={
            "entity": "harvest", "record_id": 1, "field": "unit_price",
            "rule_code": "price_dispute", "entered_settlement": True})
        self.assertEqual(r.status_code, 201, r.text)
        rid = r.json()["id"]
        self.c.post(f"/api/data-reviews/{rid}/decision/", json={"approve": True})
        r = self.c.post(f"/api/data-reviews/{rid}/resolve/",
                        json={"idempotency_key": "price-rev"})
        self.assertEqual(r.status_code, 200, r.text)
        # 单价是 POSITIVE，但作为整笔销售作废允许归零
        self.assertEqual(r.json()["after_value"], 0.0)

        sale = self.c.get("/api/harvest-sales/1/").json()
        self.assertEqual(sale["weight"], 0)
        self.assertEqual(sale["unit_price"], 0)
        self.assertEqual(sale["total_amount"], 0)

        d = self.c.get("/api/analysis/cycle/1/?refresh=1").json()
        self.assertEqual(d["total_revenue"], 0)
        self.assertEqual(d["harvest_weight"], 0)


class CorrectionReleaseTest(unittest.TestCase):
    def setUp(self):
        self.ctx = ClientDB()
        self.c = self.ctx.client
        self.c.post("/api/ponds/", json={"name": "塘", "area": 20, "water_depth": 2})
        self.c.post("/api/batches/", json={
            "batch_number": "B", "pond_id": 1, "species": "鱼",
            "stocking_date": "2026-03-01"})
        self.c.post("/api/cost-records/", json={
            "batch_id": 1, "cost_date": "2026-04-01", "cost_type": "feed",
            "amount": 1000})

    def tearDown(self):
        self.ctx.close()

    def test_normal_update_releases_prior_correction(self):
        r = self.c.post("/api/data-reviews/", json={
            "entity": "cost", "record_id": 1, "field": "amount",
            "rule_code": "recount"})
        rid = r.json()["id"]
        self.c.post(f"/api/data-reviews/{rid}/decision/", json={"approve": True})
        r = self.c.post(f"/api/data-reviews/{rid}/resolve/",
                        json={"requested_value": 800})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.c.get("/api/cost-records/1/").json()["amount"], 800)

        # 事后正常修改覆盖该字段
        r = self.c.put("/api/cost-records/1/", json={"amount": 900})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.c.get("/api/cost-records/1/").json()["amount"], 900)

        # 原复核保持 corrected 结论；旧流水被标记 cancelled 留痕
        db = self.ctx.session()
        from app.models import RecordCorrection
        corr = db.query(RecordCorrection).filter_by(review_id=rid).first()
        self.assertEqual(corr.status, "cancelled")
        db.close()


if __name__ == "__main__":
    unittest.main()
