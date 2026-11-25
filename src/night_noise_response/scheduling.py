"""巡查与告知的资源协调。

关键规则：
- completed / confirmed 的安排冻结，保留当时依据（basis_snapshot_id），
  新读数只能改变未来建议，不能改写历史；
- 仅 proposed 且尚未开始的安排允许重新评估；
- 同一队伍或设备在同一时段被两个地点占用即为冲突，
  冲突要明确指出哪些建议因此不能兑现；
- 资质、夜间时段和地点间路程都参与可行性判断。
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import datetime, timedelta

from .clock import build_window, overlap, parse_hm
from .ingestion import DomainData
from .model import (
    Operation,
    OperationStatus,
    Plan,
    Recommendation,
    Resource,
    RiskBand,
    RiskRange,
)


@dataclass(frozen=True)
class Assignment:
    staff: str
    equipment: str | None
    starts_at: datetime
    ends_at: datetime
    travel_minutes: int


# ---------------------------------------------------------------------------
# 冲突检测
# ---------------------------------------------------------------------------

def detect_conflicts(operations: tuple[Operation, ...]) -> tuple[dict, ...]:
    """检测队伍/设备在时间上的双重占用。"""
    conflicts: list[dict] = []
    for a, b in itertools.combinations(operations, 2):
        if a.status is OperationStatus.CANCELLED or b.status is OperationStatus.CANCELLED:
            continue
        overlapping = overlap(a.starts_at, a.ends_at, b.starts_at, b.ends_at)
        shared = []
        if a.staff and a.staff == b.staff:
            shared.append(("staff", a.staff))
        if a.equipment and a.equipment == b.equipment:
            shared.append(("equipment", a.equipment))
        if overlapping and shared:
            conflicts.append({
                "operation_ids": [a.operation_id, b.operation_id],
                "site_ids": [a.site_id, b.site_id],
                "shared_resources": [
                    {"resource_type": kind, "resource_id": rid}
                    for kind, rid in shared
                ],
                "window": [
                    max(a.starts_at, b.starts_at).isoformat(),
                    min(a.ends_at, b.ends_at).isoformat(),
                ],
                "statuses": [a.status.value, b.status.value],
            })
    return tuple(conflicts)


def _resource_map(data: DomainData) -> dict[str, Resource]:
    return {r.resource_id: r for r in data.resources}


def _qualified(data: DomainData, resource_id: str | None,
               required: str | None) -> bool:
    if required is None or resource_id is None:
        return True
    resource = _resource_map(data).get(resource_id)
    return bool(resource and required in resource.qualifications)


def _travel_minutes(data: DomainData, from_site: str, to_site: str) -> int:
    table = data.travel_times
    if from_site in table and to_site in table[from_site]:
        return int(table[from_site][to_site])
    return 0


# ---------------------------------------------------------------------------
# 规划
# ---------------------------------------------------------------------------

def _notice_window(data: DomainData, night: str) -> tuple[datetime, datetime]:
    window = build_window(night, data.policy.night_start,
                          data.policy.night_end, data.policy.slot_minutes)
    sh, sm = parse_hm(data.policy.notice_window_start)
    eh, em = parse_hm(data.policy.notice_window_end)
    start = window.starts_at.replace(hour=sh, minute=sm)
    end = window.starts_at.replace(hour=eh, minute=em)
    return start, end


def _is_future(op: Operation, as_of: datetime) -> bool:
    return op.starts_at >= as_of


def split_operations(data: DomainData, as_of: datetime
                     ) -> tuple[tuple[Operation, ...], tuple[Operation, ...],
                                tuple[Operation, ...]]:
    """拆分为冻结安排、可重新评估安排与已过期失效安排。"""
    frozen: list[Operation] = []
    replannable: list[Operation] = []
    expired: list[Operation] = []
    for op in data.operations:
        if op.status is OperationStatus.CANCELLED:
            expired.append(op)
        elif op.status.is_frozen:
            # 已确认/已完成一律冻结；已结束的安排保留依据但不参与排期
            frozen.append(op)
        elif op.status is OperationStatus.PROPOSED:
            if _is_future(op, as_of):
                replannable.append(op)
            else:
                expired.append(op)
    return tuple(frozen), tuple(replannable), tuple(expired)


def _busy_intervals(operations: tuple[Operation, ...]
                    ) -> dict[str, list[tuple[datetime, datetime, str]]]:
    busy: dict[str, list[tuple[datetime, datetime, str]]] = {}
    for op in operations:
        for rid in (op.staff, op.equipment):
            if rid:
                busy.setdefault(rid, []).append(
                    (op.starts_at, op.ends_at, op.operation_id))
    return busy


def _site_patrol_window(data: DomainData, site_id: str, night: str
                        ) -> tuple[datetime, datetime]:
    """地点允许到场巡查的时段；缺省为整个夜间窗口。"""
    window = build_window(night, data.policy.night_start,
                          data.policy.night_end, data.policy.slot_minutes)
    site = next((s for s in data.sites if s["site_id"] == site_id), None)
    if not site or "patrol_window_start" not in site:
        return window.starts_at, window.ends_at
    sh, sm = parse_hm(site["patrol_window_start"])
    eh, em = parse_hm(site["patrol_window_end"])
    start = window.starts_at.replace(hour=sh, minute=sm)
    if (eh, em) <= (sh, sm):
        end = (start + timedelta(days=1)).replace(hour=eh, minute=em)
    else:
        end = start.replace(hour=eh, minute=em)
    return start, end


def _find_assignment(data: DomainData, site_id: str, kind: str,
                     night: str, duration: int,
                     busy: dict[str, list[tuple[datetime, datetime, str]]],
                     pinned: tuple[Operation, ...]) -> Assignment | None:
    """在满足资质、时段与路程约束下寻找可行资源安排。"""
    teams = [r for r in data.resources if r.resource_type == "team"]
    needed_qual = ("night_patrol" if kind in ("patrol", "revisit")
                   else "community_notice")
    candidates = [t for t in teams if needed_qual in t.qualifications]

    if kind == "notice":
        win_start, win_end = _notice_window(data, night)
        search_starts = [win_start]
    else:
        # 巡查在地点允许的到场时段内逐槽位尝试
        win_start, win_end = _site_patrol_window(data, site_id, night)
        window = build_window(night, data.policy.night_start,
                              data.policy.night_end,
                              data.policy.slot_minutes)
        search_starts = [s for s in window.slots if s >= win_start]

    for team in sorted(candidates, key=lambda t: t.resource_id):
        equipment = None
        if kind in ("patrol", "revisit"):
            equipment = _find_equipment(data, busy)
        for start in search_starts:
            end = start + timedelta(minutes=duration)
            if end > win_end:
                continue
            if not _resource_free(team.resource_id, start, end, busy):
                continue
            if equipment and not _resource_free(equipment, start, end, busy):
                continue
            travel = _travel_from_last_site(team.resource_id, site_id,
                                            start, pinned, data)
            if travel is None:
                continue
            return Assignment(
                staff=team.resource_id, equipment=equipment,
                starts_at=start, ends_at=end, travel_minutes=travel)
    return None


def _find_equipment(data: DomainData,
                    busy: dict[str, list[tuple[datetime, datetime, str]]]
                    ) -> str | None:
    for resource in sorted(data.resources, key=lambda r: r.resource_id):
        if resource.resource_type == "equipment":
            return resource.resource_id
    return None


def _resource_free(resource_id: str, start: datetime, end: datetime,
                   busy: dict[str, list[tuple[datetime, datetime, str]]]
                   ) -> bool:
    for b_start, b_end, _ in busy.get(resource_id, ()):
        if overlap(start, end, b_start, b_end):
            return False
    return True


def _travel_from_last_site(team_id: str, target_site: str, start: datetime,
                           pinned: tuple[Operation, ...],
                           data: DomainData) -> int | None:
    """检查从上一地点出发的路程是否来得及。"""
    previous = [
        op for op in pinned
        if op.staff == team_id and op.ends_at <= start
    ]
    if not previous:
        resource = _resource_map(data).get(team_id)
        from_site = resource.home_site if resource else target_site
        return _travel_minutes(data, from_site, target_site)
    last = max(previous, key=lambda op: op.ends_at)
    travel = _travel_minutes(data, last.site_id, target_site)
    available = (start - last.ends_at).total_seconds() / 60
    # 必须留足完整路程时间，不接受压线零缓冲
    if available < travel:
        return None
    return travel


def _kind_duration(data: DomainData, kind: str) -> int:
    if kind == "notice":
        return data.policy.notice_duration_minutes
    return data.policy.patrol_duration_minutes


def _desired_kind(band: RiskBand) -> tuple[str, str]:
    if band is RiskBand.HIGH:
        return "patrol", "风险带上界高且证据充分，安排巡查"
    if band is RiskBand.UNKNOWN:
        return "patrol", "没有有效观察证据，安排巡查现场核实"
    if band is RiskBand.MODERATE:
        return "notice", "风险带中等，优先居民告知"
    return "none", "风险带低，暂不新增行动"


def build_plan(data: DomainData,
               latest_risks: dict[str, RiskRange],
               target_night: str,
               as_of: datetime,
               plan_id: str | None = None) -> Plan:
    """生成下一轮巡查与告知规划。"""
    frozen, replannable, expired = split_operations(data, as_of)
    # 只有仍在未来的冻结安排占用资源；历史安排保留依据但不阻塞排期
    active_frozen = tuple(op for op in frozen if _is_future(op, as_of))
    busy = _busy_intervals(active_frozen)
    pinned_ops = list(active_frozen)
    recommendations: list[Recommendation] = []
    blockers: list[str] = []
    snapshot_ids: list[str] = []
    notes: list[str] = []

    # 1) 冻结安排先形成建议（保留当时依据，不重新评估）
    for op in active_frozen:
        risk = latest_risks.get(op.site_id)
        rec = Recommendation(
            recommendation_id=f"rec-pin-{op.operation_id}",
            site_id=op.site_id,
            kind=op.kind,
            reason="已确认/已完成安排，依据冻结，不重新评估",
            risk_band=risk.band if risk else RiskBand.UNKNOWN,
            risk_range=(risk.lower, risk.upper) if risk else (0.0, 0.0),
            feasible=True,
            blockers=(),
            assignment={
                "staff": op.staff,
                "equipment": op.equipment,
                "starts_at": op.starts_at.isoformat(),
                "ends_at": op.ends_at.isoformat(),
                "operation_id": op.operation_id,
            },
            basis_snapshot_id=op.basis_snapshot_id,
            source="pinned_operation",
        )
        recommendations.append(rec)
        if op.basis_snapshot_id:
            snapshot_ids.append(op.basis_snapshot_id)

    # 2) 可重新评估的旧 proposed：先释放，再按当前风险决定
    for op in replannable:
        notes.append(
            f"安排 {op.operation_id} 尚未确认，按当前风险重新评估"
        )

    # 3) 按当前风险带生成新建议，高风险优先
    ordered = sorted(
        latest_risks.values(),
        key=lambda r: (-r.band.rank, -r.upper, r.site_id))
    counter = itertools.count(1)
    for risk in ordered:
        kind, reason = _desired_kind(risk.band)
        if kind == "none":
            recommendations.append(Recommendation(
                recommendation_id=f"rec-new-{next(counter):03d}",
                site_id=risk.site_id, kind="none", reason=reason,
                risk_band=risk.band, risk_range=(risk.lower, risk.upper),
                feasible=True, blockers=(), assignment=None,
                basis_snapshot_id=None, source="risk_engine"))
            continue
        if risk.provisional:
            reason += "（观察成熟度不足，结论为暂定）"
        assignment = _find_assignment(
            data, risk.site_id, kind, target_night,
            _kind_duration(data, kind), busy, tuple(pinned_ops))
        rec_id = f"rec-new-{next(counter):03d}"
        if assignment is None:
            blocker = (f"{risk.site_id} 的 {kind} 无可行资源："
                       "队伍资质/时段/路程或设备占用冲突")
            blockers.append(blocker)
            recommendations.append(Recommendation(
                recommendation_id=rec_id,
                site_id=risk.site_id, kind=kind, reason=reason,
                risk_band=risk.band,
                risk_range=(risk.lower, risk.upper),
                feasible=False, blockers=(blocker,),
                assignment=None, basis_snapshot_id=None,
                source="risk_engine"))
            continue
        busy.setdefault(assignment.staff, []).append(
            (assignment.starts_at, assignment.ends_at, rec_id))
        if assignment.equipment:
            busy.setdefault(assignment.equipment, []).append(
                (assignment.starts_at, assignment.ends_at, rec_id))
        recommendations.append(Recommendation(
            recommendation_id=rec_id,
            site_id=risk.site_id, kind=kind, reason=reason,
            risk_band=risk.band,
            risk_range=(risk.lower, risk.upper),
            feasible=True, blockers=(),
            assignment={
                "staff": assignment.staff,
                "equipment": assignment.equipment,
                "starts_at": assignment.starts_at.isoformat(),
                "ends_at": assignment.ends_at.isoformat(),
                "travel_minutes": assignment.travel_minutes,
            },
            basis_snapshot_id=None, source="risk_engine"))

    # 4) 冻结安排之间的既有冲突也要暴露（含历史安排，便于追责查回）
    conflicts = detect_conflicts(frozen)
    if conflicts:
        for conflict in conflicts:
            notes.append(
                "既有安排冲突：" + ", ".join(conflict["operation_ids"])
                + " 占用同一资源，相关建议无法同时兑现"
            )
    for op in expired:
        notes.append(
            f"安排 {op.operation_id} 已过期且未确认，本轮不再安排"
        )

    return Plan(
        plan_id=plan_id or f"plan-{as_of.strftime('%Y%m%d%H%M')}",
        as_of=as_of,
        target_night=target_night,
        recommendations=tuple(recommendations),
        conflicts=conflicts,
        frozen_operations=tuple(op.operation_id for op in frozen),
        replanned_operations=tuple(op.operation_id for op in replannable),
        snapshot_ids=tuple(snapshot_ids),
        notes=tuple(notes),
    )
