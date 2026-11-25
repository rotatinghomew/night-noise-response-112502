"""夜间窗口、槽位对齐与观察机会语义测试。"""

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from night_noise_response.clock import build_window, night_key, overlap
from night_noise_response.ingestion import load_domain
from night_noise_response.model import Quality, SlotState
from night_noise_response.observations import (
    build_observations,
    exceedance_rate_range,
    is_mature,
    valid_ratio,
    weighted_gap_ratio,
)

DATA = Path(__file__).parents[1] / "data"
NIGHTS = ["2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05",
          "2026-10-06"]


class ClockTest(unittest.TestCase):
    def test_night_window_spans_midnight_with_eight_slots(self):
        window = build_window("2026-10-03", "22:00", "06:00", 60)
        self.assertEqual(window.slot_count, 8)
        self.assertEqual(window.starts_at, datetime(2026, 10, 3, 22, 0))
        self.assertEqual(window.ends_at, datetime(2026, 10, 4, 6, 0))
        self.assertEqual(window.slots[-1], datetime(2026, 10, 4, 5, 0))

    def test_night_key_groups_early_morning_into_previous_night(self):
        self.assertEqual(night_key(datetime(2026, 10, 4, 1, 0)),
                         "2026-10-03")
        self.assertEqual(night_key(datetime(2026, 10, 3, 22, 0)),
                         "2026-10-03")

    def test_slot_index_and_overlap(self):
        window = build_window("2026-10-03")
        self.assertEqual(window.slot_index(datetime(2026, 10, 3, 22, 0)), 0)
        self.assertEqual(window.slot_index(datetime(2026, 10, 4, 5, 59)), 7)
        self.assertIsNone(window.slot_index(datetime(2026, 10, 3, 20, 0)))
        self.assertTrue(
            overlap(datetime(2026, 10, 3, 22, 0), datetime(2026, 10, 3, 23, 0),
                    datetime(2026, 10, 3, 22, 30), datetime(2026, 10, 4, 0, 0)))
        self.assertFalse(
            overlap(datetime(2026, 10, 3, 22, 0), datetime(2026, 10, 3, 23, 0),
                    datetime(2026, 10, 3, 23, 0), datetime(2026, 10, 4, 0, 0)))


class ObservationSemanticsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_domain(DATA)
        cls.observations = build_observations(cls.data, NIGHTS)

    def test_missing_is_not_silence_and_below_threshold_is_evidence(self):
        # N-01 2026-10-03：原始 s-002 离线占据槽位1，槽位2同为离线
        obs = self.observations["N-01"]["2026-10-03"]
        states = list(obs.states)
        self.assertEqual(states[1], SlotState.OFFLINE)
        self.assertEqual(states[2], SlotState.OFFLINE)
        # 68 >= 65 判超标；明确低于阈值是证据而非缺口
        self.assertEqual(states[0], SlotState.EXCEEDED)
        self.assertIn(SlotState.OBSERVED_BELOW, states)
        self.assertNotIn(SlotState.OBSERVED_BELOW,
                         [s for s in states if s.is_evidence_gap])
        # N-02 2026-10-03 槽位0 无任何读数 -> 不是安静，是观察缺口
        n02 = self.observations["N-02"]["2026-10-03"]
        self.assertEqual(n02.states[0], SlotState.NOT_OBSERVED)
        self.assertTrue(n02.states[0].is_evidence_gap)

    def test_maintenance_and_partial_are_distinct_gap_states(self):
        # 10-04 有两个维护槽位（该夜成熟但因雨夜不参与效果对比）
        obs = self.observations["N-01"]["2026-10-04"]
        self.assertEqual(obs.count(SlotState.MAINTENANCE), 2)
        self.assertEqual(obs.count(SlotState.PARTIAL), 0)
        # partial 状态出现在 10-06
        mixed = self.observations["N-01"]["2026-10-06"]
        self.assertEqual(mixed.count(SlotState.PARTIAL), 1)
        self.assertEqual(mixed.count(SlotState.NOT_OBSERVED), 2)

    def test_partial_reading_keeps_reference_value_but_never_exceeds(self):
        # s-003 partial 带数值 51，不能判成超标
        obs = self.observations["N-02"]["2026-10-03"]
        partial_slots = [s for s in obs.slots
                         if s.state is SlotState.PARTIAL]
        self.assertTrue(partial_slots)
        self.assertTrue(all(s.state is not SlotState.EXCEEDED
                            for s in partial_slots))

    def test_no_record_slot_has_no_reading_id(self):
        obs = self.observations["N-03"]["2026-10-04"]
        self.assertEqual(obs.count(SlotState.NOT_OBSERVED), 8)
        self.assertEqual(obs.valid_count, 0)
        # 无记录槽位不会伪造读数标识
        self.assertEqual(
            [rid for s in obs.slots for rid in s.reading_ids], [])

    def test_range_keeps_uncertainty_separate_from_evidence(self):
        # N-03 2026-10-04 全缺口：下界 0，上界 1
        obs = self.observations["N-03"]["2026-10-04"]
        lower, upper = exceedance_rate_range(obs)
        self.assertEqual((lower, upper), (0.0, 1.0))
        # N-01 2026-10-02 全有效：区间退化为点估计
        full = self.observations["N-01"]["2026-10-02"]
        lower, upper = exceedance_rate_range(full)
        self.assertEqual(lower, upper)

    def test_maturity_uses_valid_ratio_only(self):
        mature_night = self.observations["N-01"]["2026-10-02"]
        sparse_night = self.observations["N-03"]["2026-10-04"]
        self.assertTrue(is_mature(mature_night, self.data.policy))
        self.assertFalse(is_mature(sparse_night, self.data.policy))
        self.assertEqual(valid_ratio(mature_night), 1.0)
        self.assertEqual(valid_ratio(sparse_night), 0.0)

    def test_weighted_gap_counts_offline_and_missing_fully(self):
        obs = self.observations["N-01"]["2026-10-03"]
        # 2 个离线槽位，权重均为 1.0 -> 2/8
        self.assertAlmostEqual(weighted_gap_ratio(obs, self.data.policy),
                               0.25)
        # N-01 2026-10-06：2 个无记录 + 1 个离线 + 1 个 partial(0.5)
        mixed = self.observations["N-01"]["2026-10-06"]
        self.assertAlmostEqual(weighted_gap_ratio(mixed, self.data.policy),
                               (2 + 1 + 0.5) / 8)


if __name__ == "__main__":
    unittest.main()
