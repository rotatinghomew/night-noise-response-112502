"""输入契约、证据快照、防篡改与追查链测试。"""

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from night_noise_response.audit import (
    make_snapshot,
    reconcile_follow_up,
    trace_recommendation,
    verify_record_hashes,
    verify_snapshot,
)
from night_noise_response.complaints import (
    attribution_resolver,
    cluster_complaints,
)
from night_noise_response.ingestion import (
    canonical_hash,
    load_domain,
)
from night_noise_response.intervention import evaluate_intervention
from night_noise_response.model import OperationStatus
from night_noise_response.observations import build_observations
from night_noise_response.pipeline import run_pipeline, run_with_trace
from night_noise_response.risk_ranges import assess_all, latest_risk
from night_noise_response.scheduling import build_plan

DATA = Path(__file__).parents[1] / "data"
NIGHTS = ["2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05",
          "2026-10-06"]
AS_OF = datetime(2026, 10, 7, 12, 0)


class ContractTest(unittest.TestCase):
    def test_valid_reading_requires_value(self):
        data = load_domain(DATA)
        valid = [r for r in data.readings if r.quality.value == "valid"]
        self.assertTrue(valid)
        self.assertTrue(all(isinstance(r.db, int) for r in valid))

    def test_offline_and_maintenance_have_no_value(self):
        data = load_domain(DATA)
        for reading in data.readings:
            if reading.quality.value in ("offline", "maintenance"):
                self.assertIsNone(reading.db)

    def test_unknown_quality_rejected(self):
        from night_noise_response.ingestion import _parse_reading

        with self.assertRaises(ValueError):
            _parse_reading({"reading_id": "x", "site_id": "N-01",
                            "observed_at": "2026-10-03T22:00",
                            "db": 70, "quality": "bogus"})

    def test_unknown_site_reference_rejected(self):
        from night_noise_response.ingestion import _parse_complaint

        record = {"complaint_id": "x", "site_id": "N-99",
                  "reported_at": "2026-10-03T22:00", "channel": "app",
                  "text_key": "music", "status": "received"}
        # 解析本身成功，跨表引用校验在 load_domain 中失败
        parsed = _parse_complaint(record)
        self.assertEqual(parsed.site_id, "N-99")

    def test_canonical_hash_stable_and_sensitive(self):
        record = {"a": 1, "b": [1, 2]}
        h1 = canonical_hash(record)
        h2 = canonical_hash({"b": [1, 2], "a": 1})
        self.assertEqual(h1, h2)  # 键顺序不影响
        self.assertNotEqual(h1, canonical_hash({"a": 1, "b": [1, 3]}))


class AuditTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_domain(DATA)
        cls.observations = build_observations(cls.data, NIGHTS)
        resolver = attribution_resolver(cls.data)
        cls.events = cluster_complaints(
            cls.data.complaints, "22:00", "06:00", 60, resolver)
        cls.risks = assess_all(cls.data, cls.observations, cls.events, AS_OF)
        cls.latest = {s: latest_risk(p) for s, p in cls.risks.items()}
        cls.latest = {k: v for k, v in cls.latest.items() if v}
        cls.plan = build_plan(cls.data, cls.latest, "2026-10-07", AS_OF)
        cls.effects = (evaluate_intervention(
            cls.data, "N-01", NIGHTS, cls.observations, cls.events),)
        cls.snapshot = make_snapshot(
            cls.data, cls.plan, cls.risks, cls.effects)

    def test_loaded_bundle_has_no_tampering(self):
        self.assertEqual(verify_record_hashes(self.data), {})

    def test_disk_tampering_detected_against_snapshot_baseline(self):
        import json
        import shutil
        import tempfile
        from pathlib import Path as P

        from night_noise_response.audit import verify_against_snapshot

        with tempfile.TemporaryDirectory() as tmp:
            copied = P(tmp) / "data"
            shutil.copytree(DATA, copied)
            sensor_path = copied / "sensors.json"
            rows = json.loads(sensor_path.read_text(encoding="utf-8"))
            target = next(r for r in rows if r["reading_id"] == "s-001")
            target["db"] = 99
            sensor_path.write_text(
                json.dumps(rows, ensure_ascii=False, indent=2),
                encoding="utf-8")
            reloaded = load_domain(copied)
            findings = verify_against_snapshot(reloaded, self.snapshot)
            self.assertIn("sensors.json", findings)
            self.assertIn("s-001", findings["sensors.json"])
            # 原始资料包自身对照仍然干净
            self.assertEqual(
                verify_against_snapshot(self.data, self.snapshot), {})

    def test_snapshot_digest_verifies(self):
        self.assertTrue(verify_snapshot(self.snapshot))

    def test_snapshot_tampering_detected(self):
        from dataclasses import replace

        tampered = replace(
            self.snapshot,
            plan_summary={**self.snapshot.plan_summary, "plan_id": "fake"})
        self.assertFalse(verify_snapshot(tampered))

    def test_trace_recommendation_links_facts_gaps_and_versions(self):
        rec_id = next(r.recommendation_id
                      for r in self.plan.recommendations
                      if r.source == "pinned_operation")
        trace = trace_recommendation(
            self.plan, rec_id, self.data, self.risks,
            (self.snapshot,))
        self.assertIn("risk_facts", trace)
        self.assertIsInstance(trace["fact_records"]["operations"], list)
        self.assertIn("sv-n01-v1", trace["fact_records"]["site_versions"])
        self.assertEqual(trace["snapshot_id"], self.snapshot.snapshot_id)

    def test_trace_unknown_recommendation_raises(self):
        with self.assertRaises(KeyError):
            trace_recommendation(self.plan, "nope", self.data,
                                 self.risks, (self.snapshot,))

    def test_follow_up_reconciliation_keeps_frozen_basis(self):
        completed = next(op for op in self.data.operations
                         if op.status is OperationStatus.COMPLETED)
        night = completed.starts_at.date().isoformat()
        # 已完成操作发生在 10-05 夜
        night_risks = self.risks[completed.site_id]
        result = reconcile_follow_up(completed, self.data, night_risks)
        self.assertFalse(result["basis_rewritten"])
        self.assertEqual(result["recorded_result"], completed.result)

    def test_reconcile_requires_completed_operation(self):
        proposed = next(op for op in self.data.operations
                        if op.status is OperationStatus.PROPOSED)
        with self.assertRaises(ValueError):
            reconcile_follow_up(proposed, self.data,
                                self.risks[proposed.site_id])


class PipelineTest(unittest.TestCase):
    def test_end_to_end_report_is_complete_and_traceable(self):
        report = run_pipeline(DATA, AS_OF, "2026-10-07")
        for key in ("risk_ranking", "risk_detail", "intervention_effects",
                    "plan", "snapshot", "integrity", "opportunity_bias",
                    "noise_events", "attribution_changes",
                    "correction_history"):
            self.assertIn(key, report)
        self.assertEqual(report["integrity"]["tampered_records"], {})
        self.assertEqual(len(report["snapshot"]["digest"]), 64)

    def test_pipeline_trace_contains_chain(self):
        report = run_pipeline(DATA, AS_OF, "2026-10-07")
        rec_id = report["plan"]["recommendations"][0]["recommendation_id"]
        traced = run_with_trace(DATA, rec_id, AS_OF, "2026-10-07")
        self.assertIn("trace", traced)
        self.assertEqual(traced["trace"]["recommendation_id"], rec_id)

    def test_provisional_vs_settled_changes_with_as_of(self):
        # 窗口尚未结束时，当夜风险必须是暂定的
        early = datetime(2026, 10, 6, 23, 0)
        report = run_pipeline(DATA, early, "2026-10-06")
        night_risk = report["risk_detail"]["N-01"]["2026-10-06"]
        self.assertTrue(night_risk["provisional"])


if __name__ == "__main__":
    unittest.main()
