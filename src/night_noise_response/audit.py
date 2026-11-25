"""审计链与快照管理。

管理者需要从当前建议追到：
1. 采用的事实（原始读数、投诉、气象、操作记录及其哈希）；
2. 观察缺口（无记录/离线/维护/部分覆盖的槽位）；
3. 前后版本（边界与活动更正）；
4. 已完成安排当时的依据（冻结快照）。

快照在规划时刻生成并带哈希；后续资料变化不会改写历史快照，
只产生新快照。回访数据进入后与原干预解释并列比较。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from .ingestion import DomainData, canonical_hash
from .model import EffectEstimate, Operation, OperationStatus, Plan, RiskRange


@dataclass(frozen=True)
class Snapshot:
    """规划时刻的证据快照（不可变）。"""

    snapshot_id: str
    created_at: datetime
    as_of: datetime
    target_night: str
    record_hashes: dict
    risk_ranges: tuple[dict, ...]
    effect_estimates: tuple[dict, ...]
    plan_summary: dict
    digest: str

    def to_dict(self) -> dict:
        return {
            "snapshot_id": self.snapshot_id,
            "created_at": self.created_at.isoformat(),
            "as_of": self.as_of.isoformat(),
            "target_night": self.target_night,
            "record_hashes": self.record_hashes,
            "risk_ranges": list(self.risk_ranges),
            "effect_estimates": list(self.effect_estimates),
            "plan_summary": self.plan_summary,
            "digest": self.digest,
        }


def _risk_to_dict(risk: RiskRange) -> dict:
    return {
        "site_id": risk.site_id,
        "night": risk.night,
        "range": [risk.lower, risk.upper],
        "band": risk.band.value,
        "provisional": risk.provisional,
        "mature": risk.mature,
        "valid_ratio": risk.valid_ratio,
        "state_counts": risk.state_counts,
        "event_count": risk.event_count,
        "raw_complaint_count": risk.raw_complaint_count,
        "evidence_gap_ids": list(risk.evidence_gap_ids),
        "basis": risk.basis,
    }


def _effect_to_dict(effect: EffectEstimate) -> dict:
    return {
        "site_id": effect.site_id,
        "intervention_id": effect.intervention_id,
        "pre_nights": list(effect.pre_nights),
        "post_nights": list(effect.post_nights),
        "excluded_nights": list(effect.excluded_nights),
        "pre_rate_range": list(effect.pre_rate_range),
        "post_rate_range": list(effect.post_rate_range),
        "effect_range": list(effect.effect_range),
        "event_rate_pre": effect.event_rate_pre,
        "event_rate_post": effect.event_rate_post,
        "control_rate_range": (list(effect.control_rate_range)
                               if effect.control_rate_range else None),
        "did_range": list(effect.did_range) if effect.did_range else None,
        "verdict": effect.verdict,
        "maturity_notes": list(effect.maturity_notes),
        "basis": effect.basis,
    }


def _plan_summary(plan: Plan) -> dict:
    return {
        "plan_id": plan.plan_id,
        "recommendations": [
            {
                "recommendation_id": r.recommendation_id,
                "site_id": r.site_id,
                "kind": r.kind,
                "feasible": r.feasible,
                "blockers": list(r.blockers),
                "source": r.source,
            }
            for r in plan.recommendations
        ],
        "conflicts": list(plan.conflicts),
        "frozen_operations": list(plan.frozen_operations),
        "replanned_operations": list(plan.replanned_operations),
        "notes": list(plan.notes),
    }


def make_snapshot(data: DomainData, plan: Plan,
                  risks: dict[str, dict[str, RiskRange]],
                  effects: tuple[EffectEstimate, ...],
                  created_at: datetime | None = None) -> Snapshot:
    """在规划时刻固化证据快照。"""
    created_at = created_at or datetime.now()
    snapshot_id = f"snap-{plan.target_night}-{plan.as_of.strftime('%H%M')}"
    risk_dicts = tuple(
        _risk_to_dict(risk)
        for per_night in risks.values()
        for risk in per_night.values()
    )
    effect_dicts = tuple(_effect_to_dict(e) for e in effects)
    plan_summary = _plan_summary(plan)
    payload = {
        "record_hashes": data.record_hashes,
        "risk_ranges": risk_dicts,
        "effect_estimates": effect_dicts,
        "plan_summary": plan_summary,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return Snapshot(
        snapshot_id=snapshot_id,
        created_at=created_at,
        as_of=plan.as_of,
        target_night=plan.target_night,
        record_hashes=data.record_hashes,
        risk_ranges=risk_dicts,
        effect_estimates=effect_dicts,
        plan_summary=plan_summary,
        digest=digest,
    )


def verify_record_hashes(data: DomainData,
                         baseline: dict | None = None) -> dict[str, list[str]]:
    """核对原始记录哈希，返回被改动的记录标识。

    baseline 为外部固化的记录哈希（通常取自历史快照）。
    若不传 baseline，则以本次加载时保存的 record_hashes 为基线，
    此时只能用于校验内存中 raw 未被业务代码改写；
    要检测磁盘资料被篡改，必须传入历史快照的 record_hashes。
    """
    mismatches: dict[str, list[str]] = {}
    filename_map = {
        "readings": ("sensors.json", "reading_id"),
        "complaints": ("complaints.json", "complaint_id"),
        "operations": ("operations.json", "operation_id"),
        "weather": ("weather.json", "weather_id"),
        "activities": ("activities.json", "activity_id"),
        "site_versions": ("site_versions.json", "version_id"),
        "resources": ("resources.json", "resource_id"),
    }
    current: dict[str, dict[str, str]] = {}
    for attr, (filename, id_attr) in filename_map.items():
        rows = getattr(data, attr)
        current[filename] = {
            getattr(row, id_attr): canonical_hash(row.raw) for row in rows
        }
    reference = baseline if baseline is not None else data.record_hashes
    for filename, hashes in current.items():
        baseline_hashes = reference.get(filename, {})
        changed = [
            ident for ident, digest in hashes.items()
            if baseline_hashes.get(ident) != digest
        ]
        missing = [
            ident for ident in baseline_hashes
            if ident not in hashes
        ]
        problems = sorted(set(changed) | set(missing))
        if problems:
            mismatches[filename] = problems
    return mismatches


def verify_against_snapshot(data: DomainData,
                            snapshot: Snapshot) -> dict[str, list[str]]:
    """用历史快照固化的哈希核对当前资料，检测磁盘篡改。"""
    return verify_record_hashes(data, baseline=snapshot.record_hashes)


def verify_snapshot(snapshot: Snapshot) -> bool:
    """重新计算快照摘要，判断快照本身是否被篡改。"""
    payload = {
        "record_hashes": snapshot.record_hashes,
        "risk_ranges": list(snapshot.risk_ranges),
        "effect_estimates": list(snapshot.effect_estimates),
        "plan_summary": snapshot.plan_summary,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return digest == snapshot.digest


def trace_recommendation(plan: Plan, recommendation_id: str,
                         data: DomainData,
                         risks: dict[str, dict[str, RiskRange]],
                         snapshots: tuple[Snapshot, ...]) -> dict:
    """从一条建议追到事实、观察缺口、版本与快照。"""
    rec = next((r for r in plan.recommendations
                if r.recommendation_id == recommendation_id), None)
    if rec is None:
        raise KeyError(f"未知建议: {recommendation_id}")
    risk = risks.get(rec.site_id, {}).get(plan.target_night)
    related_ops = [
        op.operation_id for op in data.operations
        if op.site_id == rec.site_id
    ]
    related_complaints = [
        c.complaint_id for c in data.complaints
        if c.site_id == rec.site_id or c.recorded_site_id == rec.site_id
    ]
    related_versions = [
        v.version_id for v in data.site_versions
        if v.site_id == rec.site_id
    ]
    used_snapshot = next(
        (s.snapshot_id for s in snapshots
         if s.target_night == plan.target_night), None)
    return {
        "recommendation_id": rec.recommendation_id,
        "site_id": rec.site_id,
        "kind": rec.kind,
        "feasible": rec.feasible,
        "blockers": list(rec.blockers),
        "source": rec.source,
        "risk_facts": _risk_to_dict(risk) if risk else None,
        "evidence_gaps": list(risk.evidence_gap_ids) if risk else [],
        "fact_records": {
            "operations": related_ops,
            "complaints": related_complaints,
            "site_versions": related_versions,
        },
        "snapshot_id": used_snapshot,
    }


def reconcile_follow_up(operation: Operation, data: DomainData,
                        night_risks: dict[str, RiskRange]) -> dict:
    """回访/巡查结果返回后的对账：原依据保留，实际变化并列。"""
    if operation.status is not OperationStatus.COMPLETED:
        raise ValueError("只有已完成安排可以做回访对账")
    risk = night_risks.get(
        operation.raw.get("night") or _night_of(operation.starts_at))
    return {
        "operation_id": operation.operation_id,
        "site_id": operation.site_id,
        "frozen_basis_snapshot_id": operation.basis_snapshot_id,
        "recorded_result": operation.result,
        "current_risk": _risk_to_dict(risk) if risk else None,
        "basis_rewritten": False,
        "note": "已完成安排的当时依据保持不变，回访结果作为新事实追加。",
    }


def _night_of(moment: datetime) -> str:
    from .clock import night_key

    return night_key(moment)
