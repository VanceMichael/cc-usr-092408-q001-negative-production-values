"""详情、追溯、周期分析共享同一有效版本；快照重启可重放。"""

import unittest

from tests.support import ClientDB


class EffectiveVersionTest(unittest.TestCase):
    def setUp(self):
        self.ctx = ClientDB()
        self.c = self.ctx.client
        self.c.post("/api/ponds/", json={"name": "塘1", "area": 20, "water_depth": 2})
        self.c.post("/api/batches/", json={
            "batch_number": "B1", "pond_id": 1, "species": "草鱼",
            "stocking_date": "2026-03-01",
            "actual_harvest_date": "2026-08-10"})
        self.c.post("/api/stocking-records/", json={
            "batch_id": 1, "species": "草鱼", "quantity": 10000, "total_weight": 200})
        self.c.post("/api/feeding-records/", json={
            "batch_id": 1, "feeding_date": "2026-04-01",
            "feed_type": "颗粒料", "feed_quantity": 600})
        self.c.post("/api/cost-records/", json={
            "batch_id": 1, "cost_date": "2026-04-01", "cost_type": "feed",
            "amount": 3000})
        self.c.post("/api/harvest-sales/", json={
            "batch_id": 1, "sale_date": "2026-08-05", "weight": 1000, "unit_price": 10})

    def tearDown(self):
        self.ctx.close()

    def test_cycle_metrics_use_positive_data(self):
        d = self.c.get("/api/analysis/cycle/1/").json()
        self.assertEqual(d["initial_quantity"], 10000)
        self.assertEqual(d["harvest_weight"], 1000)
        self.assertEqual(d["total_cost"], 3000)
        self.assertEqual(d["total_revenue"], 10000)
        self.assertEqual(d["profit"], 7000)
        self.assertEqual(d["yield_per_mu"], 50)
        self.assertGreater(d["survival_rate"], 0)

    def test_snapshot_replay_within_process(self):
        first = self.c.get("/api/analysis/cycle/1/").json()
        self.assertFalse(first["replayed"])
        second = self.c.get("/api/analysis/cycle/1/").json()
        self.assertTrue(second["replayed"])
        self.assertEqual(first["profit"], second["profit"])

    def test_snapshot_invalidates_after_write_and_replays_after_restart(self):
        first = self.c.get("/api/analysis/cycle/1/").json()
        # 追加一笔成本 -> 版本推进 -> 快照失效
        self.c.post("/api/cost-records/", json={
            "batch_id": 1, "cost_date": "2026-05-01", "cost_type": "labor",
            "amount": 500})
        changed = self.c.get("/api/analysis/cycle/1/").json()
        self.assertFalse(changed["replayed"])
        self.assertEqual(changed["total_cost"], 3500)
        self.assertEqual(changed["profit"], 6500)
        self.assertGreater(changed["data_version"], first["data_version"])

        # 模拟服务重启：快照按版本持久化，直接重放
        c2 = self.ctx.reopen()
        replayed = c2.get("/api/analysis/cycle/1/").json()
        self.assertTrue(replayed["replayed"])
        self.assertEqual(replayed["profit"], 6500)
        self.assertEqual(replayed["total_cost"], 3500)

    def test_detail_trace_cycle_share_version(self):
        d = self.c.get("/api/analysis/cycle/1/").json()
        t = self.c.get("/api/analysis/traceability/1/").json()
        self.assertEqual(t["batch"]["data_version"], d["data_version"])
        self.assertEqual(t["stocking_records"][0]["quantity"], d["initial_quantity"])
        self.assertEqual(t["harvest_sales"][0]["weight"], d["harvest_weight"])
        self.assertEqual(t["pond_info"]["area"], d["area"])

    def test_correction_reflects_in_all_three_views(self):
        # 人工对投苗尾数发起复核并更正（未签署批次）
        r = self.c.post("/api/data-reviews/", json={
            "entity": "stocking", "record_id": 1, "field": "quantity",
            "rule_code": "recount"})
        self.assertEqual(r.status_code, 201, r.text)
        rid = r.json()["id"]
        self.c.post(f"/api/data-reviews/{rid}/decision/", json={"approve": True})
        r = self.c.post(f"/api/data-reviews/{rid}/resolve/",
                        json={"requested_value": 8000, "idempotency_key": "k"})
        self.assertEqual(r.status_code, 200, r.text)

        self.assertEqual(self.c.get("/api/stocking-records/1/").json()["quantity"], 8000)
        self.assertEqual(
            self.c.get("/api/analysis/traceability/1/").json()
            ["stocking_records"][0]["quantity"],
            8000)
        # 版本推进导致周期重算（存活率变化）
        d = self.c.get("/api/analysis/cycle/1/?refresh=1").json()
        self.assertEqual(d["initial_quantity"], 8000)


class RestartReplayReviewTest(unittest.TestCase):
    """复核进度与冲正结果在服务重启后仍可重放。"""

    def setUp(self):
        self.ctx = ClientDB()
        self.c = self.ctx.client
        self.c.post("/api/ponds/", json={"name": "塘1", "area": 20, "water_depth": 2})
        self.c.post("/api/batches/", json={
            "batch_number": "B1", "pond_id": 1, "species": "草鱼",
            "stocking_date": "2026-03-01"})
        from datetime import date
        db = self.ctx.session()
        from app.models import FeedingRecord
        db.add(FeedingRecord(batch_id=1, feeding_date=date(2026, 4, 1),
                             feed_type="A", feed_quantity=100))
        db.commit()
        db.close()

    def tearDown(self):
        self.ctx.close()

    def test_review_and_reversal_survive_restart(self):
        self.c.post("/api/batches/1/sign/", json={})
        r = self.c.post("/api/data-reviews/", json={
            "entity": "feeding", "record_id": 1, "field": "feed_quantity",
            "rule_code": "recheck", "entered_settlement": True})
        rid = r.json()["id"]
        self.c.post(f"/api/data-reviews/{rid}/decision/", json={"approve": True})
        self.c.post(f"/api/data-reviews/{rid}/resolve/", json={"idempotency_key": "rev"})

        self.assertEqual(
            self.c.get("/api/feeding-records/1/").json()["feed_quantity"], 0)

        c2 = self.ctx.reopen()
        # 复核状态持久
        review = c2.get(f"/api/data-reviews/{rid}/").json()
        self.assertEqual(review["status"], "reversed")
        # 有效值仍按冲正呈现
        self.assertEqual(c2.get("/api/feeding-records/1/").json()["feed_quantity"], 0)
        # 周期分析冲正生效（投喂合计=0），且快照可重放
        c2.get("/api/analysis/cycle/1/")
        again = c2.get("/api/analysis/cycle/1/").json()
        self.assertTrue(again["replayed"])
        self.assertEqual(again["feed_total"], 0)


if __name__ == "__main__":
    unittest.main()
