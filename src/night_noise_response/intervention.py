"""限时播放措施的干预效果评估。

效果只能在记录机会与观察成熟度允许的范围内解释：
- 只纳入观察成熟（valid 槽位占比达标）且气象可比较的夜间窗口；
- 超标率与事件率都给区间，不用一个总分替代；
- 证据不足时 verdict=insufficient 并保留不确定范围；
- 可选对照地点做差分（DiD 风格），对照本身不成熟时不强行给结论。
"""

from __future__ import annotations

from dataclasses import dataclass

from .complaints import events_for_night
from .ingestion import DomainData
from .model import EffectEstimate, NightObservation, NoiseEvent
from .observations import exceedance_rate_range, is_mature, valid_ratio
from .versions import intervention_for


@dataclass(frozen=True)
class NightRate:
    night: str
    lower: float
    upper: float
    event_rate: float
    weather_bucket: str | None
    mature: bool
    valid_ratio: float
    excluded_reason: str | None


def _night_rates(data: DomainData, site_id: str, nights: list[str],
                 observations: dict[str, dict[str, NightObservation]],
                 events: tuple[NoiseEvent, ...],
                 weather_buckets: set[str]) -> list[NightRate]:
    rows: list[NightRate] = []
    for night in nights:
        obs = observations[site_id].get(night)
        if obs is None:
            rows.append(NightRate(night, 0.0, 1.0, 0.0, None, False, 0.0,
                                  "no_window"))
            continue
        lower, upper = exceedance_rate_range(obs)
        night_events = events_for_night(events, site_id, night)
        event_rate = len(night_events) / obs.slot_count if obs.slot_count else 0.0
        mature = is_mature(obs, data.policy)
        reason = None
        if not mature:
            reason = ("immature: valid_ratio "
                      f"{valid_ratio(obs):.2f} < "
                      f"{data.policy.maturity_min_valid_ratio}")
        elif obs.weather_bucket not in weather_buckets:
            reason = f"weather not comparable: {obs.weather_bucket}"
        rows.append(NightRate(
            night=night, lower=lower, upper=upper,
            event_rate=round(event_rate, 4),
            weather_bucket=obs.weather_bucket, mature=mature,
            valid_ratio=valid_ratio(obs), excluded_reason=reason,
        ))
    return rows


def _pooled_range(rates: list[NightRate]) -> tuple[float, float]:
    """把多夜区间按夜平均（等权），保留区间形式。"""
    if not rates:
        return (0.0, 0.0)
    lower = sum(r.lower for r in rates) / len(rates)
    upper = sum(r.upper for r in rates) / len(rates)
    return (round(lower, 4), round(upper, 4))


def _pooled_event_rate(rates: list[NightRate]) -> float | None:
    if not rates:
        return None
    return round(sum(r.event_rate for r in rates) / len(rates), 4)


def evaluate_intervention(data: DomainData,
                          site_id: str,
                          all_nights: list[str],
                          observations: dict[str, dict[str,
                                                       NightObservation]],
                          events: tuple[NoiseEvent, ...],
                          control_site_id: str | None = None
                          ) -> EffectEstimate:
    """评估某地点干预措施的前后效果。"""
    intervention = intervention_for(data, site_id)
    if intervention is None:
        raise ValueError(f"地点 {site_id} 没有干预措施记录")
    start = intervention.effective_from

    pre_nights_all = [
        n for n in all_nights
        if observations[site_id][n].slots[-1].slot_at < start
    ]
    post_nights_all = [n for n in all_nights if n not in pre_nights_all]

    # 对照范围只在相同气象桶（默认 dry）之间可比
    weather_buckets = {"dry"}
    pre_rates_all = _night_rates(data, site_id, pre_nights_all,
                                 observations, events, weather_buckets)
    post_rates_all = _night_rates(data, site_id, post_nights_all,
                                  observations, events, weather_buckets)
    pre_rates = [r for r in pre_rates_all if r.excluded_reason is None]
    post_rates = [r for r in post_rates_all if r.excluded_reason is None]
    excluded = tuple(
        f"{r.night}: {r.excluded_reason}"
        for r in [*pre_rates_all, *post_rates_all]
        if r.excluded_reason
    )

    notes: list[str] = [
        "效果解释限于成熟且气象可比较的夜间窗口；"
        "区间下界基于明确超标证据，上界包含观察缺口。",
    ]
    if not pre_rates or not post_rates:
        notes.append("干预前或干预后没有合格夜间窗口，证据不足。")
        pre_range = _pooled_range(pre_rates)
        post_range = _pooled_range(post_rates)
        return EffectEstimate(
            site_id=site_id,
            intervention_id=intervention.activity_id,
            pre_nights=tuple(r.night for r in pre_rates),
            post_nights=tuple(r.night for r in post_rates),
            excluded_nights=excluded,
            pre_rate_range=pre_range,
            post_rate_range=post_range,
            effect_range=(
                round(post_range[0] - pre_range[1], 4),
                round(post_range[1] - pre_range[0], 4),
            ),
            event_rate_pre=_pooled_event_rate(pre_rates),
            event_rate_post=_pooled_event_rate(post_rates),
            control_rate_range=None,
            did_range=None,
            verdict="insufficient",
            maturity_notes=tuple(notes),
            basis={"intervention_measure": intervention.measure,
                   "effective_from": start.isoformat()},
        )

    pre_range = _pooled_range(pre_rates)
    post_range = _pooled_range(post_rates)
    effect_range = (
        round(post_range[0] - pre_range[1], 4),
        round(post_range[1] - pre_range[0], 4),
    )
    event_pre = _pooled_event_rate(pre_rates)
    event_post = _pooled_event_rate(post_rates)

    control_range = None
    did_range = None
    if control_site_id and control_site_id != site_id:
        result = _control_ranges(
            data, control_site_id,
            [r.night for r in pre_rates],
            [r.night for r in post_rates],
            observations, events, weather_buckets, notes)
        if result is not None:
            control_range, ctrl_diff = result
            # DiD = 治疗组变化区间 - 对照组变化区间（区间减法）
            did_range = (
                round(effect_range[0] - ctrl_diff[1], 4),
                round(effect_range[1] - ctrl_diff[0], 4),
            )

    verdict = _verdict(effect_range)
    if verdict == "unclear":
        notes.append("效果区间跨越 0，方向不确定，保留不确定范围。")
    elif verdict == "reduced":
        notes.append("区间上界仍为负，超标风险下降的证据较明确。")
    else:
        notes.append("区间下界仍为正，措施未显示降噪作用。")

    return EffectEstimate(
        site_id=site_id,
        intervention_id=intervention.activity_id,
        pre_nights=tuple(r.night for r in pre_rates),
        post_nights=tuple(r.night for r in post_rates),
        excluded_nights=excluded,
        pre_rate_range=pre_range,
        post_rate_range=post_range,
        effect_range=effect_range,
        event_rate_pre=event_pre,
        event_rate_post=event_post,
        control_rate_range=control_range,
        did_range=did_range,
        verdict=verdict,
        maturity_notes=tuple(notes),
        basis={
            "intervention_measure": intervention.measure,
            "effective_from": start.isoformat(),
            "weather_buckets": sorted(weather_buckets),
            "control_site_id": control_site_id,
        },
    )


def _control_ranges(data, control_site_id, pre_nights, post_nights,
                    observations, events, weather_buckets, notes):
    """返回 (对照前后区间对, 对照自身变化区间)；不可比时返回 None。"""
    if control_site_id not in observations:
        notes.append(f"对照地点 {control_site_id} 无观察资料，跳过对照。")
        return None
    pre_ctrl = [r for r in _night_rates(
        data, control_site_id, pre_nights, observations, events,
        weather_buckets) if r.excluded_reason is None]
    post_ctrl = [r for r in _night_rates(
        data, control_site_id, post_nights, observations, events,
        weather_buckets) if r.excluded_reason is None]
    if not pre_ctrl or not post_ctrl:
        notes.append("对照地点缺少成熟可比窗口，不计算差分。")
        return None
    pre_range = _pooled_range(pre_ctrl)
    post_range = _pooled_range(post_ctrl)
    ctrl_diff = (
        round(post_range[0] - pre_range[1], 4),
        round(post_range[1] - pre_range[0], 4),
    )
    return (pre_range, post_range), ctrl_diff


def _verdict(effect_range: tuple[float, float]) -> str:
    lo, hi = effect_range
    if hi < 0:
        return "reduced"
    if lo > 0:
        return "increased"
    return "unclear"


def follow_up_comparison(estimate: EffectEstimate,
                         observed_post_range: tuple[float, float],
                         observed_event_rate: float | None) -> dict:
    """回访数据进入后，与原先干预解释做对比，不覆盖原结论。"""
    expected_lo, expected_hi = estimate.post_rate_range
    actual_lo, actual_hi = observed_post_range
    if actual_hi <= expected_hi:
        consistency = "consistent_or_better"
    elif actual_lo >= expected_lo:
        consistency = "worse_than_expected"
    else:
        consistency = "partly_overlapping"
    return {
        "site_id": estimate.site_id,
        "intervention_id": estimate.intervention_id,
        "original_verdict": estimate.verdict,
        "expected_post_range": estimate.post_rate_range,
        "observed_post_range": observed_post_range,
        "observed_event_rate": observed_event_rate,
        "consistency": consistency,
        "basis_rewritten": False,
        "note": "回访结果与原解释并列保存，原结论不被改写。",
    }
