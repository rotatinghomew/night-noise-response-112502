"""投诉去重、风险区间和边界版本归属测试。"""

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from night_noise_response.complaints import (
    attribution_changes,
    attribution_resolver,
    cluster_complaints,
    resolve_site_by_zone,
    version_at,
)
from night_noise_response.ingestion import load_domain
from night_noise_response.model import RiskBand
from night_noise_response.observations import build_observations
from night_noise_response.risk_ranges import (
    assess_all,
    latest_risk,
    opportunity_bias_summary,
    rank_sites,
)
from night_noise_response.versions import (
    activity_correction_windows,
    correction_history,
    correction_impact,
)

DATA = Path(__file__).parents[1] / "data"
NIGHTS = ["2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05",
          "2026-10-06"]
AS_OF = datetime(2026, 10, 8, 12, 0)


class ComplaintClusteringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_domain(DATA)

    def test_duplicate_complaints_merge_into_one_event(self):
        events = cluster_complaints(
            self.data.complaints, "22:00", "06:00", 60)
        ev = next(e for e in events if set(e.complaint_ids)
                  == {"c-001", "c-002"})
        self.assertEqual(ev.site_id, "N-01")
        self.assertEqual(ev.complaint_count, 2)
        # 原始投诉标识全部保留可查回
        self.assertEqual(ev.channels, ("app", "phone"))

    def test_withdrawn_complaint_does_not_form_event(self):
        events = cluster_complaints(
            self.data.complaints, "22:00", "06:00", 60)
        all_ids = {cid for e in events for cid in e.complaint_ids}
        self.assertNotIn("c-009", all_ids)

    def test_complaints_beyond_window_split_into_separate_events(self):
        # c-006(23:30) 与 c-005(次日00:40) 相差 70 分钟，超出 60 分钟归并窗口
        resolver = attribution_resolver(self.data)
        events = cluster_complaints(
            self.data.complaints, "22:00", "06:00", 60, resolver)
        n01_night_0404 = [e for e in events
                          if e.site_id == "N-01" and e.night == "2026-10-04"]
        self.assertEqual(len(n01_night_0404), 2)
        self.assertEqual(
            sorted(cid for e in n01_night_0404
                   for cid in e.complaint_ids),
            ["c-005", "c-006"])


class BoundaryAttributionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_domain(DATA)

    def test_zone_c_reattributed_after_correction(self):
        # 更正前 zone-c 属于 N-02
        before = resolve_site_by_zone(
            self.data, datetime(2026, 10, 3, 23, 0), "zone-c")
        self.assertEqual(before, "N-02")
        # 更正后 zone-c 属于 N-01
        after = resolve_site_by_zone(
            self.data, datetime(2026, 10, 5, 0, 40), "zone-c")
        self.assertEqual(after, "N-01")

    def test_recorded_site_preserved_while_resolved_site_differs(self):
        changes = attribution_changes(self.data)
        change = next(c for c in changes if c["complaint_id"] == "c-005")
        self.assertEqual(change["recorded_site_id"], "N-02")
        self.assertEqual(change["resolved_site_id"], "N-01")
        complaint = next(c for c in self.data.complaints
                         if c.complaint_id == "c-005")
        # 原始记录不被改写
        self.assertEqual(complaint.site_id, "N-02")

    def test_clustering_uses_version_aware_resolver(self):
        resolver = attribution_resolver(self.data)
        events = cluster_complaints(
            self.data.complaints, "22:00", "06:00", 60, resolver)
        c005_event = next(e for e in events
                          if "c-005" in e.complaint_ids)
        self.assertEqual(c005_event.site_id, "N-01")

    def test_correction_impact_lists_affected_windows_and_records(self):
        impact = correction_impact(self.data, "sv-n01-v2", NIGHTS)
        self.assertEqual(impact.added_zones, ("zone-c",))
        self.assertIn("c-005", impact.affected_complaint_ids)
        self.assertIn("2026-10-04", impact.affected_nights)
        self.assertIn("2026-10-02", impact.comparable_nights_before)
        self.assertTrue(impact.correction_reason)

    def test_activity_correction_windows_visible(self):
        rows = activity_correction_windows(self.data)
        mkt = next(r for r in rows if r["activity_id"] == "mkt-01")
        self.assertEqual(mkt["window_before"], ["21:30", "02:30"])
        self.assertEqual(mkt["window_after"], ["22:00", "02:00"])
        self.assertTrue(mkt["is_correction"])

    def test_correction_history_ordered(self):
        history = correction_history(self.data)
        self.assertTrue(all(v.is_correction for v in history))
        self.assertTrue(all(history[i].effective_from <= history[i + 1].effective_from
                            for i in range(len(history) - 1)))


class RiskRangeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_domain(DATA)
        cls.observations = build_observations(cls.data, NIGHTS)
        resolver = attribution_resolver(cls.data)
        cls.events = cluster_complaints(
            cls.data.complaints, "22:00", "06:00", 60, resolver)
        cls.risks = assess_all(cls.data, cls.observations, cls.events, AS_OF)

    def test_range_not_a_single_score(self):
        risk = self.risks["N-01"]["2026-10-03"]
        self.assertIsInstance(risk.lower, float)
        self.assertIsInstance(risk.upper, float)
        self.assertLessEqual(risk.lower, risk.upper)
        self.assertFalse(hasattr(risk, "total_score"))

    def test_fully_unobserved_night_is_unknown_band(self):
        risk = self.risks["N-03"]["2026-10-04"]
        self.assertEqual(risk.band, RiskBand.UNKNOWN)
        self.assertTrue(risk.provisional)

    def test_provisional_flag_tracks_maturity(self):
        settled = self.risks["N-01"]["2026-10-02"]
        self.assertFalse(settled.provisional)
        self.assertTrue(settled.mature)
        # 10-06 有效覆盖仅一半，成熟度不足，结论暂定
        immature = self.risks["N-01"]["2026-10-06"]
        self.assertTrue(immature.provisional)
        self.assertFalse(immature.mature)

    def test_event_and_raw_complaint_counts_both_reported(self):
        risk = self.risks["N-01"]["2026-10-03"]
        self.assertEqual(risk.event_count, 1)
        self.assertEqual(risk.raw_complaint_count, 2)

    def test_ranking_is_deterministic_and_uses_band_then_upper(self):
        latest = {s: latest_risk(p) for s, p in self.risks.items()}
        latest = {k: v for k, v in latest.items() if v}
        order = [r.site_id for r in rank_sites(latest)]
        self.assertEqual(sorted(order), ["N-01", "N-02", "N-03"])
        # 无证据的 UNKNOWN 地点因需要现场核实排在前面
        self.assertEqual(order[0], "N-03")

    def test_opportunity_bias_exposes_coverage_difference(self):
        summary = opportunity_bias_summary(
            self.data, self.observations, self.events)
        by_site = {row["site_id"]: row for row in summary}
        self.assertGreater(by_site["N-01"]["coverage_ratio"],
                           by_site["N-03"]["coverage_ratio"])
        self.assertGreater(by_site["N-03"]["evidence_gap_slots"],
                           by_site["N-01"]["evidence_gap_slots"])
        # 投诉多的 N-01 同时巡查更频繁，记录机会差异被显式暴露
        self.assertGreaterEqual(by_site["N-01"]["patrol_operations"],
                                by_site["N-03"]["patrol_operations"])
        # 两种投诉口径并存；边界更正使归属口径与录入口径不同
        self.assertIn("raw_complaints_recorded", by_site["N-01"])
        self.assertIn("raw_complaints_attributed", by_site["N-01"])
        total_recorded = sum(r["raw_complaints_recorded"]
                             for r in summary)
        total_attributed = sum(r["raw_complaints_attributed"]
                               for r in summary)
        # 归并不改变投诉总量，只改变地点分布
        self.assertEqual(total_recorded, total_attributed)
        self.assertGreater(by_site["N-01"]["raw_complaints_attributed"],
                           by_site["N-01"]["raw_complaints_recorded"])


if __name__ == "__main__":
    unittest.main()
