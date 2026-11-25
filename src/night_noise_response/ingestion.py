"""资料加载与输入校验。

在 input_contract.load_bundle 之上做结构化解析与契约校验：
- 必填字段、枚举值、时间格式错误直接报错，不静默填充；
- 保留每条原始记录（raw），后续所有分析都能回溯；
- 提供稳定的规范化哈希，供审计链使用。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .clock import parse_ts
from .input_contract import load_bundle
from .model import (
    ActivityVersion,
    Complaint,
    Operation,
    OperationStatus,
    Policy,
    Quality,
    Reading,
    Resource,
    SiteVersion,
    WeatherObs,
)

OPTIONAL_FILES = (
    "weather.json",
    "activities.json",
    "site_versions.json",
    "resources.json",
    "travel_times.json",
    "policy.json",
)

READING_QUALITIES = {q.value for q in Quality}
OPERATION_KINDS = {"patrol", "notice", "revisit"}


def canonical_hash(record: dict) -> str:
    """对原始记录计算稳定的 SHA-256 摘要。"""
    blob = json.dumps(record, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _require(record: dict, field: str, record_type: str) -> object:
    if field not in record:
        raise ValueError(f"{record_type} 记录缺少必填字段 {field}: {record}")
    return record[field]


def _parse_reading(record: dict) -> Reading:
    quality_value = _require(record, "quality", "sensor")
    if quality_value not in READING_QUALITIES:
        raise ValueError(f"未知传感器质量状态: {quality_value}")
    quality = Quality(quality_value)
    db = record.get("db")
    if quality is Quality.VALID and not isinstance(db, (int, float)):
        raise ValueError(f"valid 读数必须携带数值 db: {record}")
    if quality is Quality.MAINTENANCE and db is not None:
        raise ValueError(f"maintenance 读数不应携带数值 db: {record}")
    if quality is Quality.OFFLINE and db is not None:
        raise ValueError(f"offline 读数不应携带数值 db: {record}")
    # partial 允许携带参考数值（原始资料即如此），但不单独判定超标
    return Reading(
        reading_id=str(_require(record, "reading_id", "sensor")),
        site_id=str(_require(record, "site_id", "sensor")),
        sensor_id=str(record.get("sensor_id", "main")),
        observed_at=parse_ts(str(_require(record, "observed_at", "sensor"))),
        db=None if db is None else int(db),
        quality=quality,
        raw=dict(record),
    )


def _parse_complaint(record: dict) -> Complaint:
    return Complaint(
        complaint_id=str(_require(record, "complaint_id", "complaint")),
        site_id=str(_require(record, "site_id", "complaint")),
        recorded_site_id=str(record.get("recorded_site_id", record["site_id"])),
        zone_id=record.get("zone_id"),
        reported_at=parse_ts(str(_require(record, "reported_at", "complaint"))),
        channel=str(_require(record, "channel", "complaint")),
        text_key=str(_require(record, "text_key", "complaint")),
        status=str(_require(record, "status", "complaint")),
        raw=dict(record),
    )


def _parse_operation(record: dict) -> Operation:
    kind = str(_require(record, "kind", "operation"))
    if kind not in OPERATION_KINDS:
        raise ValueError(f"未知巡查动作类型: {kind}")
    status_value = str(_require(record, "status", "operation"))
    try:
        status = OperationStatus(status_value)
    except ValueError as exc:
        raise ValueError(f"未知操作状态: {status_value}") from exc
    return Operation(
        operation_id=str(_require(record, "operation_id", "operation")),
        site_id=str(_require(record, "site_id", "operation")),
        kind=kind,
        starts_at=parse_ts(str(_require(record, "starts_at", "operation"))),
        duration_minutes=int(record.get("duration_minutes", 60)),
        staff=record.get("staff"),
        equipment=record.get("equipment"),
        required_qualification=record.get("required_qualification"),
        status=status,
        linked_operation_id=record.get("linked_operation_id"),
        result=record.get("result"),
        basis_snapshot_id=record.get("basis_snapshot_id"),
        raw=dict(record),
    )


def _parse_weather(record: dict) -> WeatherObs:
    return WeatherObs(
        weather_id=str(_require(record, "weather_id", "weather")),
        site_id=str(_require(record, "site_id", "weather")),
        night=str(_require(record, "night_date", "weather")),
        bucket=str(_require(record, "bucket", "weather")),
        condition=str(_require(record, "condition", "weather")),
        temp_c=float(_require(record, "temp_c", "weather")),
        wind_ms=float(_require(record, "wind_ms", "weather")),
        raw=dict(record),
    )


def _parse_activity(record: dict) -> ActivityVersion:
    effective_to = record.get("effective_to")
    return ActivityVersion(
        activity_id=str(_require(record, "activity_id", "activity")),
        site_id=str(_require(record, "site_id", "activity")),
        activity_type=str(_require(record, "activity_type", "activity")),
        version=str(_require(record, "version", "activity")),
        window_start=str(_require(record, "window_start", "activity")),
        window_end=str(_require(record, "window_end", "activity")),
        effective_from=parse_ts(str(_require(record, "effective_from", "activity"))),
        effective_to=None if effective_to is None else parse_ts(str(effective_to)),
        is_correction=bool(record.get("is_correction", False)),
        correction_reason=record.get("correction_reason"),
        measure=record.get("measure"),
        raw=dict(record),
    )


def _parse_site_version(record: dict) -> SiteVersion:
    effective_to = record.get("effective_to")
    return SiteVersion(
        version_id=str(_require(record, "version_id", "site_version")),
        site_id=str(_require(record, "site_id", "site_version")),
        boundary_version=str(_require(record, "boundary_version", "site_version")),
        catchment_zones=tuple(_require(record, "catchment_zones", "site_version")),
        effective_from=parse_ts(str(_require(record, "effective_from", "site_version"))),
        effective_to=None if effective_to is None else parse_ts(str(effective_to)),
        is_correction=bool(record.get("is_correction", False)),
        correction_reason=record.get("correction_reason"),
        raw=dict(record),
    )


def _parse_resource(record: dict) -> Resource:
    return Resource(
        resource_id=str(_require(record, "resource_id", "resource")),
        resource_type=str(_require(record, "resource_type", "resource")),
        qualifications=tuple(record.get("qualifications", ())),
        home_site=str(_require(record, "home_site", "resource")),
        raw=dict(record),
    )


def _parse_policy(record: dict) -> Policy:
    return Policy(
        policy_id=str(_require(record, "policy_id", "policy")),
        version=str(_require(record, "version", "policy")),
        threshold_db=int(_require(record, "threshold_db", "policy")),
        night_start=str(_require(record, "night_start", "policy")),
        night_end=str(_require(record, "night_end", "policy")),
        slot_minutes=int(_require(record, "slot_minutes", "policy")),
        cluster_window_minutes=int(_require(record, "cluster_window_minutes", "policy")),
        maturity_min_valid_ratio=float(_require(record, "maturity_min_valid_ratio", "policy")),
        risk_missing_weight=float(_require(record, "risk_missing_weight", "policy")),
        risk_partial_weight=float(_require(record, "risk_partial_weight", "policy")),
        risk_maintenance_weight=float(_require(record, "risk_maintenance_weight", "policy")),
        risk_offline_weight=float(_require(record, "risk_offline_weight", "policy")),
        patrol_duration_minutes=int(_require(record, "patrol_duration_minutes", "policy")),
        notice_duration_minutes=int(_require(record, "notice_duration_minutes", "policy")),
        notice_window_start=str(_require(record, "notice_window_start", "policy")),
        notice_window_end=str(_require(record, "notice_window_end", "policy")),
        band_high_upper=float(_require(record, "band_high_upper", "policy")),
        band_high_lower=float(_require(record, "band_high_lower", "policy")),
        band_moderate_upper=float(_require(record, "band_moderate_upper", "policy")),
        raw=dict(record),
    )


@dataclass(frozen=True)
class DomainData:
    """解析后的全量领域资料。"""

    readings: tuple[Reading, ...]
    complaints: tuple[Complaint, ...]
    operations: tuple[Operation, ...]
    weather: tuple[WeatherObs, ...]
    activities: tuple[ActivityVersion, ...]
    site_versions: tuple[SiteVersion, ...]
    resources: tuple[Resource, ...]
    sites: tuple[dict, ...]
    travel_times: dict
    policy: Policy
    source_info: dict
    record_hashes: dict[str, dict[str, str]]

    @property
    def site_ids(self) -> tuple[str, ...]:
        return tuple(s["site_id"] for s in self.sites)

    def readings_for(self, site_id: str) -> tuple[Reading, ...]:
        return tuple(r for r in self.readings if r.site_id == site_id)

    def weather_lookup(self) -> dict[tuple[str, str], WeatherObs]:
        return {(w.site_id, w.night): w for w in self.weather}


def _load_optional(bundle: dict, filename: str) -> list:
    if filename not in bundle:
        path = None
        for key in bundle:
            if key == filename:
                path = key
        return []
    value = bundle[filename]
    return value if isinstance(value, list) else []


def load_domain(root: str | Path) -> DomainData:
    """读取并校验资料包，返回结构化领域资料。"""
    base = Path(root)
    bundle = load_bundle(base)

    # 可选文件单独读取，保持与必需文件同样的 JSON 规则
    for name in OPTIONAL_FILES:
        if name not in bundle:
            path = base / name
            if path.is_file():
                with path.open(encoding="utf-8") as handle:
                    bundle[name] = json.load(handle)

    readings = tuple(_parse_reading(r) for r in bundle["sensors.json"])
    complaints = tuple(_parse_complaint(r) for r in bundle["complaints.json"])
    operations = tuple(_parse_operation(r) for r in bundle["operations.json"])
    weather = tuple(_parse_weather(r) for r in bundle.get("weather.json", []))
    activities = tuple(_parse_activity(r) for r in bundle.get("activities.json", []))
    site_versions = tuple(_parse_site_version(r)
                          for r in bundle.get("site_versions.json", []))
    resources = tuple(_parse_resource(r) for r in bundle.get("resources.json", []))
    policy = _parse_policy(bundle["policy.json"])
    travel_times = bundle.get("travel_times.json", {})
    sites = bundle["sites.json"]

    _check_references(readings, complaints, operations, sites)
    _check_unique_ids(readings, complaints, operations)

    record_hashes: dict[str, dict[str, str]] = {}
    for filename, rows in bundle.items():
        if isinstance(rows, list):
            record_hashes[filename] = {
                str(row.get(_id_field(filename), i)): canonical_hash(row)
                for i, row in enumerate(rows)
                if isinstance(row, dict)
            }
        elif isinstance(rows, dict):
            record_hashes[filename] = {"_document": canonical_hash(rows)}

    return DomainData(
        readings=readings,
        complaints=complaints,
        operations=operations,
        weather=weather,
        activities=activities,
        site_versions=site_versions,
        resources=resources,
        sites=tuple(sites),
        travel_times=travel_times,
        policy=policy,
        source_info=bundle["source_info.json"],
        record_hashes=record_hashes,
    )


def _id_field(filename: str) -> str:
    return {
        "sensors.json": "reading_id",
        "complaints.json": "complaint_id",
        "operations.json": "operation_id",
        "weather.json": "weather_id",
        "activities.json": "activity_id",
        "site_versions.json": "version_id",
        "resources.json": "resource_id",
    }.get(filename, "id")


def _check_references(readings, complaints, operations, sites) -> None:
    known_sites = {s["site_id"] for s in sites}
    for reading in readings:
        if reading.site_id not in known_sites:
            raise ValueError(f"读数 {reading.reading_id} 引用未知地点 {reading.site_id}")
    for complaint in complaints:
        if complaint.site_id not in known_sites:
            raise ValueError(f"投诉 {complaint.complaint_id} 引用未知地点 {complaint.site_id}")
    for operation in operations:
        if operation.site_id not in known_sites:
            raise ValueError(f"操作 {operation.operation_id} 引用未知地点 {operation.site_id}")


def _check_unique_ids(readings, complaints, operations) -> None:
    for items, label in ((readings, "reading"), (complaints, "complaint"),
                         (operations, "operation")):
        seen: set[str] = set()
        for item in items:
            ident = getattr(item, f"{label}_id")
            if ident in seen:
                raise ValueError(f"{label} 标识重复: {ident}")
            seen.add(ident)


def as_of_default() -> datetime:
    """资料包内最晚时间之后的默认评估时点。"""
    return datetime.fromisoformat("2026-10-08T12:00")
