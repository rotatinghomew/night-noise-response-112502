"""观察机会与成熟度核算。

把传感器读数对齐到夜间槽位，并把"没有记录"与"明确低于阈值"分开：
- 没有任何读数的槽位 -> NOT_OBSERVED（观察缺口，不是安静证据）；
- valid 读数 -> EXCEEDED / OBSERVED_BELOW（明确证据）；
- partial / maintenance / offline -> 各自独立的缺口状态；
同一槽位有多条读数时，保留全部引用，取数值最保守（最大）者判定。
"""

from __future__ import annotations

from collections import defaultdict

from .clock import Window, build_window
from .ingestion import DomainData
from .model import (
    NightObservation,
    Policy,
    Quality,
    Reading,
    SlotObservation,
    SlotState,
    WeatherObs,
)


def align_readings(readings: tuple[Reading, ...], window: Window
                   ) -> dict[int, list[Reading]]:
    """把读数按槽位索引分组，窗口外的读数被忽略并单独可查。"""
    grouped: dict[int, list[Reading]] = defaultdict(list)
    for reading in readings:
        idx = window.slot_index(reading.observed_at)
        if idx is not None:
            grouped[idx].append(reading)
    return grouped


def _resolve_slot(site_id: str, night: str, idx: int, window: Window,
                  readings: list[Reading], threshold: int) -> SlotObservation:
    slot_at = window.slots[idx]
    if not readings:
        return SlotObservation(site_id=site_id, night=night, slot_index=idx,
                               slot_at=slot_at, state=SlotState.NOT_OBSERVED)

    qualities = {r.quality for r in readings}
    reading_ids = tuple(r.reading_id for r in readings)
    valued = [r for r in readings if r.db is not None]

    if Quality.VALID in qualities:
        valid = [r for r in readings if r.quality is Quality.VALID]
        chosen = max(valid, key=lambda r: r.db if r.db is not None else -1)
        state = (SlotState.EXCEEDED if (chosen.db or 0) >= threshold
                 else SlotState.OBSERVED_BELOW)
        return SlotObservation(site_id=site_id, night=night, slot_index=idx,
                               slot_at=slot_at, state=state,
                               reading_ids=reading_ids, db=chosen.db,
                               quality=Quality.VALID)

    # 没有有效读数时，质量状态按 维护 > 离线 > 部分 的保守顺序表达
    if Quality.MAINTENANCE in qualities:
        quality = Quality.MAINTENANCE
        state = SlotState.MAINTENANCE
    elif Quality.OFFLINE in qualities:
        quality = Quality.OFFLINE
        state = SlotState.OFFLINE
    else:
        quality = Quality.PARTIAL
        state = SlotState.PARTIAL
    db = max((r.db for r in valued), default=None)
    return SlotObservation(site_id=site_id, night=night, slot_index=idx,
                           slot_at=slot_at, state=state,
                           reading_ids=reading_ids, db=db, quality=quality)


def build_night_observation(site_id: str, night: str,
                            readings: tuple[Reading, ...],
                            weather: WeatherObs | None,
                            has_activity: bool,
                            policy: Policy) -> NightObservation:
    window: Window = build_window(night, policy.night_start,
                                  policy.night_end, policy.slot_minutes)
    grouped = align_readings(readings, window)
    slots = tuple(
        _resolve_slot(site_id, night, idx, window,
                      grouped.get(idx, []), policy.threshold_db)
        for idx in range(window.slot_count)
    )
    return NightObservation(
        site_id=site_id,
        night=night,
        slot_count=window.slot_count,
        states=tuple(s.state for s in slots),
        slots=slots,
        weather_bucket=weather.bucket if weather else None,
        has_activity=has_activity,
    )


def build_observations(data: DomainData, nights: list[str]
                       ) -> dict[str, dict[str, NightObservation]]:
    """返回 {site_id: {night: NightObservation}}。"""
    weather_lookup = data.weather_lookup()
    result: dict[str, dict[str, NightObservation]] = {}
    for site_id in data.site_ids:
        per_night: dict[str, NightObservation] = {}
        site_readings = data.readings_for(site_id)
        for night in nights:
            night_readings = tuple(
                r for r in site_readings
                if r.observed_at >= build_window(
                    night, data.policy.night_start,
                    data.policy.night_end, data.policy.slot_minutes).starts_at
                and r.observed_at < build_window(
                    night, data.policy.night_start,
                    data.policy.night_end, data.policy.slot_minutes).ends_at
            )
            weather = weather_lookup.get((site_id, night))
            has_activity = _has_activity(data, site_id, night)
            per_night[night] = build_night_observation(
                site_id, night, night_readings, weather,
                has_activity, data.policy)
        result[site_id] = per_night
    return result


def _has_activity(data: DomainData, site_id: str, night: str) -> bool:
    from .clock import parse_hm

    window = build_window(night, data.policy.night_start,
                          data.policy.night_end, data.policy.slot_minutes)
    for activity in data.activities:
        if activity.site_id != site_id:
            continue
        if not (activity.effective_from <= window.starts_at
                and (activity.effective_to is None
                     or activity.effective_to > window.starts_at)):
            continue
        start_h, start_m = parse_hm(activity.window_start)
        end_h, end_m = parse_hm(activity.window_end)
        act_start = window.starts_at.replace(hour=start_h, minute=start_m)
        if end_h <= start_h:
            from datetime import timedelta

            act_end = act_start + timedelta(days=1)
            act_end = act_end.replace(hour=end_h, minute=end_m)
        else:
            act_end = window.starts_at.replace(hour=end_h, minute=end_m)
        if act_start < window.ends_at and act_end > window.starts_at:
            return True
    return False


# ---------------------------------------------------------------------------
# 观察机会与成熟度
# ---------------------------------------------------------------------------

def valid_ratio(obs: NightObservation) -> float:
    """有效证据槽位占比（明确达标或超标的槽位）。"""
    if obs.slot_count == 0:
        return 0.0
    return obs.valid_count / obs.slot_count


def opportunity_counts(obs: NightObservation) -> dict[str, int]:
    """逐状态统计记录机会，缺口与证据分列。"""
    return {state.value: obs.count(state) for state in SlotState}


def is_mature(obs: NightObservation, policy: Policy) -> bool:
    """成熟度只看有效证据覆盖比例；夜间窗口结束后才有资格成熟。"""
    return valid_ratio(obs) >= policy.maturity_min_valid_ratio


def weighted_gap_ratio(obs: NightObservation, policy: Policy) -> float:
    """按策略权重折算的观察缺口比例（维护/离线/无记录权重最高）。"""
    if obs.slot_count == 0:
        return 1.0
    weights = {
        SlotState.NOT_OBSERVED: policy.risk_missing_weight,
        SlotState.PARTIAL: policy.risk_partial_weight,
        SlotState.MAINTENANCE: policy.risk_maintenance_weight,
        SlotState.OFFLINE: policy.risk_offline_weight,
    }
    weighted = sum(
        weights[state]
        for state in obs.states
        if state in weights
    )
    return weighted / obs.slot_count


def exceedance_rate_range(obs: NightObservation) -> tuple[float, float]:
    """超标率的不确定区间。

    下界：仅把明确超标槽位计为超标；
    上界：把全部观察缺口（含 partial/maintenance/offline/无记录）
    也视为可能超标。明确低于阈值的读数不会进入上界。
    """
    total = obs.slot_count
    if total == 0:
        return (0.0, 1.0)
    lower = obs.exceeded_count / total
    upper = (obs.exceeded_count + obs.evidence_gap_count) / total
    return (round(lower, 4), round(upper, 4))


def gap_reading_ids(obs: NightObservation) -> tuple[str, ...]:
    """观察缺口涉及的读数标识（无记录槽位没有读数，自然不出现）。"""
    ids: list[str] = []
    for slot in obs.slots:
        if slot.state.is_evidence_gap:
            ids.extend(slot.reading_ids)
    return tuple(ids)
