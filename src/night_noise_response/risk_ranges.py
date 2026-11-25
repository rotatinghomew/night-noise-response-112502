"""可解释的扰民风险区间。

不输出单一总分：每个地点-夜给出 [lower, upper] 风险区间，
下界只由明确超标证据支撑，上界把观察缺口按策略权重计入；
投诉以"去重后事件数"与"原始条数"并列呈现，暴露记录机会差异。
观察成熟度不足（或评估时点窗口尚未结束）时标记 provisional，
并在 basis 中保留全部事实与缺口标识，供审计追查。
"""

from __future__ import annotations

from datetime import datetime

from .complaints import events_for_night
from .ingestion import DomainData
from .model import (
    NightObservation,
    NoiseEvent,
    Policy,
    RiskBand,
    RiskRange,
    SlotState,
)
from .observations import (
    exceedance_rate_range,
    gap_reading_ids,
    is_mature,
    valid_ratio,
    weighted_gap_ratio,
)


def classify_band(lower: float, upper: float, policy: Policy,
                  event_count: int, valid_slots: int) -> RiskBand:
    """按区间位置和投诉事件确定风险带，规则固定且可解释。"""
    if valid_slots == 0 and event_count == 0:
        return RiskBand.UNKNOWN
    # 高带要求证据本身就强（下界高），或区间整体处于高位且多起事件
    if lower >= policy.band_high_lower or (
            upper >= policy.band_high_upper and event_count >= 2):
        band = RiskBand.HIGH
    elif upper >= policy.band_moderate_upper:
        band = RiskBand.MODERATE
    else:
        band = RiskBand.LOW
    # 投诉事件至少使风险带上调一级，两条及以上事件直接进入高带
    if event_count >= 2:
        band = RiskBand.HIGH
    elif event_count == 1 and band is RiskBand.LOW:
        band = RiskBand.MODERATE
    return band


def assess_night(data: DomainData, obs: NightObservation,
                 events: tuple[NoiseEvent, ...],
                 as_of: datetime) -> RiskRange:
    """评估单个地点-夜的风险区间。"""
    policy = data.policy
    lower, upper = exceedance_rate_range(obs)
    night_events = events_for_night(events, obs.site_id, obs.night)
    event_count = len(night_events)
    raw_complaints = sum(e.complaint_count for e in night_events)

    mature = is_mature(obs, policy)
    window_ended = obs.slots[-1].slot_at < as_of if obs.slots else False
    provisional = not (mature and window_ended)

    gap_ratio = weighted_gap_ratio(obs, policy)
    band = classify_band(lower, upper, policy, event_count,
                         obs.valid_count)
    state_counts = {state.value: obs.count(state) for state in SlotState}
    basis = {
        "threshold_db": policy.threshold_db,
        "valid_ratio": round(valid_ratio(obs), 4),
        "weighted_gap_ratio": round(gap_ratio, 4),
        "weather_bucket": obs.weather_bucket,
        "has_activity": obs.has_activity,
        "window_ended": window_ended,
        "event_ids": [e.event_id for e in night_events],
        "complaint_ids": [
            cid for e in night_events for cid in e.complaint_ids
        ],
        "exceeded_reading_ids": [
            s.reading_ids[0] for s in obs.slots
            if s.state is SlotState.EXCEEDED and s.reading_ids
        ],
        "below_threshold_reading_ids": [
            s.reading_ids[0] for s in obs.slots
            if s.state is SlotState.OBSERVED_BELOW and s.reading_ids
        ],
        "slot_states": [s.state.value for s in obs.slots],
    }
    return RiskRange(
        site_id=obs.site_id,
        night=obs.night,
        lower=lower,
        upper=upper,
        band=band,
        provisional=provisional,
        mature=mature,
        valid_ratio=round(valid_ratio(obs), 4),
        slot_count=obs.slot_count,
        state_counts=state_counts,
        event_count=event_count,
        raw_complaint_count=raw_complaints,
        evidence_gap_ids=gap_reading_ids(obs),
        basis=basis,
    )


def assess_all(data: DomainData,
               observations: dict[str, dict[str, NightObservation]],
               events: tuple[NoiseEvent, ...],
               as_of: datetime) -> dict[str, dict[str, RiskRange]]:
    result: dict[str, dict[str, RiskRange]] = {}
    for site_id, per_night in observations.items():
        result[site_id] = {
            night: assess_night(data, obs, events, as_of)
            for night, obs in per_night.items()
        }
    return result


def latest_risk(risk_by_night: dict[str, RiskRange]) -> RiskRange | None:
    """取某地点最近一个夜间窗口的风险。"""
    if not risk_by_night:
        return None
    return risk_by_night[sorted(risk_by_night)[-1]]


def rank_sites(risks: dict[str, RiskRange]) -> list[RiskRange]:
    """风险带优先、上界次之的确定性排序；不产生跨地点总分。"""
    return sorted(
        risks.values(),
        key=lambda r: (-r.band.rank, -r.upper, -r.lower,
                       -r.event_count, r.site_id),
    )


def opportunity_bias_summary(data: DomainData,
                             observations: dict[str, dict[str,
                                                          NightObservation]],
                             events: tuple[NoiseEvent, ...] | None = None
                             ) -> list[dict]:
    """记录机会对比：传感器覆盖与已安排巡查的地点间差异。

    用于说明"投诉多的街区往往也是传感器最密集、巡查最频繁的地方"，
    避免把记录机会差异误读为环境恶化。投诉同时给出口径：
    - raw_complaints_recorded：按录入地点统计的原始投诉；
    - raw_complaints_attributed：按当前边界版本归属后的投诉。
    """
    patrols: dict[str, int] = {}
    for op in data.operations:
        if op.kind in ("patrol", "revisit"):
            patrols[op.site_id] = patrols.get(op.site_id, 0) + 1
    attributed: dict[str, int] = {}
    if events:
        for event in events:
            attributed[event.site_id] = (
                attributed.get(event.site_id, 0) + event.complaint_count)
    rows = []
    for site_id in data.site_ids:
        per_night = observations.get(site_id, {})
        total_slots = sum(o.slot_count for o in per_night.values())
        valid_slots = sum(o.valid_count for o in per_night.values())
        gap_slots = sum(o.evidence_gap_count for o in per_night.values())
        recorded_count = sum(
            1 for c in data.complaints
            if c.recorded_site_id == site_id and c.status == "received"
        )
        rows.append({
            "site_id": site_id,
            "scheduled_slots": total_slots,
            "valid_slots": valid_slots,
            "evidence_gap_slots": gap_slots,
            "coverage_ratio": round(valid_slots / total_slots, 4)
            if total_slots else 0.0,
            "raw_complaints_recorded": recorded_count,
            "raw_complaints_attributed": attributed.get(site_id, 0),
            "patrol_operations": patrols.get(site_id, 0),
        })
    return rows
