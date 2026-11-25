"""地点边界与活动时段的版本更正管理。

更正发生后，受影响的时间窗口、投诉归属和可比较对照范围必须能被查回。
本模块只做只读查询，不修改任何原始记录；版本之间通过
effective_from / effective_to 表达，更正版本带 is_correction 标记。
"""

from __future__ import annotations

from dataclasses import dataclass

from .clock import build_window
from .ingestion import DomainData
from .model import ActivityVersion, SiteVersion


@dataclass(frozen=True)
class CorrectionImpact:
    """一次边界/活动更正的可追溯影响。"""

    version_id: str
    site_id: str
    correction_reason: str
    effective_from: str
    affected_nights: tuple[str, ...]
    added_zones: tuple[str, ...]
    removed_zones: tuple[str, ...]
    affected_complaint_ids: tuple[str, ...]
    affected_reading_ids: tuple[str, ...]
    comparable_nights_before: tuple[str, ...]
    comparable_nights_after: tuple[str, ...]


def activity_version_at(data: DomainData, site_id: str, moment,
                        activity_type: str | None = None
                        ) -> ActivityVersion | None:
    """某地点在某时刻有效的活动（或干预）版本。"""
    candidates = [
        a for a in data.activities
        if a.site_id == site_id
        and (activity_type is None or a.activity_type == activity_type)
        and a.effective_from <= moment
        and (a.effective_to is None or a.effective_to > moment)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda a: a.effective_from)


def intervention_for(data: DomainData, site_id: str) -> ActivityVersion | None:
    """地点上生效中的干预措施。"""
    for activity in data.activities:
        if (activity.site_id == site_id
                and activity.activity_type == "intervention"):
            return activity
    return None


def correction_history(data: DomainData) -> tuple[SiteVersion | ActivityVersion, ...]:
    """全部带更正标记的版本，按生效时间排序。"""
    items = [
        *[v for v in data.site_versions if v.is_correction],
        *[a for a in data.activities if a.is_correction],
    ]
    return tuple(sorted(items, key=lambda x: x.effective_from))


def _prev_site_version(data: DomainData, site_id: str,
                       current: SiteVersion) -> SiteVersion | None:
    earlier = [
        v for v in data.site_versions
        if v.site_id == site_id and v.effective_from < current.effective_from
    ]
    if not earlier:
        return None
    return max(earlier, key=lambda v: v.effective_from)


def correction_impact(data: DomainData, version_id: str,
                      all_nights: list[str]) -> CorrectionImpact:
    """查回一次边界更正影响到的窗口、投诉与读数。"""
    current = next(
        (v for v in data.site_versions if v.version_id == version_id), None)
    if current is None:
        raise KeyError(f"未知边界版本: {version_id}")
    previous = _prev_site_version(data, current.site_id, current)
    prev_zones = set(previous.catchment_zones) if previous else set()
    cur_zones = set(current.catchment_zones)
    added = tuple(sorted(cur_zones - prev_zones))
    removed = tuple(sorted(prev_zones - cur_zones))
    touched_zones = set(added) | set(removed)

    affected_complaints = tuple(
        c.complaint_id for c in data.complaints
        if c.zone_id in touched_zones
        and c.reported_at >= current.effective_from
    )
    affected_readings = tuple(
        r.reading_id for r in data.readings
        if r.site_id == current.site_id
        and r.observed_at >= current.effective_from
    )
    affected_nights = tuple(
        n for n in all_nights
        if build_window(n, data.policy.night_start, data.policy.night_end,
                        data.policy.slot_minutes).ends_at
        > current.effective_from
    )
    before = tuple(n for n in all_nights if n not in affected_nights)
    return CorrectionImpact(
        version_id=current.version_id,
        site_id=current.site_id,
        correction_reason=current.correction_reason or "",
        effective_from=current.effective_from.isoformat(),
        affected_nights=affected_nights,
        added_zones=added,
        removed_zones=removed,
        affected_complaint_ids=affected_complaints,
        affected_reading_ids=affected_readings,
        comparable_nights_before=before,
        comparable_nights_after=affected_nights,
    )


def activity_correction_windows(data: DomainData) -> tuple[dict, ...]:
    """活动时段版本之间的窗口差异，供对照范围查回。"""
    rows: list[dict] = []
    by_activity: dict[str, list[ActivityVersion]] = {}
    for activity in data.activities:
        by_activity.setdefault(activity.activity_id, []).append(activity)
    for activity_id, versions in by_activity.items():
        versions.sort(key=lambda a: a.effective_from)
        for old, new in zip(versions, versions[1:]):
            rows.append({
                "activity_id": activity_id,
                "site_id": new.site_id,
                "from_version": old.version,
                "to_version": new.version,
                "window_before": [old.window_start, old.window_end],
                "window_after": [new.window_start, new.window_end],
                "effective_from": new.effective_from.isoformat(),
                # 相邻版本任一被标记为更正，该版本切换即属更正
                "is_correction": old.is_correction or new.is_correction,
                "reason": new.correction_reason or old.correction_reason,
            })
    return tuple(rows)
