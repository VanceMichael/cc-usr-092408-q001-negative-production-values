"""批量导入：原子性、字段路径稳定、签署批次拦截。"""

import unittest

from tests.support import ClientDB


class BulkImportTest(unittest.TestCase):
    def setUp(self):
        self.ctx = ClientDB()
        self.c = self.ctx.client
        self.c.post("/api/ponds/", json={"name": "塘1", "area": 20, "water_depth": 2})
        r = self.c.post("/api/batches/", json={
            "batch_number": "B1", "pond_id": 1, "species": "草鱼",
            "stocking_date": "2026-03-01"})
        self.assertEqual(r.status_code, 201, r.text)

    def tearDown(self):
        self.ctx.close()

    def test_bulk_success(self):
        r = self.c.post("/api/cost-records/bulk/", json={"items": [
            {"batch_id": 1, "cost_date": "2026-03-03", "cost_type": "feed", "amount": 100},
            {"batch_id": 1, "cost_date": "2026-03-04", "cost_type": "labor", "amount": 200},
        ]})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["created"], 2)

    def test_bulk_atomic_and_stable_field_paths(self):
        r = self.c.post("/api/stocking-records/bulk/", json={"items": [
            {"batch_id": 1, "species": "草鱼", "quantity": 1000},
            {"batch_id": 1, "species": "草鱼", "quantity": -500},
            {"batch_id": 1, "species": "草鱼", "quantity": 0},
            {"batch_id": 999, "species": "草鱼", "quantity": 10},
        ]})
        self.assertEqual(r.status_code, 422)
        fields = r.json()["error"]["fields"]
        paths = {f["field"]: f["code"] for f in fields}
        self.assertEqual(paths.get("items[1].quantity"), "negative_value")
        self.assertEqual(paths.get("items[2].quantity"), "zero_not_allowed")
        self.assertEqual(paths.get("items[3].batch_id"), "not_found")
        # 原子：整批不落库
        rows = self.c.get("/api/stocking-records/?batch_id=1").json()
        self.assertEqual(len(rows), 0)

    def test_bulk_allows_zero_readings(self):
        r = self.c.post("/api/water-quality-records/bulk/", json={"items": [
            {"batch_id": 1, "record_date": "2026-03-02", "dissolved_oxygen": 0},
            {"batch_id": 1, "record_date": "2026-03-03", "transparency": 30},
        ]})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["created"], 2)

    def test_bulk_signed_batch_rejected(self):
        self.assertEqual(self.c.post("/api/batches/1/sign/", json={}).status_code, 200)
        r = self.c.post("/api/feeding-records/bulk/", json={"items": [
            {"batch_id": 1, "feeding_date": "2026-03-03",
             "feed_type": "A", "feed_quantity": 10},
        ]})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(
            r.json()["error"]["fields"][0]["field"], "items[0].batch_id")

    def test_bulk_empty(self):
        r = self.c.post("/api/cost-records/bulk/", json={"items": []})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["error"]["fields"][0]["code"], "empty")

    def test_bulk_pond_import(self):
        r = self.c.post("/api/ponds/bulk/", json={"items": [
            {"name": "塘A", "area": 5, "water_depth": 1.5},
            {"name": "塘B", "area": 8, "water_depth": 2.0},
        ]})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["created"], 2)

        r = self.c.post("/api/ponds/bulk/", json={"items": [
            {"name": "塘C", "area": -3, "water_depth": 1.5},
        ]})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["error"]["fields"][0]["field"], "items[0].area")


if __name__ == "__main__":
    unittest.main()
