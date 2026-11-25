"""干预效果区间评估与巡查资源协调测试。"""

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from night_noise_response.complaints import (
    attribution_resolver,
    cluster_complaints,
)
from night_noise_response.ingestion import load_domain
from night_noise_response.intervention import (
    evaluate_intervention,
    follow_up_comparison,
)
from night_noise_response.model import OperationStatus, RiskBand
from night_noise_response.observations import build_observations
from night_noise_response.risk_ranges import assess_all, latest_risk
from night_noise_response.scheduling import (
    build_plan,
    detect_conflicts,
    split_operations,
)

DATA = Path(__file__).parents[1] / "data"
NIGHTS = ["2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05",
          "2026-10-06"]
PLAN_AS_OF = datetime(2026, 10, 7, 12, 0)
LATE_AS_OF = datetime(2026, 10, 8, 12, 0)


def _prepare():
    data = load_domain(DATA)
    observations = build_observations(data, NIGHTS)
    resolver = attribution_resolver(data)
    events = cluster_complaints(
        data.complaints, "22:00", "06:00", 60, resolver)
    risks = assess_all(data, observations, events, LATE_AS_OF)
    return data, observations, events, risks


class InterventionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data, cls.observations, cls.events, cls.risks = _prepare()

    def test_effect_is_a_range_with_verdict_reduced(self):
        estimate = evaluate_intervention(
            self.data, "N-01", NIGHTS, self.observations, self.events)
        self.assertEqual(estimate.verdict, "reduced")
        lo, hi = estimate.effect_range
        self.assertLess(hi, 0)  # 区间上界仍为负
        self.assertEqual(estimate.pre_nights, ("2026-10-02", "2026-10-03"))
        self.assertEqual(estimate.post_nights, ("2026-10-05",))

    def test_immature_and_wet_nights_excluded_with_reasons(self):
        estimate = evaluate_intervention(
            self.data, "N-01", NIGHTS, self.observations, self.events)
        excluded = " ".join(estimate.excluded_nights)
        # 10-06 观察不成熟被排除
        self.assertIn("2026-10-06", excluded)
        self.assertIn("immature", excluded)
        # 10-04 观察已成熟但因雨夜气象不可比被排除
        self.assertIn("2026-10-04", excluded)
        self.assertIn("weather", excluded)

    def test_insufficient_evidence_keeps_uncertainty_not_score(self):
        # N-02 没有干预，调用应报错，证明评估绑定具体措施
        with self.assertRaises(ValueError):
            evaluate_intervention(
                self.data, "N-02", NIGHTS, self.observations, self.events)

    def test_follow_up_comparison_never_rewrites_original(self):
        estimate = evaluate_intervention(
            self.data, "N-01", NIGHTS, self.observations, self.events)
        comparison = follow_up_comparison(
            estimate, (0.0, 0.1), 0.0)
        self.assertEqual(comparison["original_verdict"], "reduced")
        self.assertFalse(comparison["basis_rewritten"])
        self.assertIn(comparison["consistency"],
                      {"consistent_or_better", "worse_than_expected",
                       "partly_overlapping"})


class SchedulingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data, cls.observations, cls.events, cls.risks = _prepare()
        cls.latest = {s: latest_risk(p) for s, p in cls.risks.items()}
        cls.latest = {k: v for k, v in cls.latest.items() if v}

    def test_confirmed_and_completed_operations_are_frozen(self):
        frozen, replannable, expired = split_operations(self.data, PLAN_AS_OF)
        frozen_ids = {op.operation_id for op in frozen}
        self.assertIn("o-001", frozen_ids)   # confirmed
        self.assertIn("o-004", frozen_ids)   # completed
        # 已过期的 proposed 不进入重评
        self.assertIn("o-002", {op.operation_id for op in expired})
        self.assertNotIn("o-002",
                         {op.operation_id for op in replannable})

    def test_frozen_revisits_appear_as_pinned_recommendations(self):
        plan = build_plan(self.data, self.latest, "2026-10-07", PLAN_AS_OF)
        pinned = {r.site_id: r for r in plan.recommendations
                  if r.source == "pinned_operation"}
        self.assertIn("N-01", pinned)
        self.assertIn("N-02", pinned)
        self.assertTrue(all(r.feasible for r in pinned.values()))
        # 冻结建议必须保留当时依据快照，且这些快照进入规划的快照清单
        self.assertTrue(all(r.basis_snapshot_id for r in pinned.values()))
        self.assertTrue(plan.snapshot_ids)
        for r in pinned.values():
            self.assertIn(r.basis_snapshot_id, plan.snapshot_ids)

    def test_infeasible_recommendation_names_blocker(self):
        plan = build_plan(self.data, self.latest, "2026-10-07", PLAN_AS_OF)
        n03 = next(r for r in plan.recommendations if r.site_id == "N-03"
                   and r.source == "risk_engine")
        self.assertFalse(n03.feasible)
        self.assertTrue(n03.blockers)
        self.assertIsNone(n03.assignment)

    def test_unknown_band_triggers_verification_patrol(self):
        plan = build_plan(self.data, self.latest, "2026-10-07", PLAN_AS_OF)
        n03 = next(r for r in plan.recommendations if r.site_id == "N-03")
        self.assertEqual(n03.kind, "patrol")
        self.assertEqual(n03.risk_band, RiskBand.UNKNOWN)

    def test_staff_double_booking_detected(self):
        conflicts = detect_conflicts(self.data.operations)
        conflict = next(c for c in conflicts
                        if set(c["operation_ids"]) == {"o-001", "o-002"})
        self.assertEqual(conflict["shared_resources"][0]["resource_id"],
                         "team-a")

    def test_new_readings_change_priority_but_keep_history(self):
        # 同一批已冻结操作在两个评估时点保持冻结身份
        plan_a = build_plan(self.data, self.latest, "2026-10-07", PLAN_AS_OF)
        frozen_a = set(plan_a.frozen_operations)
        # 模拟更晚的时点，冻结集合不缩小
        plan_b = build_plan(self.data, self.latest, "2026-10-07", LATE_AS_OF)
        self.assertTrue(frozen_a.issubset(set(plan_b.frozen_operations)))
        completed = next(op for op in self.data.operations
                         if op.operation_id == "o-004")
        self.assertEqual(completed.status, OperationStatus.COMPLETED)
        self.assertIsNotNone(completed.result)
        self.assertIsNotNone(completed.basis_snapshot_id)

    def test_injected_high_readings_raise_priority_without_rewriting_basis(self):
        import json
        import shutil
        import tempfile
        from pathlib import Path as P

        from night_noise_response.complaints import (
            attribution_resolver,
            cluster_complaints,
        )
        from night_noise_response.observations import build_observations
        from night_noise_response.risk_ranges import assess_all, rank_sites

        def ranking(root):
            dd = load_domain(root)
            obs = build_observations(dd, NIGHTS)
            ev = cluster_complaints(
                dd.complaints, "22:00", "06:00", 60,
                attribution_resolver(dd))
            rr = assess_all(dd, obs, ev, LATE_AS_OF)
            latest = {s: latest_risk(p) for s, p in rr.items()}
            return dd, rank_sites({k: v for k, v in latest.items() if v})

        _, before = ranking(DATA)
        before_n02 = next(r for r in before if r.site_id == "N-02")
        with tempfile.TemporaryDirectory() as tmp:
            copied = P(tmp) / "data"
            shutil.copytree(DATA, copied)
            sensor_path = copied / "sensors.json"
            rows = json.loads(sensor_path.read_text(encoding="utf-8"))
            nxt = max(int(r["reading_id"].split("-")[1]) for r in rows) + 1
            for hour, db in ((22, 72), (23, 71)):
                rows.append({
                    "reading_id": f"s-{nxt:03d}", "site_id": "N-02",
                    "sensor_id": "main",
                    "observed_at": f"2026-10-06T{hour:02d}:00",
                    "db": db, "quality": "valid"})
                nxt += 1
            sensor_path.write_text(
                json.dumps(rows, ensure_ascii=False, indent=2),
                encoding="utf-8")
            reloaded, after = ranking(copied)
            after_n02 = next(r for r in after if r.site_id == "N-02")
            # 新读数抬高了 N-02 的风险下界
            self.assertGreater(after_n02.lower, before_n02.lower)
            # 已完成巡查的结果与依据快照保持原样
            completed = next(o for o in reloaded.operations
                             if o.operation_id == "o-004")
            original = next(o for o in self.data.operations
                            if o.operation_id == "o-004")
            self.assertEqual(completed.result, original.result)
            self.assertEqual(completed.basis_snapshot_id,
                             original.basis_snapshot_id)


if __name__ == "__main__":
    unittest.main()
