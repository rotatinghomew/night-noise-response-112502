"""投诉去重、事件归并与版本化归属。

重复投诉可能描述同一次扰民事件，不能直接按条数排名。
归并规则（确定性、可解释）：
- 仅 status == received 的投诉参与（withdrawn 等保留但不形成事件）；
- 同一地点、同一夜间窗口内，相邻投诉时间差不超过策略窗口即归并；
- 事件保留全部原始投诉标识，可逐条查回。

地点归属支持边界版本：按事件发生时刻的有效边界版本，
通过 zone_id 重新计算归属地点，与录入地点并列保留。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from .clock import build_window, night_key
from .ingestion import DomainData
from .model import Complaint, NoiseEvent, SiteVersion


def cluster_complaints(complaints: tuple[Complaint, ...],
                       policy_night_start: str,
                       policy_night_end: str,
                       cluster_window_minutes: int,
                       site_resolver=None
                       ) -> tuple[NoiseEvent, ...]:
    """把投诉归并为事件。

    site_resolver: 可选函数 (complaint) -> site_id，
    用于按边界版本重算归属；缺省使用投诉记录上的 site_id。
    """
    received = [c for c in complaints if c.status == "received"]
    groups: dict[tuple[str, str], list[Complaint]] = defaultdict(list)
    for complaint in received:
        site_id = site_resolver(complaint) if site_resolver else complaint.site_id
        night = night_key(complaint.reported_at)
        groups[(site_id, night)].append(complaint)

    events: list[NoiseEvent] = []
    counter = 1
    for (site_id, night) in sorted(groups):
        bucket = sorted(groups[(site_id, night)],
                        key=lambda c: c.reported_at)
        current: list[Complaint] = []
        for complaint in bucket:
            if not current:
                current = [complaint]
                continue
            gap = (complaint.reported_at - current[-1].reported_at)
            if gap <= timedelta(minutes=cluster_window_minutes):
                current.append(complaint)
            else:
                events.append(_make_event(f"ev-{counter:03d}", site_id,
                                          night, current))
                counter += 1
                current = [complaint]
        if current:
            events.append(_make_event(f"ev-{counter:03d}", site_id,
                                      night, current))
            counter += 1
    return tuple(events)


def _make_event(event_id: str, site_id: str, night: str,
                members: list[Complaint]) -> NoiseEvent:
    return NoiseEvent(
        event_id=event_id,
        site_id=site_id,
        zone_id=members[0].zone_id,
        night=night,
        first_reported_at=members[0].reported_at,
        last_reported_at=members[-1].reported_at,
        complaint_ids=tuple(c.complaint_id for c in members),
        channels=tuple(sorted({c.channel for c in members})),
        text_keys=tuple(sorted({c.text_key for c in members})),
    )


# ---------------------------------------------------------------------------
# 版本化边界归属
# ---------------------------------------------------------------------------

def version_at(site_versions: tuple[SiteVersion, ...], site_id: str,
               moment) -> SiteVersion | None:
    """返回某地点在某时刻有效的边界版本。"""
    candidates = [
        v for v in site_versions
        if v.site_id == site_id
        and v.effective_from <= moment
        and (v.effective_to is None or v.effective_to > moment)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda v: v.effective_from)


def resolve_site_by_zone(data: DomainData, moment,
                         zone_id: str | None) -> str | None:
    """按时刻有效的边界版本，由分区反查归属地点。"""
    if zone_id is None:
        return None
    for site_id in data.site_ids:
        version = version_at(data.site_versions, site_id, moment)
        if version and zone_id in version.catchment_zones:
            return site_id
    return None


def attribution_resolver(data: DomainData):
    """生成投诉归属解析器：优先按分区+有效版本重算，失败则回退记录地点。"""

    def resolve(complaint: Complaint) -> str:
        resolved = resolve_site_by_zone(data, complaint.reported_at,
                                        complaint.zone_id)
        return resolved or complaint.recorded_site_id

    return resolve


def attribution_changes(data: DomainData) -> tuple[dict, ...]:
    """列出在当前（最新）边界版本下归属发生变化的投诉。"""
    resolver = attribution_resolver(data)
    changes = []
    for complaint in data.complaints:
        current_site = resolver(complaint)
        if current_site != complaint.recorded_site_id:
            changes.append({
                "complaint_id": complaint.complaint_id,
                "recorded_site_id": complaint.recorded_site_id,
                "resolved_site_id": current_site,
                "zone_id": complaint.zone_id,
                "reported_at": complaint.reported_at.isoformat(),
            })
    return tuple(changes)


def events_for_night(events: tuple[NoiseEvent, ...], site_id: str,
                     night: str) -> tuple[NoiseEvent, ...]:
    return tuple(
        e for e in events if e.site_id == site_id and e.night == night
    )


def nights_with_complaints(complaints: tuple[Complaint, ...]) -> list[str]:
    return sorted({night_key(c.reported_at) for c in complaints})


def window_for(night: str, data: DomainData):
    return build_window(night, data.policy.night_start,
                        data.policy.night_end, data.policy.slot_minutes)
