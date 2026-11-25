"""夜间噪声证据与巡查资源复议领域包。

模块分工：
- clock: 夜间窗口与槽位时间
- model: 显式状态枚举与不可变值对象
- ingestion: 资料加载、契约校验与记录哈希
- observations: 观察机会、缺口与成熟度核算
- complaints: 投诉去重、事件归并与版本化归属
- versions: 边界与活动版本更正追溯
- risk_ranges: 可解释风险区间（非总分）
- intervention: 干预效果区间评估与回访对比
- scheduling: 巡查/告知资源协调与冲突检测
- audit: 证据快照、防篡改与追查链
- pipeline: 端到端流水线与 CLI
"""

from __future__ import annotations

from .ingestion import DomainData, load_domain
from .model import (
    EffectEstimate,
    NightObservation,
    NoiseEvent,
    Operation,
    OperationStatus,
    Plan,
    Policy,
    Quality,
    Recommendation,
    RiskBand,
    RiskRange,
    SlotState,
)

__all__ = [
    "DomainData",
    "load_domain",
    "EffectEstimate",
    "NightObservation",
    "NoiseEvent",
    "Operation",
    "OperationStatus",
    "Plan",
    "Policy",
    "Quality",
    "Recommendation",
    "RiskBand",
    "RiskRange",
    "SlotState",
]

__version__ = "0.2.0"
