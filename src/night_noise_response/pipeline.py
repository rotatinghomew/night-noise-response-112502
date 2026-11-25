"""端到端分析流水线与命令行入口。

把资料加载、观察核算、投诉归并、风险区间、干预评估、
资源规划和证据快照串成一次可复现运行，输出结构化报告。
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path

from .audit import (
    make_snapshot,
    trace_recommendation,
    verify_against_snapshot,
)
from .clock import night_key
from .complaints import (
    attribution_changes,
    attribution_resolver,
    cluster_complaints,
)
from .ingestion import DomainData, load_domain
from .intervention import evaluate_intervention
from .observations import build_observations
from .risk_ranges import (
    assess_all,
    latest_risk,
    opportunity_bias_summary,
    rank_sites,
)
from .scheduling import build_plan, detect_conflicts
from .versions import (
    activity_correction_windows,
    correction_history,
    correction_impact,
)


def _default_nights(data: DomainData) -> list[str]:
    nights = {night_key(r.observed_at) for r in data.readings}
    nights |= {night_key(c.reported_at) for c in data.complaints}
    return sorted(nights)


def run_pipeline(data_root: str | Path,
                 as_of: datetime | None = None,
                 target_night: str | None = None,
                 control_site_id: str | None = None) -> dict:
    """执行完整流水线，返回可 JSON 序列化的报告字典。"""
    data = load_domain(data_root)
    as_of = as_of or datetime.fromisoformat("2026-10-08T12:00")
    nights = _default_nights(data)
    target_night = target_night or (nights[-1] if nights else None)

    # 1) 观察机会核算（缺失/离线/维护/达标严格区分）
    observations = build_observations(data, nights)

    # 2) 投诉按当前边界版本归属并归并为事件
    resolver = attribution_resolver(data)
    events = cluster_complaints(
        data.complaints, data.policy.night_start, data.policy.night_end,
        data.policy.cluster_window_minutes, resolver)

    # 3) 风险区间（非总分，保留不确定范围）
    risks = assess_all(data, observations, events, as_of)
    latest = {site_id: latest_risk(per_night)
              for site_id, per_night in risks.items()}
    latest = {k: v for k, v in latest.items() if v is not None}
    ranking = rank_sites(latest)

    # 4) 干预效果区间评估
    effects = []
    intervention_sites = [
        a.site_id for a in data.activities
        if a.activity_type == "intervention"
    ]
    for site_id in intervention_sites:
        effects.append(evaluate_intervention(
            data, site_id, nights, observations, events,
            control_site_id=control_site_id))

    # 5) 资源规划与冲突
    plan = build_plan(data, latest, target_night, as_of)
    conflicts_all = detect_conflicts(data.operations)

    # 6) 证据快照与审计（用快照固化的哈希核对当前资料）
    snapshot = make_snapshot(data, plan, risks, tuple(effects))
    hash_check = verify_against_snapshot(data, snapshot)

    report = {
        "generated_at": as_of.isoformat(),
        "target_night": target_night,
        "nights": nights,
        "opportunity_bias": opportunity_bias_summary(
            data, observations, events),
        "noise_events": [_event_dict(e) for e in events],
        "attribution_changes": list(attribution_changes(data)),
        "risk_ranking": [_risk_brief(r) for r in ranking],
        "risk_detail": {
            site_id: {night: _risk_full(risk)
                      for night, risk in per_night.items()}
            for site_id, per_night in risks.items()
        },
        "intervention_effects": [_effect_dict(e) for e in effects],
        "plan": _plan_dict(plan),
        "all_operation_conflicts": list(conflicts_all),
        "correction_history": [_version_dict(v)
                               for v in correction_history(data)],
        "activity_corrections": list(activity_correction_windows(data)),
        "snapshot": snapshot.to_dict(),
        "integrity": {
            "tampered_records": hash_check,
            "snapshot_digest": snapshot.digest,
        },
    }
    return report


def run_with_trace(data_root: str | Path, recommendation_id: str,
                   as_of: datetime | None = None,
                   target_night: str | None = None) -> dict:
    """运行流水线并追加某条建议的完整追查链。"""
    report = run_pipeline(data_root, as_of, target_night)
    data = load_domain(data_root)
    as_of = as_of or datetime.fromisoformat("2026-10-08T12:00")
    nights = _default_nights(data)
    target_night = target_night or nights[-1]
    observations = build_observations(data, nights)
    resolver = attribution_resolver(data)
    events = cluster_complaints(
        data.complaints, data.policy.night_start, data.policy.night_end,
        data.policy.cluster_window_minutes, resolver)
    risks = assess_all(data, observations, events, as_of)
    latest = {site_id: latest_risk(per_night)
              for site_id, per_night in risks.items()}
    plan = build_plan(data, {k: v for k, v in latest.items() if v},
                      target_night, as_of)
    snapshot = make_snapshot(data, plan, risks, ())
    report["trace"] = trace_recommendation(
        plan, recommendation_id, data, risks, (snapshot,))
    return report


def correction_report(data_root: str | Path, version_id: str) -> dict:
    """单次边界更正的影响查回。"""
    data = load_domain(data_root)
    nights = _default_nights(data)
    impact = correction_impact(data, version_id, nights)
    return _to_jsonable(impact)


# ---------------------------------------------------------------------------
# 序列化辅助
# ---------------------------------------------------------------------------

def _to_jsonable(value):
    if is_dataclass(value):
        return _to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    return value


def _event_dict(event) -> dict:
    return {
        "event_id": event.event_id,
        "site_id": event.site_id,
        "zone_id": event.zone_id,
        "night": event.night,
        "first_reported_at": event.first_reported_at.isoformat(),
        "last_reported_at": event.last_reported_at.isoformat(),
        "complaint_ids": list(event.complaint_ids),
        "complaint_count": event.complaint_count,
        "channels": list(event.channels),
        "text_keys": list(event.text_keys),
    }


def _risk_brief(risk) -> dict:
    return {
        "site_id": risk.site_id,
        "night": risk.night,
        "range": [risk.lower, risk.upper],
        "band": risk.band.value,
        "provisional": risk.provisional,
        "event_count": risk.event_count,
        "raw_complaint_count": risk.raw_complaint_count,
    }


def _risk_full(risk) -> dict:
    return {
        "site_id": risk.site_id,
        "night": risk.night,
        "range": [risk.lower, risk.upper],
        "band": risk.band.value,
        "provisional": risk.provisional,
        "mature": risk.mature,
        "valid_ratio": risk.valid_ratio,
        "slot_count": risk.slot_count,
        "state_counts": risk.state_counts,
        "event_count": risk.event_count,
        "raw_complaint_count": risk.raw_complaint_count,
        "evidence_gap_ids": list(risk.evidence_gap_ids),
        "basis": risk.basis,
    }


def _effect_dict(effect) -> dict:
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


def _plan_dict(plan) -> dict:
    return {
        "plan_id": plan.plan_id,
        "as_of": plan.as_of.isoformat(),
        "target_night": plan.target_night,
        "recommendations": [_to_jsonable(r) for r in plan.recommendations],
        "conflicts": list(plan.conflicts),
        "frozen_operations": list(plan.frozen_operations),
        "replanned_operations": list(plan.replanned_operations),
        "snapshot_ids": list(plan.snapshot_ids),
        "notes": list(plan.notes),
    }


def _version_dict(version) -> dict:
    ident = getattr(version, "version_id", None) or (
        f"{version.activity_id}:{version.version}")
    return {
        "version_id": ident,
        "site_id": version.site_id,
        "boundary_version": getattr(version, "boundary_version", None),
        "activity_type": getattr(version, "activity_type", None),
        "is_correction": version.is_correction,
        "correction_reason": version.correction_reason,
        "effective_from": version.effective_from.isoformat(),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="夜间噪声证据与巡查资源复议流水线")
    parser.add_argument("--data", default="data", help="资料包目录")
    parser.add_argument("--as-of", default="2026-10-08T12:00")
    parser.add_argument("--target-night", default="2026-10-07")
    parser.add_argument("--control", default=None, help="对照地点")
    parser.add_argument("--trace", default=None, help="追查某条建议")
    parser.add_argument("--correction", default=None, help="查回某次边界更正")
    parser.add_argument("--output", default=None, help="报告输出文件")
    args = parser.parse_args(argv)

    if args.correction:
        report = correction_report(args.data, args.correction)
    else:
        as_of = datetime.fromisoformat(args.as_of)
        if args.trace:
            report = run_with_trace(args.data, args.trace, as_of,
                                    args.target_night)
        else:
            report = run_pipeline(args.data, as_of, args.target_night,
                                  args.control)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
