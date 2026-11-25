"""领域模型：显式状态枚举与不可变值对象。

设计原则：
- 传感器质量状态是一等语义，绝不与"无记录"混同；
- 观察槽位有独立的状态（含 NOT_OBSERVED），低于阈值的达标读数与缺失分开表达；
- 所有值对象冻结，业务函数返回新对象，不就地修改原始记录。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime


class Quality(str, enum.Enum):
    """传感器原始质量标记。"""

    VALID = "valid"
    PARTIAL = "partial"
    MAINTENANCE = "maintenance"
    OFFLINE = "offline"


class SlotState(str, enum.Enum):
    """观察槽位的最终状态。

    NOT_OBSERVED 与 OBSERVED_BELOW 严格区分：
    前者是没有观察机会，后者是明确证据。
    """

    NOT_OBSERVED = "not_observed"          # 该槽位没有任何读数
    EXCEEDED = "exceeded"                  # 有效读数且 >= 阈值
    OBSERVED_BELOW = "observed_below"      # 有效读数且 < 阈值
    PARTIAL = "partial"                    # 部分质量读数，不单独判定超标
    MAINTENANCE = "maintenance"
    OFFLINE = "offline"

    @property
    def is_observed(self) -> bool:
        return self in (SlotState.EXCEEDED, SlotState.OBSERVED_BELOW)

    @property
    def is_evidence_gap(self) -> bool:
        """计入观察缺口的状态（非明确证据）。"""
        return self in (
            SlotState.NOT_OBSERVED,
            SlotState.PARTIAL,
            SlotState.MAINTENANCE,
            SlotState.OFFLINE,
        )


class OperationStatus(str, enum.Enum):
    PROPOSED = "proposed"        # 尚未确认，可重新评估
    CONFIRMED = "confirmed"      # 已确认，冻结
    COMPLETED = "completed"      # 已完成，冻结并保留当时依据
    CANCELLED = "cancelled"

    @property
    def is_frozen(self) -> bool:
        return self in (OperationStatus.CONFIRMED, OperationStatus.COMPLETED)


class RiskBand(str, enum.Enum):
    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"
    UNKNOWN = "unknown"

    @property
    def rank(self) -> int:
        # UNKNOWN 排在高风险之后、中等之前：信息缺失需要优先核实，
        # 但不凌驾于证据充分的高风险地点
        return {"high": 4, "unknown": 3, "moderate": 2, "low": 1}[self.value]


@dataclass(frozen=True)
class Reading:
    reading_id: str
    site_id: str
    sensor_id: str
    observed_at: datetime
    db: int | None
    quality: Quality
    raw: dict


@dataclass(frozen=True)
class Complaint:
    complaint_id: str
    site_id: str                 # 记录当时归属地点（原样保留）
    recorded_site_id: str        # 录入系统的地点（通常相同，更正时可能不同）
    zone_id: str | None
    reported_at: datetime
    channel: str
    text_key: str
    status: str                  # received / withdrawn ...
    raw: dict


@dataclass(frozen=True)
class WeatherObs:
    weather_id: str
    site_id: str
    night: str
    bucket: str                  # dry / wet
    condition: str
    temp_c: float
    wind_ms: float
    raw: dict


@dataclass(frozen=True)
class ActivityVersion:
    activity_id: str
    site_id: str
    activity_type: str           # night_market / intervention
    version: str
    window_start: str
    window_end: str
    effective_from: datetime
    effective_to: datetime | None
    is_correction: bool
    correction_reason: str | None
    measure: str | None
    raw: dict


@dataclass(frozen=True)
class SiteVersion:
    version_id: str
    site_id: str
    boundary_version: str
    catchment_zones: tuple[str, ...]
    effective_from: datetime
    effective_to: datetime | None
    is_correction: bool
    correction_reason: str | None
    raw: dict


@dataclass(frozen=True)
class Operation:
    operation_id: str
    site_id: str
    kind: str                    # patrol / notice / revisit
    starts_at: datetime
    duration_minutes: int
    staff: str | None
    equipment: str | None
    required_qualification: str | None
    status: OperationStatus
    linked_operation_id: str | None
    result: dict | None
    basis_snapshot_id: str | None
    raw: dict

    @property
    def ends_at(self) -> datetime:
        from datetime import timedelta

        return self.starts_at + timedelta(minutes=self.duration_minutes)


@dataclass(frozen=True)
class Resource:
    resource_id: str
    resource_type: str           # team / equipment
    qualifications: tuple[str, ...]
    home_site: str
    raw: dict


@dataclass(frozen=True)
class Policy:
    policy_id: str
    version: str
    threshold_db: int
    night_start: str
    night_end: str
    slot_minutes: int
    cluster_window_minutes: int
    maturity_min_valid_ratio: float
    risk_missing_weight: float
    risk_partial_weight: float
    risk_maintenance_weight: float
    risk_offline_weight: float
    patrol_duration_minutes: int
    notice_duration_minutes: int
    notice_window_start: str
    notice_window_end: str
    band_high_upper: float
    band_high_lower: float
    band_moderate_upper: float
    raw: dict


@dataclass(frozen=True)
class SlotObservation:
    """单个地点-夜-槽位的综合观察结果。"""

    site_id: str
    night: str
    slot_index: int
    slot_at: datetime
    state: SlotState
    reading_ids: tuple[str, ...] = ()
    db: int | None = None
    quality: Quality | None = None


@dataclass(frozen=True)
class NightObservation:
    site_id: str
    night: str
    slot_count: int
    states: tuple[SlotState, ...]
    slots: tuple[SlotObservation, ...]
    weather_bucket: str | None
    has_activity: bool

    def count(self, state: SlotState) -> int:
        return sum(1 for s in self.states if s == state)

    @property
    def valid_count(self) -> int:
        return (self.count(SlotState.EXCEEDED)
                + self.count(SlotState.OBSERVED_BELOW))

    @property
    def exceeded_count(self) -> int:
        return self.count(SlotState.EXCEEDED)

    @property
    def not_observed_count(self) -> int:
        return self.count(SlotState.NOT_OBSERVED)

    @property
    def evidence_gap_count(self) -> int:
        return sum(1 for s in self.states if s.is_evidence_gap)


@dataclass(frozen=True)
class NoiseEvent:
    """去重后的扰民事件，引用全部原始投诉。"""

    event_id: str
    site_id: str
    zone_id: str | None
    night: str
    first_reported_at: datetime
    last_reported_at: datetime
    complaint_ids: tuple[str, ...]
    channels: tuple[str, ...]
    text_keys: tuple[str, ...]

    @property
    def complaint_count(self) -> int:
        return len(self.complaint_ids)


@dataclass(frozen=True)
class RiskRange:
    """某地点某夜间窗口的可解释风险区间（不是单一总分）。"""

    site_id: str
    night: str
    lower: float
    upper: float
    band: RiskBand
    provisional: bool                 # 观察成熟度不足时为 True
    mature: bool
    valid_ratio: float
    slot_count: int
    state_counts: dict[str, int]
    event_count: int
    raw_complaint_count: int
    evidence_gap_ids: tuple[str, ...]
    basis: dict = field(default_factory=dict)


@dataclass(frozen=True)
class EffectEstimate:
    """干预前后效果的区间估计与成熟度说明。"""

    site_id: str
    intervention_id: str
    pre_nights: tuple[str, ...]
    post_nights: tuple[str, ...]
    excluded_nights: tuple[str, ...]
    pre_rate_range: tuple[float, float]
    post_rate_range: tuple[float, float]
    effect_range: tuple[float, float]   # post - pre，负值代表下降
    event_rate_pre: float | None
    event_rate_post: float | None
    control_rate_range: tuple[float, float] | None
    did_range: tuple[float, float] | None
    verdict: str                        # reduced / increased / unclear / insufficient
    maturity_notes: tuple[str, ...]
    basis: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Recommendation:
    """一条可追溯的行动建议。"""

    recommendation_id: str
    site_id: str
    kind: str                           # patrol / notice / revisit / none
    reason: str
    risk_band: RiskBand
    risk_range: tuple[float, float]
    feasible: bool
    blockers: tuple[str, ...]
    assignment: dict | None             # 可行时的具体资源安排
    basis_snapshot_id: str | None
    source: str                         # risk_engine / pinned_operation / follow_up


@dataclass(frozen=True)
class Plan:
    """一轮规划的完整结果。"""

    plan_id: str
    as_of: datetime
    target_night: str
    recommendations: tuple[Recommendation, ...]
    conflicts: tuple[dict, ...]
    frozen_operations: tuple[str, ...]
    replanned_operations: tuple[str, ...]
    snapshot_ids: tuple[str, ...]
    notes: tuple[str, ...]
