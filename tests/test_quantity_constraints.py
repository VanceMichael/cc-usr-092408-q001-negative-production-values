"""数量约束：创建/修改两条路径对三类规则执行一致校验，错误字段稳定。"""

import unittest

from tests.support import ClientDB


class QuantityConstraintTest(unittest.TestCase):
    def setUp(self):
        self.ctx = ClientDB()
        self.c = self.ctx.client
        r = self.c.post("/api/ponds/", json={"name": "塘1", "area": 20, "water_depth": 2})
        self.assertEqual(r.status_code, 201, r.text)
        r = self.c.post(
            "/api/batches/",
            json={"batch_number": "B1", "pond_id": 1, "species": "草鱼",
                  "stocking_date": "2026-03-01"},
        )
        self.assertEqual(r.status_code, 201, r.text)

    def tearDown(self):
        self.ctx.close()

    # --- 必须大于零 -----------------------------------------------------
    def test_pond_area_must_be_positive(self):
        for value, code in ((-10, "negative_value"), (0, "zero_not_allowed")):
            r = self.c.post("/api/ponds/", json={
                "name": f"塘{value}", "area": value, "water_depth": 2})
            self.assertEqual(r.status_code, 422)
            body = r.json()["error"]
            self.assertEqual(body["code"], "validation_error")
            self.assertEqual(body["fields"][0]["field"], "area")
            self.assertEqual(body["fields"][0]["code"], code)

    def test_stocking_quantity_must_be_positive(self):
        r = self.c.post("/api/stocking-records/", json={
            "batch_id": 1, "species": "草鱼", "quantity": -500})
        self.assertEqual(r.status_code, 422)
        field = r.json()["error"]["fields"][0]
        self.assertEqual(field["field"], "quantity")
        self.assertEqual(field["code"], "negative_value")

        r = self.c.post("/api/stocking-records/", json={
            "batch_id": 1, "species": "草鱼", "quantity": 1000})
        self.assertEqual(r.status_code, 201, r.text)
        rid = r.json()["id"]
        r = self.c.put(f"/api/stocking-records/{rid}/", json={"quantity": 0})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["error"]["fields"][0]["code"], "zero_not_allowed")

    def test_harvest_unit_price_must_be_positive(self):
        r = self.c.post("/api/harvest-sales/", json={
            "batch_id": 1, "sale_date": "2026-08-01", "weight": 100, "unit_price": 0})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["error"]["fields"][0]["field"], "unit_price")

    # --- 允许为零 -------------------------------------------------------
    def test_water_quality_zero_allowed(self):
        r = self.c.post("/api/water-quality-records/", json={
            "batch_id": 1, "record_date": "2026-03-02",
            "dissolved_oxygen": 0, "ammonia_nitrogen": 0, "ph_value": 0,
        })
        self.assertEqual(r.status_code, 201, r.text)

    def test_water_quality_negative_rejected(self):
        r = self.c.post("/api/water-quality-records/", json={
            "batch_id": 1, "record_date": "2026-03-02", "ph_value": -0.5})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["error"]["fields"][0]["field"], "ph_value")

    def test_ph_range(self):
        r = self.c.post("/api/water-quality-records/", json={
            "batch_id": 1, "record_date": "2026-03-02", "ph_value": 15})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["error"]["fields"][0]["code"], "above_max")

    # --- 修改路径与创建一致 ---------------------------------------------
    def test_update_enforces_same_rules(self):
        r = self.c.post("/api/cost-records/", json={
            "batch_id": 1, "cost_date": "2026-03-03", "cost_type": "feed", "amount": 100})
        self.assertEqual(r.status_code, 201)
        rid = r.json()["id"]
        r = self.c.put(f"/api/cost-records/{rid}/", json={"amount": -1})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["error"]["fields"][0]["field"], "amount")
        # 未改成负数，原值保留
        self.assertEqual(self.c.get(f"/api/cost-records/{rid}/").json()["amount"], 100)

    def test_nan_and_inf_rejected_at_service_layer(self):
        import math
        from app.validation import check_field
        for bad in (math.nan, math.inf, -math.inf):
            self.assertIsNotNone(check_field("feeding", "feed_quantity", bad))
            self.assertIsNotNone(check_field("pond", "area", bad))


class FrozenBatchWriteTest(unittest.TestCase):
    def setUp(self):
        self.ctx = ClientDB()
        self.c = self.ctx.client
        self.c.post("/api/ponds/", json={"name": "塘1", "area": 20, "water_depth": 2})
        self.c.post("/api/batches/", json={
            "batch_number": "B1", "pond_id": 1, "species": "草鱼",
            "stocking_date": "2026-03-01"})
        self.c.post("/api/stocking-records/", json={
            "batch_id": 1, "species": "草鱼", "quantity": 1000})
        self.assertEqual(
            self.c.post("/api/batches/1/sign/", json={}).status_code, 200)

    def tearDown(self):
        self.ctx.close()

    def test_signed_batch_rejects_writes(self):
        r = self.c.post("/api/feeding-records/", json={
            "batch_id": 1, "feeding_date": "2026-03-03",
            "feed_type": "A", "feed_quantity": 5})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "state_conflict")

        r = self.c.put("/api/stocking-records/1/", json={"quantity": 1200})
        self.assertEqual(r.status_code, 409)

        r = self.c.delete("/api/stocking-records/1/")
        self.assertEqual(r.status_code, 409)

        r = self.c.post("/api/feeding-records/bulk/", json={"items": [{
            "batch_id": 1, "feeding_date": "2026-03-03",
            "feed_type": "A", "feed_quantity": 5}]})
        self.assertEqual(r.status_code, 422)  # 批量预检字段错误
        self.assertIn("batch_signed", [f["code"] for f in r.json()["error"]["fields"]])


if __name__ == "__main__":
    unittest.main()
